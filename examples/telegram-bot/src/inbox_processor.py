import asyncio
import contextlib
import glob
import json
import logging
import os
import re
import shutil
import subprocess
import time
from urllib.parse import urlparse
import uuid

import yaml
from nexus.core.process_orchestrator import ProcessOrchestrator
from nexus.core.project.repo_utils import (
    iter_project_configs as _iter_project_configs,
)
from nexus.core.project.repo_utils import (
    project_repos_from_config as _project_repos_from_config,
)

# Nexus Core framework imports — orchestration handled by ProcessOrchestrator
# Import centralized configuration
from config import (
    BASE_DIR,
    INBOX_PROCESSOR_LOG_FILE,
    NEXUS_CORE_STORAGE_DIR,
    NEXUS_STATE_DIR,
    ORCHESTRATOR_CONFIG,
    PROJECT_CONFIG,
    SLEEP_INTERVAL,
    get_default_project,
    get_github_repo,
    get_github_repos,
    get_inbox_storage_backend,
    get_inbox_dir,
    get_nexus_dir_name,
    get_project_platform,
    get_tasks_active_dir,
    get_tasks_closed_dir,
)
from integrations.inbox_queue import claim_pending_tasks, mark_task_done, mark_task_failed
from integrations.notifications import (
    emit_alert,
    notify_agent_needs_input,
    notify_workflow_completed,
)
from orchestration.ai_orchestrator import get_orchestrator
from orchestration.nexus_core_helpers import (
    complete_step_for_issue,
    get_git_platform,
    get_workflow_definition_path,
    start_workflow,
)
from orchestration.plugin_runtime import (
    get_profiled_plugin,
    get_workflow_monitor_policy_plugin,
    get_workflow_policy_plugin,
    get_workflow_state_plugin,
)
from runtime.agent_launcher import (
    get_sop_tier_from_issue,
    invoke_copilot_agent,
    is_recent_launch,
)
from nexus.core.router import WorkflowRouter
from runtime.nexus_agent_runtime import NexusAgentRuntime
from services.workflow_signal_sync import (
    normalize_agent_reference as _normalize_agent_reference,
)
from state_manager import HostStateManager

_STEP_COMPLETE_COMMENT_RE = re.compile(
    r"^\s*##\s+.+?\bcomplete\b\s+—\s+([a-zA-Z0-9_-]+)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_READY_FOR_COMMENT_RE = re.compile(
    r"\bready\s+for\s+(?:\*\*)?`?@?([a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)

# Helper to get issue repo (currently defaults to nexus, should be extended for multi-project)
def get_issue_repo(project: str = "nexus") -> str:
    """Get the GitHub repo for issue operations.
    
    Args:
        project: Project name (currently unused, defaults to nexus)
        
    Returns:
        GitHub repo string
        
    Note: This should be extended to support per-project repos when multi-project
          issue tracking is implemented.
    """
    try:
        return get_github_repo(project)
    except Exception:
        return get_github_repo(get_default_project())

# Initialize orchestrator (CLI-only)
orchestrator = get_orchestrator(ORCHESTRATOR_CONFIG)

# Track alerted agents to avoid spam
alerted_agents = set()
notified_comments = set()  # Track comment IDs we've already notified about
auto_chained_agents = {}  # Track issue -> log_file to avoid re-chaining same completion
INBOX_PROCESSOR_STARTED_AT = time.time()
POLLING_FAILURE_THRESHOLD = 3
polling_failure_counts: dict[str, int] = {}
from integrations.workflow_state_factory import get_workflow_state as _get_wf_state

_WORKFLOW_STATE_PLUGIN_KWARGS = {
    "storage_dir": NEXUS_CORE_STORAGE_DIR,
    "issue_to_workflow_id": lambda n: _get_wf_state().get_workflow_id(n),
    "issue_to_workflow_map_setter": lambda n, w: _get_wf_state().map_issue(n, w),
    "workflow_definition_path_resolver": get_workflow_definition_path,
}


def _get_github_issue_plugin(repo: str, max_attempts: int = 3, timeout: int = 30):
    """Create a configured GitHub issue plugin instance for a repo."""
    return get_profiled_plugin(
        "github_inbox",
        overrides={
            "repo": repo,
            "max_attempts": max_attempts,
            "timeout": timeout,
        },
        cache_key=f"github:inbox:{repo}:{max_attempts}:{timeout}",
    )


def _resolve_tier_for_issue(
    issue_num: str,
    project_name: str,
    repo: str,
    *,
    context: str = "auto-chain",
) -> str | None:
    """Resolve workflow tier for an issue, halt with alert when unknown.

    Resolution order:
      1. ``launched_agents`` tracker (persisted from the most recent launch)
      2. Issue ``workflow:`` labels on GitHub
      3. ``None`` — caller must halt the flow

    When the tier is found from the tracker but the issue has no
    ``workflow:`` label, the label is added automatically so that
    future reads succeed.

    Returns:
        Tier name (``"full"``, ``"shortened"``, ``"fast-track"``) or
        ``None`` when the tier cannot be determined.  When ``None`` is
        returned, a Telegram alert has already been sent.
    """
    tracker_tier = HostStateManager.get_last_tier_for_issue(issue_num)
    label_tier = get_sop_tier_from_issue(issue_num, project_name, repo_override=repo)

    if tracker_tier and label_tier:
        # Both sources available — prefer label (canonical), but warn on mismatch
        if tracker_tier != label_tier:
            logger.warning(
                f"Tier mismatch for issue #{issue_num}: "
                f"tracker={tracker_tier}, label={label_tier}. Using label."
            )
        return label_tier

    if label_tier:
        return label_tier

    if tracker_tier:
        # Label missing — backfill it so future reads work
        _ensure_workflow_label(issue_num, tracker_tier, repo)
        return tracker_tier

    # Neither source available — halt
    logger.error(
        f"Cannot determine workflow tier for issue #{issue_num} "
        f"({context}): no tracker entry and no workflow: label."
    )
    emit_alert(
        f"⚠️ {context.title()} halted for issue #{issue_num}: "
        f"missing `workflow:` label and no prior launch data.\n"
        f"Add a label (e.g. `workflow:full`) to the issue and retry.",
        severity="warning", source="inbox_processor",
    )
    return None


def _ensure_workflow_label(issue_num: str, tier_name: str, repo: str) -> None:
    """Add `workflow:<tier>` label to an issue if missing."""
    label = f"workflow:{tier_name}"
    try:
        plugin = _get_github_issue_plugin(repo, max_attempts=2, timeout=10)
        plugin.add_label(str(issue_num), label)
        logger.info(f"Added missing label '{label}' to issue #{issue_num}")
    except Exception as e:
        logger.warning(f"Failed to add label '{label}' to issue #{issue_num}: {e}")


# Wrapper functions for backward compatibility - these now delegate to HostStateManager
def load_launched_agents():
    """Load recently launched agents from persistent storage."""
    return HostStateManager.load_launched_agents()

def save_launched_agents(data):
    """Save launched agents to persistent storage."""
    HostStateManager.save_launched_agents(data)

# Load persisted state
launched_agents_tracker = HostStateManager.load_launched_agents()
# PROJECT_CONFIG is now imported from config.py

# Failed task file lookup tracking (stop checking after 3 failures)
FAILED_LOOKUPS_FILE = os.path.join(NEXUS_STATE_DIR, "failed_task_lookups.json")
COMPLETION_COMMENTS_FILE = os.path.join(NEXUS_STATE_DIR, "completion_comments.json")

def load_failed_lookups():
    """Load failed task file lookup counters."""
    try:
        if os.path.exists(FAILED_LOOKUPS_FILE):
            with open(FAILED_LOOKUPS_FILE) as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Error loading failed lookups: {e}")
    return {}

def save_failed_lookups(lookups):
    """Save failed task file lookup counters."""
    try:
        with open(FAILED_LOOKUPS_FILE, 'w') as f:
            json.dump(lookups, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving failed lookups: {e}")


def load_completion_comments():
    """Load completion comment tracking data."""
    try:
        if os.path.exists(COMPLETION_COMMENTS_FILE):
            with open(COMPLETION_COMMENTS_FILE) as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to read completion comments file: {e}")
    return {}


def save_completion_comments(comments):
    """Save completion comment tracking data."""
    try:
        with open(COMPLETION_COMMENTS_FILE, "w") as f:
            json.dump(comments, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to save completion comments file: {e}")


def get_completion_replay_window_seconds() -> int:
    """Return startup replay window (seconds) for completion file scans.

    Completions older than this window at process startup are ignored to avoid
    replaying historical summaries when dedup state is reset.
    """
    raw = os.getenv("NEXUS_COMPLETION_REPLAY_WINDOW_SECONDS", "1800")
    try:
        value = int(str(raw).strip())
        return max(0, value)
    except Exception:
        return 1800

failed_task_lookups = load_failed_lookups()
completion_comments = load_completion_comments()

# Logging — force=True overrides the root handler set by config.py at import time
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    force=True,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(INBOX_PROCESSOR_LOG_FILE)
    ]
)
logger = logging.getLogger("InboxProcessor")


def _record_polling_failure(scope: str, error: Exception) -> None:
    """Increment polling failure count and alert once threshold is reached."""
    count = polling_failure_counts.get(scope, 0) + 1
    polling_failure_counts[scope] = count
    if count != POLLING_FAILURE_THRESHOLD:
        return

    try:
        emit_alert(
            "⚠️ **Polling Error Threshold Reached**\n\n"
            f"Scope: `{scope}`\n"
            f"Consecutive failures: {count}\n"
            f"Last error: `{error}`",
            severity="error", source="inbox_processor",
        )
    except Exception as notify_err:
        logger.error(f"Failed to send polling escalation alert for {scope}: {notify_err}")


def _clear_polling_failures(scope: str) -> None:
    """Reset polling failure count for a scope after a successful attempt."""
    if scope in polling_failure_counts:
        polling_failure_counts.pop(scope, None)


def slugify(text):
    """Converts text to a branch-friendly slug."""
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'\s+', '-', text)
    return text[:50]


def _resolve_project_from_path(summary_path: str) -> str:
    """Resolve project name from a completion_summary file path.

    Matches the path against configured project workspaces.
    Returns project key or empty string if no match.
    """
    for key, cfg in _iter_project_configs(PROJECT_CONFIG, get_github_repos):
        workspace = cfg.get("workspace")
        if not workspace:
            continue
        workspace_abs = os.path.join(BASE_DIR, workspace)
        if summary_path.startswith(workspace_abs):
            return key
    return ""


def _extract_repo_from_issue_url(issue_url: str) -> str:
    """Extract ``namespace/repo`` from GitHub or GitLab issue URL."""
    if not issue_url:
        return ""

    try:
        parsed = urlparse(issue_url.strip())
        parts = [segment for segment in parsed.path.strip("/").split("/") if segment]
        # GitHub: /owner/repo/issues/<num>
        if len(parts) >= 4 and parts[2].lower() == "issues":
            return f"{parts[0]}/{parts[1]}"
        # GitLab: /group/subgroup/repo/-/issues/<num>
        if "-" in parts:
            dash_idx = parts.index("-")
            if dash_idx >= 1 and len(parts) > dash_idx + 2 and parts[dash_idx + 1] == "issues":
                return "/".join(parts[:dash_idx])
    except Exception:
        return ""

    return ""


def _resolve_project_for_repo(repo_name: str) -> str | None:
    """Resolve configured project key for a repository full name."""
    for key, cfg in _iter_project_configs(PROJECT_CONFIG, get_github_repos):
        if repo_name in _project_repos_from_config(key, cfg, get_github_repos):
            return key
    return None


def _reroute_webhook_task_to_project(filepath: str, target_project: str) -> str | None:
    """Move a webhook task file to the target project's inbox directory."""
    project_cfg = PROJECT_CONFIG.get(target_project)
    if not isinstance(project_cfg, dict):
        return None

    workspace_rel = project_cfg.get("workspace")
    if not workspace_rel:
        return None

    workspace_abs = os.path.join(BASE_DIR, workspace_rel)
    inbox_dir = get_inbox_dir(workspace_abs, target_project)
    os.makedirs(inbox_dir, exist_ok=True)

    target_path = os.path.join(inbox_dir, os.path.basename(filepath))
    if os.path.abspath(target_path) == os.path.abspath(filepath):
        return target_path

    if os.path.exists(target_path):
        stem, ext = os.path.splitext(os.path.basename(filepath))
        target_path = os.path.join(inbox_dir, f"{stem}_{int(time.time())}{ext}")

    shutil.move(filepath, target_path)
    return target_path


def _resolve_repo_for_issue(issue_num: str, default_project: str | None = None) -> str:
    """Resolve the repository that owns an issue across all configured project repos."""
    default_repo = (
        get_github_repo(default_project)
        if default_project
        else get_github_repo(get_default_project())
    )

    repo_candidates: list[str] = []
    if default_project and default_project in PROJECT_CONFIG:
        repo_candidates.extend(
            _project_repos_from_config(default_project, PROJECT_CONFIG[default_project], get_github_repos)
        )
    if default_repo and default_repo not in repo_candidates:
        repo_candidates.append(default_repo)

    for project_key, cfg in _iter_project_configs(PROJECT_CONFIG, get_github_repos):
        for repo_name in _project_repos_from_config(project_key, cfg, get_github_repos):
            if repo_name not in repo_candidates:
                repo_candidates.append(repo_name)

    for repo_name in repo_candidates:
        matched_project = None
        for project_key, cfg in _iter_project_configs(PROJECT_CONFIG, get_github_repos):
            if repo_name in _project_repos_from_config(project_key, cfg, get_github_repos):
                matched_project = project_key
                break
        if not matched_project:
            matched_project = default_project or get_default_project()

        try:
            issue = asyncio.run(
                get_git_platform(repo_name, project_name=matched_project).get_issue(str(issue_num))
            )
        except Exception:
            continue
        if not issue:
            continue

        issue_url = getattr(issue, "url", "") or ""
        url_repo = _extract_repo_from_issue_url(issue_url)
        if url_repo:
            return url_repo

        body = getattr(issue, "body", "") or ""
        task_file_match = re.search(r"\*\*Task File:\*\*\s*`([^`]+)`", body)
        if task_file_match:
            task_file = task_file_match.group(1)
            for project_key, cfg in _iter_project_configs(PROJECT_CONFIG, get_github_repos):
                workspace = cfg.get("workspace")
                if not workspace:
                    continue
                workspace_abs = os.path.join(BASE_DIR, workspace)
                if task_file.startswith(workspace_abs):
                    project_repos = _project_repos_from_config(project_key, cfg, get_github_repos)
                    if repo_name in project_repos:
                        return repo_name

        return repo_name

    return default_repo


def _resolve_repo_strict(project_name: str, issue_num: str) -> str:
    """Resolve repo with boundary checks between project and issue context."""
    project_repos: list[str] = []
    if project_name and project_name in PROJECT_CONFIG:
        project_repos = _project_repos_from_config(
            project_name,
            PROJECT_CONFIG[project_name],
            get_github_repos,
        )

    issue_repo = _resolve_repo_for_issue(
        issue_num,
        default_project=project_name or get_default_project(),
    )
    if project_repos and issue_repo and issue_repo not in project_repos:
        message = (
            f"🚫 Project boundary mismatch for issue #{issue_num}: "
            f"project '{project_name}' repos {project_repos}, issue context -> {issue_repo}. "
            "Workflow finalization blocked."
        )
        logger.error(message)
        emit_alert(message, severity="error", source="inbox_processor")
        raise ValueError(message)

    return issue_repo or (project_repos[0] if project_repos else get_github_repo(get_default_project()))


def _read_latest_local_completion(issue_num: str) -> dict | None:
    """Return latest local completion summary for issue, if present."""
    nexus_dir_name = get_nexus_dir_name()
    pattern = os.path.join(
        BASE_DIR,
        "**",
        nexus_dir_name,
        "tasks",
        "*",
        "completions",
        f"completion_summary_{issue_num}.json",
    )
    matches = glob.glob(pattern, recursive=True)
    if not matches:
        return None

    latest = max(matches, key=os.path.getmtime)
    try:
        with open(latest, encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return None

    return {
        "file": latest,
        "mtime": os.path.getmtime(latest),
        "agent_type": _normalize_agent_reference(str(payload.get("agent_type", ""))).lower(),
        "next_agent": _normalize_agent_reference(str(payload.get("next_agent", ""))).lower(),
    }


def _read_latest_structured_comment(issue_num: str, repo: str, project_name: str) -> dict | None:
    """Return latest structured (non-automated) agent comment signal from GitHub."""
    try:
        platform = get_git_platform(repo, project_name=project_name)
        comments = asyncio.run(platform.get_comments(str(issue_num)))
    except Exception as exc:
        logger.debug(f"Startup drift check skipped for issue #{issue_num}: {exc}")
        return None

    for comment in reversed(comments or []):
        body = str(getattr(comment, "body", "") or "")
        if "_Automated comment from Nexus._" in body:
            continue

        complete_match = _STEP_COMPLETE_COMMENT_RE.search(body)
        next_match = _READY_FOR_COMMENT_RE.search(body)
        if not (complete_match and next_match):
            continue

        return {
            "comment_id": getattr(comment, "id", None),
            "created_at": str(getattr(comment, "created_at", "") or ""),
            "completed_agent": _normalize_agent_reference(complete_match.group(1)).lower(),
            "next_agent": _normalize_agent_reference(next_match.group(1)).lower(),
        }

    return None


def reconcile_completion_signals_on_startup() -> None:
    """Audit workflow/comment/local completion alignment and alert on drift.

    Safe startup check only: emits alerts when signals diverge, does not mutate
    workflow state or completion files.
    """
    mappings = _get_wf_state().load_all_mappings()
    if not mappings:
        return

    for issue_num, workflow_id in mappings.items():
        wf_file = os.path.join(NEXUS_CORE_STORAGE_DIR, "workflows", f"{workflow_id}.json")
        try:
            with open(wf_file, encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            continue

        state = str(payload.get("state", "")).strip().lower()
        if state not in {"running", "paused"}:
            continue

        expected_running_agent = ""
        for step in payload.get("steps", []):
            if not isinstance(step, dict):
                continue
            if str(step.get("status", "")).strip().lower() != "running":
                continue
            agent = step.get("agent")
            if not isinstance(agent, dict):
                continue
            expected_running_agent = _normalize_agent_reference(
                str(agent.get("name") or agent.get("display_name") or "")
            ).lower()
            if expected_running_agent:
                break

        if not expected_running_agent:
            continue

        metadata = payload.get("metadata", {}) if isinstance(payload.get("metadata"), dict) else {}
        issue_url = str(metadata.get("github_issue_url", "") or "")
        repo = _extract_repo_from_issue_url(issue_url)
        project_name = str(metadata.get("project_name", "") or "")

        local_signal = _read_latest_local_completion(str(issue_num))
        comment_signal = (
            _read_latest_structured_comment(str(issue_num), repo, project_name)
            if repo
            else None
        )

        drifts = []
        local_next = (local_signal or {}).get("next_agent", "")
        comment_next = (comment_signal or {}).get("next_agent", "")

        # Safe auto-reconcile path:
        # if latest structured comment is from the currently RUNNING agent and
        # points to a non-terminal next step, trust it and advance workflow.
        comment_completed = (comment_signal or {}).get("completed_agent", "")
        if (
            comment_signal
            and comment_completed
            and comment_completed == expected_running_agent
            and comment_next
            and not _is_terminal_agent_reference(comment_next)
        ):
            try:
                outputs = {
                    "status": "complete",
                    "agent_type": comment_completed,
                    "next_agent": comment_next,
                    "summary": (
                        f"Auto-reconciled on startup from comment "
                        f"{comment_signal.get('comment_id', 'n/a')}"
                    ),
                    "source": "startup-auto-reconcile",
                }
                asyncio.run(
                    complete_step_for_issue(
                        str(issue_num),
                        comment_completed,
                        outputs,
                        event_id=f"startup:{comment_signal.get('comment_id', 'n/a')}",
                    )
                )
                if local_signal and local_signal.get("file"):
                    try:
                        with open(local_signal["file"], "w", encoding="utf-8") as handle:
                            json.dump(
                                {
                                    "status": "complete",
                                    "agent_type": comment_completed,
                                    "summary": outputs["summary"],
                                    "key_findings": [
                                        "Startup auto-reconciled from structured comment"
                                    ],
                                    "next_agent": comment_next,
                                },
                                handle,
                                indent=2,
                            )
                    except Exception as file_exc:
                        logger.debug(
                            "Startup auto-reconcile could not rewrite local completion for issue #%s: %s",
                            issue_num,
                            file_exc,
                        )
                logger.info(
                    "Startup auto-reconciled issue #%s: %s -> %s",
                    issue_num,
                    comment_completed,
                    comment_next,
                )
                continue
            except Exception as exc:
                logger.debug(
                    "Startup auto-reconcile skipped for issue #%s due to error: %s",
                    issue_num,
                    exc,
                )

        if local_next and local_next != expected_running_agent:
            drifts.append(f"local next={local_next}")
        if comment_next and comment_next != expected_running_agent:
            drifts.append(f"comment next={comment_next}")
        if local_next and comment_next and local_next != comment_next:
            drifts.append("local/comment disagree")

        if not drifts:
            continue

        details = (
            f"expected RUNNING={expected_running_agent}; "
            f"local={local_next or 'n/a'}; "
            f"comment={comment_next or 'n/a'}"
        )
        logger.warning(
            f"Startup signal drift for issue #{issue_num} ({', '.join(drifts)}): {details}"
        )
        emit_alert(
            f"⚠️ Startup routing drift detected for issue #{issue_num}\n"
            f"Workflow RUNNING: `{expected_running_agent}`\n"
            f"Local completion next: `{local_next or 'n/a'}`\n"
            f"Latest structured comment next: `{comment_next or 'n/a'}`\n\n"
            "No automatic state changes were made. Reconcile manually before /continue.",
            severity="warning", source="inbox_processor",
        )


def _is_terminal_agent_reference(agent_ref: str) -> bool:
    """Return True when a next-agent reference means workflow completion."""
    return _normalize_agent_reference(agent_ref).lower() in {
        "none", "n/a", "null", "no", "end", "done", "finish", "complete", ""
    }


def _resolve_git_dir(project_name: str) -> str | None:
    """Resolve the actual git repo directory for a project.

    Tries:
    1. workspace itself (e.g. /opt/git/<workspace>)
    2. workspace/repo_name (e.g. /opt/git/<org>/<repo>)

    Returns absolute path or None.
    """
    proj_cfg = PROJECT_CONFIG.get(project_name, {})
    workspace = proj_cfg.get("workspace", "")
    configured_repo = proj_cfg.get("git_repo", "")
    if not workspace:
        return None
    workspace_abs = os.path.join(BASE_DIR, workspace)

    if os.path.isdir(os.path.join(workspace_abs, ".git")):
        return workspace_abs
    if configured_repo and "/" in configured_repo:
        repo_name = configured_repo.split("/")[-1]
        candidate = os.path.join(workspace_abs, repo_name)
        if os.path.isdir(os.path.join(candidate, ".git")):
            return candidate
    return None


def _resolve_git_dir_for_repo(project_name: str, repo_name: str) -> str | None:
    """Resolve git directory for a specific configured repo."""
    proj_cfg = PROJECT_CONFIG.get(project_name, {})
    workspace = proj_cfg.get("workspace", "") if isinstance(proj_cfg, dict) else ""
    if not workspace:
        return None

    workspace_abs = os.path.join(BASE_DIR, workspace)
    target_repo = str(repo_name or "").strip()
    target_basename = target_repo.split("/")[-1] if "/" in target_repo else target_repo

    if os.path.isdir(os.path.join(workspace_abs, ".git")):
        if os.path.basename(workspace_abs.rstrip(os.sep)) == target_basename:
            return workspace_abs

    candidate = os.path.join(workspace_abs, target_basename)
    if os.path.isdir(os.path.join(candidate, ".git")):
        return candidate

    return None


def _resolve_git_dirs(project_name: str) -> dict[str, str]:
    """Return repo -> git_dir map for repos that currently have a checkout on disk."""
    resolved: dict[str, str] = {}
    try:
        repo_names = get_github_repos(project_name)
    except Exception:
        repo_names = []

    for repo_name in repo_names:
        repo_key = str(repo_name or "").strip()
        if not repo_key:
            continue
        git_dir = _resolve_git_dir_for_repo(project_name, repo_key)
        if git_dir:
            resolved[repo_key] = git_dir

    return resolved


def _finalize_workflow(issue_num: str, repo: str, last_agent: str, project_name: str) -> None:
    """Handle workflow completion: close issue, create PR if needed, send Telegram.

    Called when the last agent finishes (next_agent is 'none' or empty).
    Delegates PR creation and issue closing to nexus-core GitHubPlatform.
    """
    try:
        workflow_plugin = get_workflow_state_plugin(
            **_WORKFLOW_STATE_PLUGIN_KWARGS,
            cache_key="workflow:state-engine",
        )
        if workflow_plugin and hasattr(workflow_plugin, "get_workflow_status"):
            status = asyncio.run(workflow_plugin.get_workflow_status(str(issue_num)))
            state = str((status or {}).get("state", "")).strip().lower()
            if state and state not in {"completed", "failed", "cancelled"}:
                logger.warning(
                    "Skipping finalize for issue #%s: workflow state is non-terminal (%s)",
                    issue_num,
                    state,
                )
                emit_alert(
                    "⚠️ Finalization blocked for "
                    f"issue #{issue_num}: workflow state is `{state}` (expected terminal).",
                    severity="warning", source="inbox_processor",
                )
                return
    except Exception as exc:
        logger.warning(
            "Could not verify workflow state before finalize for issue #%s: %s",
            issue_num,
            exc,
        )

    def _create_pr_from_changes(**kwargs):
        platform = get_git_platform(kwargs["repo"], project_name=project_name)
        pr_result = asyncio.run(
            platform.create_pr_from_changes(
                repo_dir=kwargs["repo_dir"],
                issue_number=kwargs["issue_number"],
                title=kwargs["title"],
                body=kwargs["body"],
                issue_repo=kwargs.get("issue_repo"),
            )
        )
        return pr_result.url if pr_result else None

    def _close_issue(**kwargs):
        platform = get_git_platform(kwargs["repo"], project_name=project_name)
        return bool(asyncio.run(platform.close_issue(kwargs["issue_number"], comment=kwargs["comment"])))

    def _find_existing_pr(**kwargs):
        platform = get_git_platform(kwargs["repo"], project_name=project_name)
        issue_number = str(kwargs["issue_number"])
        linked = asyncio.run(platform.search_linked_prs(issue_number))
        if not linked:
            return None

        open_pr = next((pr for pr in linked if str(pr.state).lower() == "open"), None)
        selected = open_pr or linked[0]
        return selected.url

    workflow_policy = get_workflow_policy_plugin(
        resolve_git_dir=_resolve_git_dir,
        resolve_git_dirs=_resolve_git_dirs,
        create_pr_from_changes=_create_pr_from_changes,
        find_existing_pr=_find_existing_pr,
        close_issue=_close_issue,
        send_notification=lambda msg: emit_alert(msg, severity="info", source="workflow_policy"),
        cache_key="workflow-policy:finalize",
    )

    result = workflow_policy.finalize_workflow(
        issue_number=str(issue_num),
        repo=repo,
        last_agent=last_agent,
        project_name=project_name,
    )

    pr_urls = result.get("pr_urls") if isinstance(result, dict) else None
    if isinstance(pr_urls, list) and pr_urls:
        for pr_link in pr_urls:
            logger.info(f"🔀 Created/linked PR for issue #{issue_num}: {pr_link}")
    if result.get("issue_closed"):
        logger.info(f"🔒 Closed issue #{issue_num}")
        archived = _archive_closed_task_files(issue_num, project_name)
        if archived:
            logger.info(f"📦 Archived {archived} task file(s) for closed issue #{issue_num}")


def _archive_closed_task_files(issue_num: str, project_name: str = "") -> int:
    """Archive active task files for a closed issue into tasks/closed.

    Matches files by either:
    1) filename pattern ``issue_<issue_num>.md``
    2) issue URL metadata inside the file body
    """
    projects_to_scan = []
    if project_name and project_name in PROJECT_CONFIG:
        projects_to_scan.append(project_name)

    projects_to_scan.extend(
        key for key in PROJECT_CONFIG
        if key not in {"workflow_definition_path", "shared_agents_dir", "nexus_dir", "require_human_merge_approval", "github_issue_triage", "ai_tool_preferences"}
        and key not in projects_to_scan
    )

    archived_count = 0
    issue_pattern = re.compile(r"\*\*Issue:\*\*\s*https?://github\.com/[^/]+/[^/]+/issues/(\d+)")

    for project_key in projects_to_scan:
        project_cfg = PROJECT_CONFIG.get(project_key, {})
        if not isinstance(project_cfg, dict):
            continue

        workspace_rel = project_cfg.get("workspace")
        if not workspace_rel:
            continue

        project_root = os.path.join(BASE_DIR, workspace_rel)
        active_dir = get_tasks_active_dir(project_root, project_key)
        if not os.path.isdir(active_dir):
            continue

        closed_dir = get_tasks_closed_dir(project_root, project_key)

        for filename in os.listdir(active_dir):
            if not filename.endswith(".md"):
                continue

            source_path = os.path.join(active_dir, filename)
            matched = False

            if filename == f"issue_{issue_num}.md":
                matched = True
            else:
                try:
                    with open(source_path) as f:
                        content = f.read()
                    match = issue_pattern.search(content)
                    matched = bool(match and match.group(1) == str(issue_num))
                except Exception as exc:
                    logger.warning(f"Could not inspect active task file {source_path}: {exc}")
                    continue

            if not matched:
                continue

            target_path = os.path.join(closed_dir, filename)
            if os.path.exists(target_path):
                stem, ext = os.path.splitext(filename)
                target_path = os.path.join(closed_dir, f"{stem}_{int(time.time())}{ext}")

            try:
                os.makedirs(closed_dir, exist_ok=True)
                os.replace(source_path, target_path)
                archived_count += 1
            except Exception as exc:
                logger.warning(f"Failed to archive task file {source_path}: {exc}")

    return archived_count


# ---------------------------------------------------------------------------
# ProcessOrchestrator singleton (Phase 3)
# ---------------------------------------------------------------------------

_process_orchestrator: ProcessOrchestrator | None = None


def _get_process_orchestrator() -> ProcessOrchestrator:
    """Build (or return the cached) ProcessOrchestrator for this session."""
    global _process_orchestrator
    if _process_orchestrator is not None:
        return _process_orchestrator

    runtime = NexusAgentRuntime(
        finalize_fn=_finalize_workflow,
        resolve_project=_resolve_project_from_path,
        resolve_repo=lambda proj, issue: _resolve_repo_strict(proj, issue),
    )
    _process_orchestrator = ProcessOrchestrator(
        runtime=runtime,
        complete_step_fn=complete_step_for_issue,
        nexus_dir=get_nexus_dir_name(),
    )
    return _process_orchestrator


def _post_completion_comments_from_logs() -> None:
    """Detect agent completions and auto-chain to the next workflow step.

    Delegates to :class:`ProcessOrchestrator` from nexus-core.
    """
    orc = _get_process_orchestrator()
    wfp = get_workflow_policy_plugin(cache_key="workflow-policy:inbox")

    dedup = set(completion_comments.keys())
    replay_window_seconds = get_completion_replay_window_seconds()
    replay_ref_ts = INBOX_PROCESSOR_STARTED_AT
    orc.scan_and_process_completions(
        BASE_DIR,
        dedup,
        resolve_project=_resolve_project_from_path,
        resolve_repo=lambda proj, issue: _resolve_repo_strict(proj, issue),
        build_transition_message=lambda **kw: wfp.build_transition_message(**kw),
        build_autochain_failed_message=lambda **kw: wfp.build_autochain_failed_message(**kw),
        stale_completion_seconds=(
            replay_window_seconds if replay_window_seconds > 0 else None
        ),
        stale_reference_ts=replay_ref_ts,
    )

    # Sync newly-seen dedup keys back to the persistent dict.
    now = time.time()
    for key in dedup:
        if key not in completion_comments:
            completion_comments[key] = now
    save_completion_comments(completion_comments)


def _get_initial_agent_from_workflow(project_name: str, workflow_type: str = "") -> str:
    """Get the first agent/agent_type from a workflow YAML definition.

    Delegates to nexus-core's WorkflowDefinition.from_yaml() to parse the
    workflow, then reads the first step's agent name.

    Args:
        project_name: Project name to resolve workflow path.
        workflow_type: Tier name (full/shortened/fast-track) for multi-tier workflows.

    Returns empty string if workflow definition is missing or invalid.
    """
    from nexus.core.workflow import WorkflowDefinition

    path = get_workflow_definition_path(project_name)
    if not path:
        logger.error(f"Missing workflow_definition_path for project '{project_name}'")
        emit_alert(
            f"Missing workflow_definition_path for project '{project_name}'.",
            severity="error", source="inbox_processor",
        )
        return ""
    if not os.path.exists(path):
        logger.error(f"Workflow definition not found: {path}")
        emit_alert(f"Workflow definition not found: {path}", severity="error", source="inbox_processor")
        return ""
    try:
        workflow = WorkflowDefinition.from_yaml(path, workflow_type=workflow_type)
        if not workflow.steps:
            logger.error(f"Workflow definition has no steps: {path}")
            emit_alert(f"Workflow definition has no steps: {path}", severity="error", source="inbox_processor")
            return ""
        first_step = workflow.steps[0]
        return first_step.agent.name or first_step.agent.display_name or ""
    except Exception as e:
        logger.error(f"Failed to read workflow definition {path}: {e}")
        emit_alert(f"Failed to read workflow definition {path}: {e}", severity="error", source="inbox_processor")
        return ""





def check_stuck_agents():
    """Monitor agent processes and handle timeouts with auto-kill and retry.

    Delegates to :class:`ProcessOrchestrator` which implements both
    strategy-1 (stale-log timeout kill) and strategy-2 (dead-process detection).
    """
    scope = "stuck-agents:loop"
    try:
        _get_process_orchestrator().check_stuck_agents(BASE_DIR)
        _clear_polling_failures(scope)
    except Exception as e:
        logger.error(f"Error in check_stuck_agents: {e}")
        _record_polling_failure(scope, e)


def _resolve_project_for_issue(issue_num: str) -> str | None:
    """Best-effort project resolution from config for an issue number."""
    # Try to find which project this issue belongs to by checking agents data
    for project_name, _ in _iter_project_configs(PROJECT_CONFIG, get_github_repos):
        return project_name
    return None


def check_agent_comments():
    """Monitor GitHub issues for agent comments requesting input across all projects."""
    loop_scope = "agent-comments:loop"
    try:
        # Query issues from all project repos
        all_issue_nums = []
        for project_name, _ in _iter_project_configs(PROJECT_CONFIG, get_github_repos):
            project_platform = (get_project_platform(project_name) or "github").lower().strip()
            if project_platform != "github":
                logger.debug(
                    f"Skipping GitHub issue polling for non-GitHub project "
                    f"{project_name} (platform={project_platform})"
                )
                continue

            repo = get_github_repo(project_name)
            list_scope = f"agent-comments:list-issues:{project_name}"
            try:
                monitor_policy = get_workflow_monitor_policy_plugin(
                    list_open_issues=lambda **kwargs: asyncio.run(
                        get_git_platform(kwargs["repo"], project_name=project_name).list_open_issues(
                            limit=kwargs["limit"],
                            labels=sorted(kwargs["workflow_labels"]),
                        )
                    ),
                    cache_key=None,
                )
                workflow_labels = {
                    "workflow:full",
                    "workflow:shortened",
                    "workflow:fast-track",
                }
                issue_numbers = monitor_policy.list_workflow_issue_numbers(
                    repo=repo,
                    workflow_labels=workflow_labels,
                    limit=100,
                )
                for issue_number in issue_numbers:
                    all_issue_nums.append((issue_number, project_name, repo))
                _clear_polling_failures(list_scope)
            except Exception as e:
                logger.warning(f"Issue list failed for project {project_name}: {e}")
                _record_polling_failure(list_scope, e)
                continue
        
        if not all_issue_nums:
            return
        
        for issue_num, project_name, repo in all_issue_nums:
            if not issue_num:
                continue
                
            # Get issue comments via framework
            comments_scope = f"agent-comments:get-comments:{project_name}"
            try:
                monitor_policy = get_workflow_monitor_policy_plugin(
                    get_comments=lambda **kwargs: asyncio.run(
                        get_git_platform(kwargs["repo"], project_name=project_name).get_comments(str(kwargs["issue_number"]))
                    ),
                    cache_key=None,
                )
                bot_comments = monitor_policy.get_bot_comments(
                    repo=repo,
                    issue_number=str(issue_num),
                    bot_author="Ghabs95",
                )
                _clear_polling_failures(comments_scope)
            except Exception as e:
                logger.warning(f"Failed to fetch comments for issue #{issue_num}: {e}")
                _record_polling_failure(comments_scope, e)
                continue

            if not bot_comments:
                continue
            
            for comment in bot_comments:
                try:
                    comment_id = comment.id
                    body = comment.body or ""
                    
                    # Skip if we've already notified about this comment
                    if comment_id in notified_comments:
                        continue
                    
                    # Check if comment contains questions or blockers
                    needs_input = any(pattern in body.lower() for pattern in [
                        "questions for @ghabs",
                        "questions for `@ghabs",  # Escaped mention
                        "waiting for @ghabs",
                        "waiting for `@ghabs",  # Escaped mention
                        "need your input",
                        "please provide",
                        "owner:** @ghabs",
                        "owner:** `@ghabs",  # Escaped mention
                        "blocker:",
                        "your input to proceed"
                    ])
                    
                    if needs_input:
                        # Extract preview (first 200 chars)
                        preview = body[:200] + "..." if len(body) > 200 else body
                        
                        if notify_agent_needs_input(issue_num, "agent", preview, project=project_name):
                            logger.info(f"📨 Sent input request alert for issue #{issue_num}")
                            notified_comments.add(comment_id)
                        else:
                            logger.warning(f"Failed to send input alert for issue #{issue_num}")
                
                except Exception as e:
                    logger.error(f"Error processing comment for issue #{issue_num}: {e}")

        _clear_polling_failures(loop_scope)
                    
    except Exception as e:
        logger.error(f"Error in check_agent_comments: {e}")
        _record_polling_failure(loop_scope, e)


def check_and_notify_pr(issue_num, project):
    """
    Check if there's a PR linked to the issue and notify user for review.

    Delegates to nexus-core's GitHubPlatform.search_linked_prs().

    Args:
        issue_num: GitHub issue number
        project: Project name
    """
    try:
        repo = get_github_repo(project)
        monitor_policy = get_workflow_monitor_policy_plugin(
            search_linked_prs=lambda **kwargs: asyncio.run(
                get_git_platform(kwargs["repo"], project_name=project).search_linked_prs(str(kwargs["issue_number"]))
            ),
            cache_key=None,
        )
        pr = monitor_policy.find_open_linked_pr(repo=repo, issue_number=str(issue_num))
        if pr:
            logger.info(f"✅ Found PR #{pr.number} for issue #{issue_num}")
            notify_workflow_completed(
                issue_num, project, pr_urls=[pr.url],
            )
            return

        logger.info(f"ℹ️ No open PR found for issue #{issue_num}")
        notify_workflow_completed(issue_num, project)
    
    except Exception as e:
        logger.error(f"Error checking for PR: {e}")
        # Still notify even if PR check fails
        notify_workflow_completed(issue_num, project)


def check_completed_agents():
    """Monitor for completed agent steps and auto-chain to next agent.

    Delegates to _post_completion_comments_from_logs()
    which uses the nexus-core framework for completion scanning and auto-chaining.
    """
    _post_completion_comments_from_logs()


def _render_checklist_from_workflow(project_name: str, tier_name: str) -> str:
    """Render checklist directly from workflow YAML step definitions.

    Returns empty string when the workflow file cannot be read/resolved.
    """
    from nexus.core.workflow import WorkflowDefinition

    workflow_path = get_workflow_definition_path(project_name)
    if not workflow_path or not os.path.exists(workflow_path):
        return ""

    try:
        with open(workflow_path, encoding="utf-8") as handle:
            definition = yaml.safe_load(handle)
    except Exception:
        return ""

    workflow_type = WorkflowDefinition.normalize_workflow_type(
        tier_name,
        default=str(tier_name or "shortened"),
    )
    steps = WorkflowDefinition._resolve_steps(definition, workflow_type)
    if not steps:
        return ""

    title_by_tier = {
        "full": "Full Flow",
        "shortened": "Shortened Flow",
        "fast-track": "Fast-Track",
    }
    title = title_by_tier.get(workflow_type, str(workflow_type).replace("_", " ").title())
    lines = [f"## SOP Checklist — {title}"]

    rendered_index = 1
    for step in steps:
        if not isinstance(step, dict):
            continue
        if step.get("agent_type") == "router":
            continue

        step_name = str(step.get("name") or step.get("id") or f"Step {rendered_index}").strip()
        step_desc = str(step.get("description") or "").strip()

        if step_desc:
            lines.append(f"- [ ] {rendered_index}. **{step_name}** — {step_desc}")
        else:
            lines.append(f"- [ ] {rendered_index}. **{step_name}**")
        rendered_index += 1

    return "\n".join(lines) if rendered_index > 1 else ""


def _render_fallback_checklist(tier_name: str) -> str:
    """Render minimal fallback checklist when workflow YAML cannot be resolved."""
    heading_map = {
        "full": "Full Flow",
        "shortened": "Shortened Flow",
        "fast-track": "Fast-Track",
    }
    heading = heading_map.get(str(tier_name), str(tier_name).replace("_", " ").title())
    return (
        f"## SOP Checklist — {heading}\n"
        "- [ ] 1. **Implementation** — Complete required workflow steps\n"
        "- [ ] 2. **Verification** — Validate results\n"
        "- [ ] 3. **Documentation** — Record outcome"
    )


def get_sop_tier(task_type, title=None, body=None):
    """Returns (tier_name, sop_template, workflow_label) based on task type AND content.
    
    Now integrates WorkflowRouter for intelligent routing based on issue content.
    
    Workflow mapping:
    - hotfix, chore, feature-simple, improvement-simple → fast-track:
        Triage → Develop → Review → Deploy
    - bug → shortened:
        Triage → Debug → Develop → Review → Deploy → Close
    - feature, improvement, release → full:
        Triage → Design → Develop → Review → Compliance → Deploy → Close
    """
    # Try intelligent routing if title and body provided
    if title or body:
        try:
            suggested_label = WorkflowRouter.suggest_tier_label(title or "", body or "")
            if suggested_label:
                logger.info(f"🤖 WorkflowRouter suggestion: {suggested_label}")
                if "fast-track" in suggested_label:
                    return "fast-track", "", "workflow:fast-track"
                elif "shortened" in suggested_label:
                    return "shortened", "", "workflow:shortened"
                elif "full" in suggested_label:
                    return "full", "", "workflow:full"
        except Exception as e:
            logger.warning(f"WorkflowRouter suggestion failed: {e}, falling back to task_type")
    
    # Fallback: Original task_type-based routing
    if any(t in task_type for t in ["hotfix", "chore", "simple"]):
        return "fast-track", "", "workflow:fast-track"
    elif "bug" in task_type:
        return "shortened", "", "workflow:shortened"
    else:
        return "full", "", "workflow:full"


def create_github_issue(title, body, project, workflow_label, task_type, tier_name, github_repo):
    """Creates a GitHub Issue in the specified repo with SOP checklist."""
    type_label = f"type:{task_type}"
    project_label = f"project:{project}"
    labels = [project_label, type_label, workflow_label]

    creator = _get_github_issue_plugin(github_repo, max_attempts=3, timeout=30)

    try:
        issue_url = creator.create_issue(
            title=title,
            body=body,
            labels=labels,
        )
        if issue_url:
            issue_num = issue_url.rstrip("/").split("/")[-1]
            for label in labels:
                if label.startswith("workflow:"):
                    color = "0E8A16"
                    description = "Workflow tier"
                elif label.startswith("type:"):
                    color = "1D76DB"
                    description = "Task type"
                else:
                    color = "5319E7"
                    description = "Project key"

                with contextlib.suppress(Exception):
                    creator.ensure_label(label, color, description)
                creator.add_label(issue_num, label)

            logger.info("📋 Issue created via plugin")
            return issue_url

        raise RuntimeError("GitHub issue plugin returned no issue URL")
    except Exception as e:
        raise RuntimeError(f"GitHub issue plugin create failed: {e}") from e


def generate_issue_name(content, project_name):
    """Generate a concise task name using orchestrator (CLI only).
    
    Returns a slugified name in format: "this-is-the-task-name"
    Falls back to slugified content if AI tools are unavailable.
    """
    try:
        logger.info("Generating concise task name with orchestrator...")
        result = orchestrator.run_text_to_speech_analysis(
            text=content[:500],
            task="generate_name",
            project_name=project_name
        )

        suggested_name = result.get("text", "").strip().strip('"`\'').strip()
        slug = slugify(suggested_name)

        if slug:
            logger.info(f"✨ Orchestrator suggested: {slug}")
            return slug
        
        raise ValueError("Empty slug from orchestrator")

    except Exception as e:
        logger.warning(f"Name generation failed: {e}, using fallback")
        body = re.sub(r'^#.*\n', '', content)
        body = re.sub(r'\*\*.*\*\*.*\n', '', body)
        return slugify(body.strip()) or "generic-task"


def _extract_inline_task_name(content: str) -> str:
    if not isinstance(content, str) or not content:
        return ""
    for line in content.splitlines():
        stripped = str(line or "").strip()
        if not stripped.startswith("**Task Name:**"):
            continue
        candidate = stripped.split("**Task Name:**", 1)[1].strip()
        return candidate
    return ""



def process_file(filepath):
    """Processes a single task file."""
    logger.info(f"Processing: {filepath}")

    try:
        with open(filepath) as f:
            content = f.read()

        # Parse Metadata
        type_match = re.search(r'\*\*Type:\*\*\s*(.+)', content)
        task_type = type_match.group(1).strip().lower() if type_match else "feature"

        # Determine project from filepath
        # filepath is .../workspace/.nexus/inbox/<project>/file.md
        nexus_dir_name = get_nexus_dir_name()
        marker = f"{os.sep}{nexus_dir_name}{os.sep}inbox{os.sep}"
        project_name = None
        config = None
        project_root = None

        if marker in filepath:
            prefix, suffix = filepath.split(marker, 1)
            project_name = suffix.split(os.sep, 1)[0] if suffix else None
            project_root = prefix

        if project_name and project_name in PROJECT_CONFIG:
            cfg = PROJECT_CONFIG.get(project_name)
            if isinstance(cfg, dict):
                config = cfg

        # Fallback: look up project by matching workspace path
        if not config:
            for key, cfg in _iter_project_configs(PROJECT_CONFIG, get_github_repos):
                workspace = cfg.get("workspace")
                if not workspace:
                    continue
                workspace_abs = os.path.join(BASE_DIR, workspace)
                if filepath.startswith(workspace_abs + os.sep):
                    project_name = key
                    config = cfg
                    project_root = workspace_abs
                    break
        
        if not config:
            logger.warning(f"⚠️ No project config for workspace '{project_root}', skipping.")
            return

        logger.info(f"Project: {project_name}")

        # Check if this task came from webhook (already has GitHub issue)
        source_match = re.search(r'\*\*Source:\*\*\s*(.+)', content)
        source = source_match.group(1).strip().lower() if source_match else None
        
        # Extract issue number and URL if from webhook
        issue_num_match = re.search(r'\*\*Issue Number:\*\*\s*(.+)', content)
        issue_url_match = re.search(r'\*\*URL:\*\*\s*(.+)', content)
        agent_type_match = re.search(r'\*\*Agent Type:\*\*\s*(.+)', content)
        
        if source == "webhook":
            # This task is from a webhook - issue already exists, skip creation
            issue_number = issue_num_match.group(1).strip() if issue_num_match else None
            issue_url = issue_url_match.group(1).strip() if issue_url_match else None
            agent_type = agent_type_match.group(1).strip() if agent_type_match else "triage"
            
            if not issue_url or not issue_number:
                logger.error(f"⚠️ Webhook task missing issue URL or number, skipping: {filepath}")
                return

            issue_repo = _extract_repo_from_issue_url(issue_url)
            if not issue_repo:
                message = (
                    f"🚫 Unable to parse issue repository for webhook task issue #{issue_number}. "
                    "Blocking processing to avoid cross-project execution."
                )
                logger.error(message)
                emit_alert(message, severity="error", source="inbox_processor")
                return

            configured_repos = []
            try:
                configured_repos = get_github_repos(project_name)
            except Exception:
                configured_repos = []

            if configured_repos and issue_repo not in configured_repos:
                reroute_project = _resolve_project_for_repo(issue_repo)
                if reroute_project and reroute_project != project_name:
                    rerouted_path = _reroute_webhook_task_to_project(filepath, reroute_project)
                    message = (
                        f"⚠️ Re-routed webhook task for issue #{issue_number}: "
                        f"repo {issue_repo} does not match project {project_name} ({configured_repos}); "
                        f"moved to project '{reroute_project}'."
                    )
                    logger.warning(message)
                    emit_alert(message, severity="warning", source="inbox_processor")
                    if rerouted_path:
                        logger.info(f"Moved webhook task to: {rerouted_path}")
                    return

                message = (
                    f"🚫 Project boundary violation for issue #{issue_number}: "
                    f"task under project '{project_name}' ({configured_repos}) "
                    f"but issue URL points to '{issue_repo}'. Processing blocked."
                )
                logger.error(message)
                emit_alert(message, severity="error", source="inbox_processor")
                return
            
            logger.info(f"📌 Webhook task for existing issue #{issue_number}, launching agent directly")
            
            # Guard: skip if an agent was recently launched for this issue
            # (prevents double-launch when processor creates a GH issue and the
            # resulting webhook fires back into this path)
            if is_recent_launch(issue_number):
                logger.info(f"⏭️ Skipping webhook launch for issue #{issue_number} — agent recently launched")
                # Still move file to active so it's not re-processed
                active_dir = get_tasks_active_dir(project_root, project_name)
                os.makedirs(active_dir, exist_ok=True)
                new_filepath = os.path.join(active_dir, os.path.basename(filepath))
                shutil.move(filepath, new_filepath)
                return
            
            # Move file to project workspace active folder
            active_dir = get_tasks_active_dir(project_root, project_name)
            os.makedirs(active_dir, exist_ok=True)
            new_filepath = os.path.join(active_dir, os.path.basename(filepath))
            logger.info(f"Moving task to active: {new_filepath}")
            shutil.move(filepath, new_filepath)
            
            # Launch agent directly for existing GitHub issue
            agents_dir_val = config.get("agents_dir")
            if agents_dir_val and issue_url:
                agents_abs = os.path.join(BASE_DIR, agents_dir_val)
                workspace_abs = os.path.join(BASE_DIR, config["workspace"])
                
                # Use specified agent type or get from workflow
                if not agent_type or agent_type == "triage":
                    agent_type = _get_initial_agent_from_workflow(project_name)
                    if not agent_type:
                        logger.error(f"Stopping launch: missing workflow definition for {project_name}")
                        emit_alert(f"Stopping launch: missing workflow for {project_name}", severity="error", source="inbox_processor")
                        return
                
                # Resolve tier (halt if unknown — prevents wrong workflow execution)
                try:
                    repo_for_tier = get_github_repo(project_name)
                except Exception:
                    repo_for_tier = ""

                if not repo_for_tier:
                    logger.error(
                        f"Missing git_repo for project '{project_name}', cannot resolve tier "
                        f"for issue #{issue_number}."
                    )
                    emit_alert(
                        f"Missing git_repo for project '{project_name}' "
                        f"(issue #{issue_number}).",
                        severity="error", source="inbox_processor",
                    )
                    return
                tier_name = _resolve_tier_for_issue(
                    issue_number, project_name, repo_for_tier, context="webhook launch"
                )
                if not tier_name:
                    return

                pid, tool_used = invoke_copilot_agent(
                    agents_dir=agents_abs,
                    workspace_dir=workspace_abs,
                    issue_url=issue_url,
                    tier_name=tier_name,
                    task_content=content,
                    log_subdir=project_name,
                    agent_type=agent_type,
                    project_name=project_name
                )
                
                if pid:
                    try:
                        with open(new_filepath, 'a') as f:
                            f.write(f"\n**Agent PID:** {pid}\n")
                            f.write(f"**Agent Tool:** {tool_used}\n")
                    except Exception as e:
                        logger.error(f"Failed to append PID: {e}")
                
                logger.info(f"✅ Launched {agent_type} agent for webhook issue #{issue_number}")
            else:
                logger.info(f"ℹ️ No agents directory for {project_name}, skipping agent launch.")
            
            return  # Done processing webhook task
        
        # Standard task processing (create new GitHub issue)
        # Check if task name was already generated (in telegram_bot)
        precomputed_task_name = _extract_inline_task_name(content)
        if precomputed_task_name:
            slug = slugify(precomputed_task_name)
            if slug:
                logger.info(f"✅ Using pre-generated task name: {slug}")
            else:
                slug = generate_issue_name(content, project_name)
        else:
            # Fallback: Generate concise task name using Gemini AI
            slug = generate_issue_name(content, project_name)

        # Determine SOP tier using intelligent routing (pass content for WorkflowRouter analysis)
        tier_name, sop_template, workflow_label = get_sop_tier(
            task_type=task_type,
            title=slug,  # Use slug as preliminary title
            body=content  # Pass full content for intelligent routing
        )
        workflow_checklist = _render_checklist_from_workflow(project_name, tier_name)
        sop_checklist = workflow_checklist or sop_template or _render_fallback_checklist(tier_name)

        # Move file to project workspace active folder
        active_dir = get_tasks_active_dir(project_root, project_name)
        os.makedirs(active_dir, exist_ok=True)
        new_filepath = os.path.join(active_dir, os.path.basename(filepath))
        logger.info(f"Moving task to active: {new_filepath}")
        shutil.move(filepath, new_filepath)

        # Create GitHub Issue with SOP checklist
        # Build type prefix for issue title
        type_prefixes = {
            "feature": "feat",
            "feature-simple": "feat",
            "bug": "fix",
            "hotfix": "hotfix",
            "chore": "chore",
            "refactor": "refactor",
            "improvement": "feat",
            "improvement-simple": "feat",
        }
        prefix = type_prefixes.get(task_type, task_type.split("-")[0] if "-" in task_type else task_type)
        issue_title = f"[{project_name}] {prefix}/{slug}"
        
        # Determine target branch name
        branch_name = f"{prefix}/{slug}"
        
        issue_body = f"""## Task
{content}

---

{sop_checklist}

---

**Project:** {project_name}
**Tier:** {tier_name}
**Target Branch:** `{branch_name}`
**Task File:** `{new_filepath}`"""

        issue_url = create_github_issue(
            title=issue_title,
            body=issue_body,
            project=project_name,
            workflow_label=workflow_label,
            task_type=task_type,
            tier_name=tier_name,
            github_repo=get_github_repo(project_name)
        )

        if issue_url:
            # Rename task file from {task_type}_{telegram_msg_id}.md to
            # {task_type}_{issue_num}.md so the GitHub issue number is visible
            # in the filename instead of a random Telegram message ID.
            issue_num = issue_url.split('/')[-1]
            old_basename = os.path.basename(new_filepath)
            new_basename = re.sub(r'_(\d+)\.md$', f'_{issue_num}.md', old_basename)
            if new_basename != old_basename:
                renamed_path = os.path.join(os.path.dirname(new_filepath), new_basename)
                try:
                    os.rename(new_filepath, renamed_path)
                    logger.info(f"Renamed task file: {old_basename} → {new_basename}")
                    # Keep the issue body consistent — update the Task File path
                    corrected_body = issue_body.replace(new_filepath, renamed_path)
                    subprocess.run(
                        ["gh", "issue", "edit", issue_num,
                         "--body", corrected_body,
                         "--repo", get_github_repo(project_name)],
                        capture_output=True, timeout=15
                    )
                    new_filepath = renamed_path
                except Exception as e:
                    logger.error(f"Failed to rename task file to issue-number name: {e}")

            # Append issue URL to the task file
            try:
                with open(new_filepath, 'a') as f:
                    f.write(f"\n\n**Issue:** {issue_url}\n")
            except Exception as e:
                logger.error(f"Failed to append issue URL: {e}")
            
            # Create nexus-core workflow
            workflow_plugin = get_workflow_state_plugin(
                **_WORKFLOW_STATE_PLUGIN_KWARGS,
                github_repo=get_github_repo(project_name),
                cache_key="workflow:state-engine",
            )
            workflow_id = asyncio.run(
                workflow_plugin.create_workflow_for_issue(
                    issue_number=issue_num,
                    issue_title=slug,
                    project_name=project_name,
                    tier_name=tier_name,
                    task_type=task_type,
                    description=content,
                )
            )
            if workflow_id:
                logger.info(f"✅ Created nexus-core workflow: {workflow_id}")
                started = asyncio.run(start_workflow(workflow_id, issue_num))
                if not started:
                    logger.warning(
                        f"Created workflow {workflow_id} for issue #{issue_num} "
                        "but failed to start it"
                    )
                try:
                    with open(new_filepath, 'a') as f:
                        f.write(f"**Workflow ID:** {workflow_id}\n")
                except Exception as e:
                    logger.error(f"Failed to append workflow ID: {e}")

        # Invoke Copilot CLI agent (if agents_dir is configured)
        agents_dir_val = config["agents_dir"]
        if agents_dir_val is not None and issue_url:
            agents_abs = os.path.join(BASE_DIR, agents_dir_val)
            workspace_abs = os.path.join(BASE_DIR, config["workspace"])
            initial_agent = _get_initial_agent_from_workflow(project_name)
            if not initial_agent:
                logger.error(
                    f"Stopping launch: missing workflow definition for {project_name}"
                )
                emit_alert(
                    f"Stopping launch: missing workflow definition for {project_name}",
                    severity="error", source="inbox_processor",
                )
                return

            pid, tool_used = invoke_copilot_agent(
                agents_dir=agents_abs,
                workspace_dir=workspace_abs,
                issue_url=issue_url,
                tier_name=tier_name,
                task_content=content,
                log_subdir=project_name,
                agent_type=initial_agent,
                project_name=project_name
            )

            if pid:
                # Log PID for tracking
                try:
                    with open(new_filepath, 'a') as f:
                        f.write(f"**Agent PID:** {pid}\n")
                        f.write(f"**Agent Tool:** {tool_used}\n")
                except Exception as e:
                    logger.error(f"Failed to append PID: {e}")
        else:
            logger.info(f"ℹ️ No agents directory for {project_name}, skipping Copilot CLI invocation.")

        logger.info(f"✅ Dispatch complete for [{project_name}] {slug} (Tier: {tier_name})")

    except Exception as e:
        logger.error(f"Failed to process {filepath}: {e}")


def main():
    logger.info(f"Inbox Processor started on {BASE_DIR}")
    
    # Initialize event handlers (including SocketIO bridge)
    from orchestration.nexus_core_helpers import setup_event_handlers
    setup_event_handlers()
    
    logger.info("Inbox storage backend (effective): %s", get_inbox_storage_backend())
    logger.info("Stuck agent monitoring enabled (using workflow agent timeout)")
    logger.info("Agent comment monitoring enabled")
    try:
        reconcile_completion_signals_on_startup()
    except Exception as e:
        logger.error(f"Startup completion-signal drift check failed: {e}")
    last_check = time.time()
    check_interval = 60  # Check for stuck agents and comments every 60 seconds
    
    while True:
        inbox_backend = get_inbox_storage_backend()

        if inbox_backend == "postgres":
            _drain_postgres_inbox_queue()
        else:
            # filesystem and both modes process file inbox directly
            nexus_dir_name = get_nexus_dir_name()
            pattern = os.path.join(BASE_DIR, "**", nexus_dir_name, "inbox", "*", "*.md")
            files = glob.glob(pattern, recursive=True)

            for filepath in files:
                process_file(filepath)
        
        # Periodically check for stuck agents and agent comments
        current_time = time.time()
        if current_time - last_check >= check_interval:
            check_stuck_agents()
            check_agent_comments()
            check_completed_agents()
            last_check = current_time

        time.sleep(SLEEP_INTERVAL)

def _drain_postgres_inbox_queue(batch_size: int = 25) -> None:
    """Claim pending Postgres inbox tasks and hand them to existing file processor.

    Tasks are materialized into the per-project inbox path and then processed using
    existing `process_file` logic to minimize migration risk.
    """
    worker_id = f"{os.uname().nodename}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    try:
        tasks = claim_pending_tasks(limit=batch_size, worker_id=worker_id)
    except Exception as exc:
        logger.error("Failed to claim Postgres inbox tasks: %s", exc)
        return

    if not tasks:
        return

    for task in tasks:
        task_path = ""
        try:
            workspace_abs = os.path.join(BASE_DIR, str(task.workspace))
            inbox_dir = get_inbox_dir(workspace_abs, str(task.project_key))
            os.makedirs(inbox_dir, exist_ok=True)
            task_path = os.path.join(inbox_dir, str(task.filename))

            if os.path.exists(task_path):
                stem, ext = os.path.splitext(str(task.filename))
                task_path = os.path.join(inbox_dir, f"{stem}_{int(time.time())}{ext}")

            with open(task_path, "w", encoding="utf-8") as handle:
                handle.write(str(task.markdown_content))

            process_file(task_path)

            if os.path.exists(task_path):
                mark_task_failed(task.id, "Task file remained in inbox after processing")
                continue

            mark_task_done(task.id)
        except Exception as exc:
            logger.error("Failed processing Postgres inbox task id=%s: %s", task.id, exc, exc_info=True)
            with contextlib.suppress(Exception):
                mark_task_failed(task.id, str(exc))


if __name__ == "__main__":
    main()
