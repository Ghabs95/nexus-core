import asyncio
import contextlib
import glob
import json
import logging
import os
import re
import shutil
import time
import uuid
from urllib.parse import urlparse

import yaml

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
    get_repo,
    get_repos,
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
from nexus.adapters.git.utils import build_issue_url
from nexus.core.completion_store import CompletionStore
from nexus.core.process_orchestrator import ProcessOrchestrator
from nexus.core.project.repo_utils import (
    iter_project_configs as _iter_project_configs,
)
from nexus.core.project.repo_utils import (
    project_repos_from_config as _project_repos_from_config,
)
from nexus.core.router import WorkflowRouter
from orchestration.ai_orchestrator import get_orchestrator
from orchestration.nexus_core_helpers import (
    complete_step_for_issue,
    get_git_platform,
    get_workflow_definition_path,
    start_workflow,
)
from orchestration.plugin_runtime import (
    get_workflow_monitor_policy_plugin,
    get_workflow_policy_plugin,
    get_workflow_state_plugin,
)
from runtime.agent_launcher import (
    invoke_copilot_agent,
    is_recent_launch,
)
from runtime.nexus_agent_runtime import NexusAgentRuntime
from services.comment_monitor_service import (
    run_comment_monitor_cycle as _run_comment_monitor_cycle,
)
from services.completion_monitor_service import (
    run_completion_monitor_cycle as _run_completion_monitor_cycle,
)
from services.issue_finalize_service import (
    cleanup_worktree as _finalize_cleanup_worktree,
)
from services.issue_finalize_service import (
    close_issue as _finalize_close_issue,
)
from services.issue_finalize_service import (
    create_pr_from_changes as _finalize_create_pr_from_changes,
)
from services.issue_finalize_service import (
    find_existing_pr as _finalize_find_existing_pr,
)
from services.issue_finalize_service import (
    verify_workflow_terminal_before_finalize as _verify_workflow_terminal_before_finalize,
)
from services.issue_lifecycle_service import (
    create_issue as _create_issue,
)
from services.issue_lifecycle_service import (
    rename_task_file_and_sync_issue_body as _rename_task_file_and_sync_issue_body,
)
from services.merge_queue_service import (
    enqueue_merge_queue_prs as _enqueue_merge_queue_prs,
)
from services.merge_queue_service import (
    merge_queue_auto_merge_once as _merge_queue_auto_merge_once,
)
from services.processor_loops_service import (
    run_processor_loop as _run_processor_loop,
)
from services.processor_runtime_state import (
    ProcessorRuntimeState,
)
from services.repo_resolution_service import (
    resolve_repo_for_issue as _service_resolve_repo_for_issue,
)
from services.startup_recovery_service import (
    reconcile_completion_signals_on_startup as _startup_reconcile_completion_signals,
)
from services.task_archive_service import (
    archive_closed_task_files as _archive_closed_task_files,
)
from services.task_context_service import (
    load_task_context as _load_task_context,
)
from services.task_dispatch_service import (
    handle_new_task as _handle_new_task,
)
from services.task_dispatch_service import (
    handle_webhook_task as _handle_webhook_task,
)
from services.tier_resolution_service import (
    resolve_tier_for_issue as _resolve_tier_for_issue,
)
from services.workflow_pr_monitor_service import (
    build_bot_comments_getter as _build_bot_comments_getter,
)
from services.workflow_pr_monitor_service import (
    build_workflow_issue_number_lister as _build_workflow_issue_number_lister,
)
from services.workflow_pr_monitor_service import (
    check_and_notify_pr as _service_check_and_notify_pr,
)
from services.workflow_recovery_service import (
    run_stuck_agents_cycle as _run_stuck_agents_cycle,
)
from services.workflow_signal_sync import (
    normalize_agent_reference as _normalize_agent_reference,
)
from services.workflow_unmapped_recovery_service import (
    recover_unmapped_issues_from_completions as _service_recover_unmapped_issues_from_completions,
)
from state_manager import HostStateManager

_STEP_COMPLETE_COMMENT_RE = re.compile(
    r"^\s*##\s+.+?\bcomplete\b\s+—\s+([0-9a-z_-]+)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_READY_FOR_COMMENT_RE = re.compile(
    r"\bready\s+for\s+(?:\*\*)?`?@?([0-9a-z_-]+)",
    re.IGNORECASE,
)
_WORKFLOW_MONITOR_LABELS = (
    "workflow:full",
    "workflow:shortened",
    "workflow:fast-track",
)


# Helper to get issue repo (currently defaults to nexus, should be extended for multi-project)
def get_issue_repo(project: str = "nexus") -> str:
    """Get the Git repo for issue operations.

    Args:
        project: Project name (currently unused, defaults to nexus)

    Returns:
        Git repo string

    Note: This should be extended to support per-project repos when multi-project
          issue tracking is implemented.
    """
    try:
        return get_repo(project)
    except Exception:
        return get_repo(get_default_project())


# Initialize orchestrator (CLI-only)
orchestrator = get_orchestrator(ORCHESTRATOR_CONFIG)

# Track mutable runtime state explicitly (aliases kept for behavior-preserving migration)
PROCESSOR_RUNTIME_STATE = ProcessorRuntimeState()
alerted_agents = PROCESSOR_RUNTIME_STATE.alerted_agents
notified_comments = (
    PROCESSOR_RUNTIME_STATE.notified_comments
)  # Track comment IDs we've already notified about
auto_chained_agents = PROCESSOR_RUNTIME_STATE.auto_chained_agents  # issue -> log_file
INBOX_PROCESSOR_STARTED_AT = time.time()
POLLING_FAILURE_THRESHOLD = 3
polling_failure_counts = PROCESSOR_RUNTIME_STATE.polling_failure_counts
_ORPHAN_RECOVERY_COOLDOWN_SECONDS = max(
    60,
    int(os.getenv("NEXUS_ORPHAN_RECOVERY_COOLDOWN_SECONDS", "180")),
)
_orphan_recovery_last_attempt = PROCESSOR_RUNTIME_STATE.orphan_recovery_last_attempt
from integrations.workflow_state_factory import get_workflow_state as _get_wf_state
from integrations.workflow_state_factory import get_storage_backend as _get_storage_backend

_WORKFLOW_STATE_PLUGIN_KWARGS = {
    "storage_dir": NEXUS_CORE_STORAGE_DIR,
    "issue_to_workflow_id": lambda n: _get_wf_state().get_workflow_id(n),
    "issue_to_workflow_map_setter": lambda n, w: _get_wf_state().map_issue(n, w),
    "workflow_definition_path_resolver": get_workflow_definition_path,
}


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
        with open(FAILED_LOOKUPS_FILE, "w") as f:
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
os.makedirs(os.path.dirname(INBOX_PROCESSOR_LOG_FILE), exist_ok=True)
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    force=True,
    handlers=[logging.StreamHandler(), logging.FileHandler(INBOX_PROCESSOR_LOG_FILE)],
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
            severity="error",
            source="inbox_processor",
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
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"\s+", "-", text)
    return text[:50]


def _resolve_project_from_path(summary_path: str) -> str:
    """Resolve project name from a completion_summary file path.

    Matches the path against configured project workspaces.
    Returns project key or empty string if no match.
    """
    for key, cfg in _iter_project_configs(PROJECT_CONFIG, get_repos):
        workspace = cfg.get("workspace")
        if not workspace:
            continue
        workspace_abs = os.path.join(BASE_DIR, str(workspace))
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
    for key, cfg in _iter_project_configs(PROJECT_CONFIG, get_repos):
        if repo_name in _project_repos_from_config(key, cfg, get_repos):
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

    workspace_abs = os.path.join(BASE_DIR, str(workspace_rel))
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
    return _service_resolve_repo_for_issue(
        issue_num=str(issue_num),
        default_project=default_project,
        project_config=PROJECT_CONFIG,
        get_default_project=get_default_project,
        get_repo=get_repo,
        iter_project_configs=_iter_project_configs,
        project_repos_from_config=_project_repos_from_config,
        get_repos=get_repos,
        get_git_platform=get_git_platform,
        extract_repo_from_issue_url=_extract_repo_from_issue_url,
        base_dir=BASE_DIR,
    )


def _resolve_repo_strict(project_name: str, issue_num: str) -> str:
    """Resolve repo with boundary checks between project and issue context."""
    project_repos: list[str] = []
    if project_name and project_name in PROJECT_CONFIG:
        project_repos = _project_repos_from_config(
            project_name,
            PROJECT_CONFIG[project_name],
            get_repos,
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

    return issue_repo or (project_repos[0] if project_repos else get_repo(get_default_project()))


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
    _startup_reconcile_completion_signals(
        logger=logger,
        emit_alert=emit_alert,
        get_workflow_state_mappings=lambda: _get_wf_state().load_all_mappings(),
        nexus_core_storage_dir=NEXUS_CORE_STORAGE_DIR,
        normalize_agent_reference=_normalize_agent_reference,
        extract_repo_from_issue_url=_extract_repo_from_issue_url,
        read_latest_local_completion=_read_latest_local_completion,
        read_latest_structured_comment=_read_latest_structured_comment,
        is_terminal_agent_reference=_is_terminal_agent_reference,
        complete_step_for_issue=complete_step_for_issue,
    )


def _is_terminal_agent_reference(agent_ref: str) -> bool:
    """Return True when a next-agent reference means workflow completion."""
    return _normalize_agent_reference(agent_ref).lower() in {
        "none",
        "n/a",
        "null",
        "no",
        "end",
        "done",
        "finish",
        "complete",
        "",
    }


def _resolve_git_dir(project_name: str) -> str | None:
    """Resolve the actual git repo directory for a project.

    Tries:
    1. workspace itself (e.g. /opt/git/<workspace>)
    2. workspace/repo_name (e.g. /opt/git/<org>/<repo>)

    Returns absolute path or None.
    """
    proj_cfg = PROJECT_CONFIG.get(project_name, {})
    workspace = str(proj_cfg.get("workspace", "") or "")
    configured_repo = str(proj_cfg.get("git_repo", "") or "")
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
        repo_names = get_repos(project_name)
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


def _workflow_policy_notify(message: str) -> None:
    emit_alert(message, severity="info", source="workflow_policy")


def _finalize_workflow(issue_num: str, repo: str, last_agent: str, project_name: str) -> None:
    """Handle workflow completion: close issue, create PR if needed, send Telegram.

    Called when the last agent finishes (next_agent is 'none' or empty).
    Delegates PR creation and issue closing to nexus-core GitPlatform.
    """
    try:
        workflow_plugin = get_workflow_state_plugin(
            **_WORKFLOW_STATE_PLUGIN_KWARGS,
            cache_key="workflow:state-engine",
        )
        if not _verify_workflow_terminal_before_finalize(
            workflow_plugin=workflow_plugin,
            issue_num=str(issue_num),
            project_name=project_name,
            alert_source="inbox_processor",
        ):
            return
    except Exception as exc:
        logger.warning(
            "Could not verify workflow state before finalize for issue #%s: %s",
            issue_num,
            exc,
        )

    workflow_policy = get_workflow_policy_plugin(
        resolve_git_dir=_resolve_git_dir,
        resolve_git_dirs=_resolve_git_dirs,
        create_pr_from_changes=lambda **kwargs: _finalize_create_pr_from_changes(
            project_name=project_name,
            repo=kwargs["repo"],
            repo_dir=kwargs["repo_dir"],
            issue_number=str(kwargs["issue_number"]),
            title=kwargs["title"],
            body=kwargs["body"],
            issue_repo=kwargs.get("issue_repo"),
        ),
        find_existing_pr=lambda **kwargs: _finalize_find_existing_pr(
            project_name=project_name,
            repo=kwargs["repo"],
            issue_number=str(kwargs["issue_number"]),
        ),
        cleanup_worktree=lambda **kwargs: _finalize_cleanup_worktree(
            repo_dir=kwargs["repo_dir"],
            issue_number=str(kwargs["issue_number"]),
        ),
        close_issue=lambda **kwargs: _finalize_close_issue(
            project_name=project_name,
            repo=kwargs["repo"],
            issue_number=str(kwargs["issue_number"]),
            comment=kwargs.get("comment"),
        ),
        send_notification=_workflow_policy_notify,
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
        _enqueue_merge_queue_prs(
            issue_num=str(issue_num),
            issue_repo=repo,
            project_name=project_name,
            pr_urls=[str(url) for url in pr_urls if str(url).strip()],
        )
    if result.get("issue_closed"):
        logger.info(f"🔒 Closed issue #{issue_num}")
        archived = _archive_closed_task_files(
            issue_num=str(issue_num),
            project_name=project_name,
            project_config=PROJECT_CONFIG,
            base_dir=BASE_DIR,
            get_tasks_active_dir=get_tasks_active_dir,
            get_tasks_closed_dir=get_tasks_closed_dir,
            logger=logger,
        )
        if archived:
            logger.info(f"📦 Archived {archived} task file(s) for closed issue #{issue_num}")


# ---------------------------------------------------------------------------
# ProcessOrchestrator singleton (Phase 3)
# ---------------------------------------------------------------------------

_process_orchestrator: ProcessOrchestrator | None = None
_completion_store: CompletionStore | None = None


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


def _get_completion_store() -> CompletionStore:
    """Build (or return cached) CompletionStore for current backend."""
    global _completion_store
    if _completion_store is not None:
        return _completion_store

    backend = get_inbox_storage_backend()
    storage = None
    if backend == "postgres":
        storage = _get_storage_backend()

    _completion_store = CompletionStore(
        backend=backend,
        storage=storage,
        base_dir=BASE_DIR,
        nexus_dir=get_nexus_dir_name(),
    )
    return _completion_store


def _post_completion_comments_from_logs() -> None:
    """Detect agent completions and auto-chain to the next workflow step.

    Delegates to :class:`ProcessOrchestrator` from nexus-core.
    """
    orc = _get_process_orchestrator()
    wfp = get_workflow_policy_plugin(cache_key="workflow-policy:inbox")

    dedup = set(completion_comments.keys())
    replay_window_seconds = get_completion_replay_window_seconds()
    replay_ref_ts = INBOX_PROCESSOR_STARTED_AT
    detected_completions = _get_completion_store().scan()
    orc.scan_and_process_completions(
        BASE_DIR,
        dedup,
        detected_completions=detected_completions,
        resolve_project=_resolve_project_from_path,
        resolve_repo=lambda proj, issue: _resolve_repo_strict(proj, issue),
        build_transition_message=lambda **kw: wfp.build_transition_message(**kw),
        build_autochain_failed_message=lambda **kw: wfp.build_autochain_failed_message(**kw),
        stale_completion_seconds=(replay_window_seconds if replay_window_seconds > 0 else None),
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
            severity="error",
            source="inbox_processor",
            project_key=project_name,
        )
        return ""
    if not os.path.exists(path):
        logger.error(f"Workflow definition not found: {path}")
        emit_alert(
            f"Workflow definition not found: {path}",
            severity="error",
            source="inbox_processor",
            project_key=project_name,
        )
        return ""
    try:
        workflow = WorkflowDefinition.from_yaml(path, workflow_type=workflow_type)
        if not workflow.steps:
            logger.error(f"Workflow definition has no steps: {path}")
            emit_alert(
                f"Workflow definition has no steps: {path}",
                severity="error",
                source="inbox_processor",
                project_key=project_name,
            )
            return ""
        first_step = workflow.steps[0]
        return first_step.agent.name or first_step.agent.display_name or ""
    except Exception as e:
        logger.error(f"Failed to read workflow definition {path}: {e}")
        emit_alert(
            f"Failed to read workflow definition {path}: {e}",
            severity="error",
            source="inbox_processor",
            project_key=project_name,
        )
        return ""


def check_stuck_agents():
    """Monitor agent processes and handle timeouts with auto-kill and retry.

    Delegates to :class:`ProcessOrchestrator` which implements both
    strategy-1 (stale-log timeout kill) and strategy-2 (dead-process detection).
    """
    _run_stuck_agents_cycle(
        logger=logger,
        base_dir=BASE_DIR,
        scope="stuck-agents:loop",
        orchestrator_check_stuck_agents=lambda base_dir: _get_process_orchestrator().check_stuck_agents(
            base_dir
        ),
        recover_orphaned_running_agents=_recover_orphaned_running_agents,
        recover_unmapped_issues_from_completions=_recover_unmapped_issues_from_completions,
        clear_polling_failures=_clear_polling_failures,
        record_polling_failure=_record_polling_failure,
    )


def _recover_orphaned_running_agents(max_relaunches: int = 3) -> int:
    """Relaunch missing processes for workflows still marked RUNNING.

    This recovery path is restart-safe: if services restart and launched-process
    tracker entries are missing while workflow state still expects an agent,
    this function relaunches that expected agent.
    """
    orchestrator = _get_process_orchestrator()
    runtime = getattr(orchestrator, "_runtime", None)
    if runtime is None:
        return 0

    try:
        mappings = _get_wf_state().load_all_mappings()
    except Exception as exc:
        logger.debug(f"Orphan recovery skipped (mapping load failed): {exc}")
        return 0

    if not isinstance(mappings, dict) or not mappings:
        return 0

    launched = HostStateManager.load_launched_agents(recent_only=False)
    if not isinstance(launched, dict):
        launched = {}

    now = time.time()
    recovered = 0

    issue_keys = [str(key) for key in mappings.keys()]
    issue_keys.sort(key=lambda value: int(value) if value.isdigit() else value)

    for issue_num in issue_keys:
        if recovered >= max_relaunches:
            break

        last_attempt = _orphan_recovery_last_attempt.get(issue_num, 0.0)
        if (now - last_attempt) < _ORPHAN_RECOVERY_COOLDOWN_SECONDS:
            continue

        workflow_state = runtime.get_workflow_state(issue_num)
        if workflow_state in {"PAUSED", "STOPPED", "COMPLETED", "FAILED", "CANCELLED"}:
            _orphan_recovery_last_attempt.pop(issue_num, None)
            continue

        expected_agent = runtime.get_expected_running_agent(issue_num)
        if not expected_agent:
            continue

        if runtime.is_process_running(issue_num):
            _orphan_recovery_last_attempt.pop(issue_num, None)
            continue

        tracker_entry = launched.get(issue_num, {})
        if not isinstance(tracker_entry, dict):
            tracker_entry = {}
        tracker_pid = tracker_entry.get("pid")
        if isinstance(tracker_pid, int) and tracker_pid > 0 and runtime.is_pid_alive(tracker_pid):
            continue

        workflow_id = str(mappings.get(issue_num, "") or "")
        project_name = _resolve_project_for_issue(issue_num, workflow_id=workflow_id)
        if not project_name:
            logger.info(
                "Skipping orphan recovery for issue #%s: unable to resolve project (workflow_id=%s)",
                issue_num,
                workflow_id or "unknown",
            )
            _orphan_recovery_last_attempt.pop(issue_num, None)
            continue

        repo_name = _resolve_repo_for_issue(issue_num, default_project=project_name)
        issue_open = runtime.is_issue_open(issue_num, repo_name)
        if issue_open is not True:
            status_label = "unknown" if issue_open is None else "closed/missing"
            logger.info(
                "Skipping orphan recovery for issue #%s: remote issue not confirmed open in %s (status=%s)",
                issue_num,
                repo_name,
                status_label,
            )
            _orphan_recovery_last_attempt.pop(issue_num, None)
            continue

        if not runtime.should_retry_dead_agent(issue_num, expected_agent):
            continue

        _orphan_recovery_last_attempt[issue_num] = now
        pid, tool = runtime.launch_agent(
            issue_num,
            expected_agent,
            trigger_source="orphan-recovery",
        )
        if pid:
            recovered += 1
            logger.warning(
                "Recovered orphaned workflow issue #%s by launching %s (PID %s, tool=%s)",
                issue_num,
                expected_agent,
                pid,
                tool,
            )
        else:
            logger.info(
                "Orphan recovery launch skipped/failed for issue #%s (agent=%s, reason=%s)",
                issue_num,
                expected_agent,
                tool,
            )

    return recovered


def _resolve_project_for_issue(issue_num: str, workflow_id: str | None = None) -> str | None:
    """Best-effort project resolution for an issue number."""
    task_file = _find_task_file_for_issue(issue_num)
    if task_file:
        project_name = _resolve_project_from_task_file(task_file)
        if project_name:
            return project_name

    normalized_workflow_id = str(workflow_id or "").strip()
    if normalized_workflow_id:
        project_keys = [name for name, _ in _iter_project_configs(PROJECT_CONFIG, get_repos)]
        for project_name in sorted(project_keys, key=len, reverse=True):
            if normalized_workflow_id == project_name or normalized_workflow_id.startswith(
                f"{project_name}-"
            ):
                return project_name

    return None


def _find_task_file_for_issue(issue_num: str) -> str | None:
    """Find a local task markdown file for an issue number."""
    issue = str(issue_num).strip()
    if not issue:
        return None

    nexus_dir_name = get_nexus_dir_name()
    patterns = [
        os.path.join(
            BASE_DIR,
            "**",
            nexus_dir_name,
            "tasks",
            "*",
            "active",
            f"issue_{issue}.md",
        ),
        os.path.join(
            BASE_DIR,
            "**",
            nexus_dir_name,
            "tasks",
            "*",
            "active",
            f"*_{issue}.md",
        ),
        os.path.join(
            BASE_DIR,
            "**",
            nexus_dir_name,
            "tasks",
            "*",
            "closed",
            f"issue_{issue}.md",
        ),
        os.path.join(
            BASE_DIR,
            "**",
            nexus_dir_name,
            "tasks",
            "*",
            "closed",
            f"*_{issue}.md",
        ),
    ]
    candidates: list[str] = []
    for pattern in patterns:
        candidates.extend(glob.glob(pattern, recursive=True))

    if not candidates:
        return None

    candidates.sort(key=os.path.getmtime, reverse=True)
    return candidates[0]


def _resolve_project_from_task_file(task_file: str) -> str | None:
    """Resolve project key from a task file path."""
    task_abs = os.path.abspath(task_file)
    for project_key, project_cfg in _iter_project_configs(PROJECT_CONFIG, get_repos):
        workspace = project_cfg.get("workspace") if isinstance(project_cfg, dict) else None
        if not workspace:
            continue
        workspace_abs = os.path.abspath(os.path.join(BASE_DIR, workspace))
        if task_abs.startswith(workspace_abs + os.sep) or task_abs == workspace_abs:
            return project_key
    return None


def _recover_unmapped_issues_from_completions(max_relaunches: int = 20) -> int:
    """Recover issues missing workflow mapping using latest completion signal.

    This path handles cases where workflow mapping is lost after restarts but
    local completion + task context still indicates the next agent to run.
    """
    runtime = getattr(_get_process_orchestrator(), "_runtime", None)
    return _service_recover_unmapped_issues_from_completions(
        max_relaunches=max_relaunches,
        logger=logger,
        runtime=runtime,
        completion_store=_get_completion_store(),
        host_state_manager=HostStateManager,
        get_workflow_id=lambda issue_num: _get_wf_state().get_workflow_id(issue_num),
        normalize_agent_reference=_normalize_agent_reference,
        is_terminal_agent_reference=_is_terminal_agent_reference,
        find_task_file_for_issue=_find_task_file_for_issue,
        resolve_project_from_task_file=_resolve_project_from_task_file,
        get_default_project=get_default_project,
        project_config=PROJECT_CONFIG,
        resolve_repo_for_issue=_resolve_repo_for_issue,
        build_issue_url=build_issue_url,
        get_sop_tier=get_sop_tier,
        invoke_copilot_agent=invoke_copilot_agent,
        base_dir=BASE_DIR,
        orphan_recovery_last_attempt=_orphan_recovery_last_attempt,
        orphan_recovery_cooldown_seconds=_ORPHAN_RECOVERY_COOLDOWN_SECONDS,
    )


def check_agent_comments():
    """Monitor Git issues for agent comments requesting input across all projects."""
    _run_comment_monitor_cycle(
        logger=logger,
        iter_projects=lambda: _iter_project_configs(PROJECT_CONFIG, get_repos),
        get_project_platform=get_project_platform,
        get_repo=get_repo,
        list_workflow_issue_numbers=_build_workflow_issue_number_lister(
            get_workflow_monitor_policy_plugin=get_workflow_monitor_policy_plugin,
            get_git_platform=get_git_platform,
            workflow_labels=_WORKFLOW_MONITOR_LABELS,
        ),
        get_bot_comments=_build_bot_comments_getter(
            get_workflow_monitor_policy_plugin=get_workflow_monitor_policy_plugin,
            get_git_platform=get_git_platform,
            bot_author="Ghabs95",
        ),
        notify_agent_needs_input=notify_agent_needs_input,
        notified_comments=PROCESSOR_RUNTIME_STATE.notified_comments,
        clear_polling_failures=_clear_polling_failures,
        record_polling_failure=_record_polling_failure,
    )


def check_and_notify_pr(issue_num, project):
    """
    Check if there's a PR linked to the issue and notify user for review.

    Delegates to nexus-core's GitPlatform.search_linked_prs().

    Args:
        issue_num: Git issue number
        project: Project name
    """
    _service_check_and_notify_pr(
        issue_num=issue_num,
        project=project,
        logger=logger,
        get_repo=get_repo,
        get_workflow_monitor_policy_plugin=get_workflow_monitor_policy_plugin,
        get_git_platform=get_git_platform,
        notify_workflow_completed=notify_workflow_completed,
    )


def check_completed_agents():
    """Monitor for completed agent steps and auto-chain to next agent.

    Delegates to _post_completion_comments_from_logs()
    which uses the nexus-core framework for completion scanning and auto-chaining.
    """
    _run_completion_monitor_cycle(
        post_completion_comments_from_logs=_post_completion_comments_from_logs,
    )


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


def generate_issue_name(content, project_name):
    """Generate a concise task name using orchestrator (CLI only).

    Returns a slugified name in format: "this-is-the-task-name"
    Falls back to slugified content if AI tools are unavailable.
    """
    try:
        logger.info("Generating concise task name with orchestrator...")
        result = orchestrator.run_text_to_speech_analysis(
            text=content[:500], task="generate_name", project_name=project_name
        )

        suggested_name = result.get("text", "").strip().strip("\"`'").strip()
        slug = slugify(suggested_name)

        if slug:
            logger.info(f"✨ Orchestrator suggested: {slug}")
            return slug

        raise ValueError("Empty slug from orchestrator")

    except Exception as e:
        logger.warning(f"Name generation failed: {e}, using fallback")
        body = re.sub(r"^#.*\n", "", content)
        body = re.sub(r"\*\*.*\*\*.*\n", "", body)
        return slugify(body.strip()) or "generic-task"


def _refine_issue_content(content: str, project_name: str) -> str:
    """Refine task text before issue creation, preserving original on failure."""
    source = str(content or "").strip()
    if not source:
        return source

    try:
        logger.info("Refining issue content with orchestrator (len=%s)", len(source))
        result = orchestrator.run_text_to_speech_analysis(
            text=source,
            task="refine_description",
            project_name=project_name,
        )
        candidate = str((result or {}).get("text", "")).strip()
        if candidate:
            return candidate
    except Exception as exc:
        logger.warning("Issue content refinement failed: %s", exc)

    return source


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
        project_root = None
        task_ctx = _load_task_context(
            filepath=filepath,
            project_config=PROJECT_CONFIG,
            base_dir=BASE_DIR,
            get_nexus_dir_name=get_nexus_dir_name,
            iter_project_configs=_iter_project_configs,
            get_repos=get_repos,
        )
        if not task_ctx:
            logger.warning(f"⚠️ No project config for workspace '{project_root}', skipping.")
            return
        content = task_ctx["content"]
        task_type = str(task_ctx["task_type"])
        project_name = task_ctx["project_name"]
        project_root = task_ctx["project_root"]
        config = task_ctx["config"]

        logger.info(f"Project: {project_name}")

        if _handle_webhook_task(
            filepath=filepath,
            content=content,
            project_name=str(project_name),
            project_root=str(project_root),
            config=config,
            base_dir=BASE_DIR,
            logger=logger,
            emit_alert=emit_alert,
            get_repos_for_project=get_repos,
            extract_repo_from_issue_url=_extract_repo_from_issue_url,
            resolve_project_for_repo=_resolve_project_for_repo,
            reroute_webhook_task_to_project=_reroute_webhook_task_to_project,
            get_tasks_active_dir=get_tasks_active_dir,
            is_recent_launch=is_recent_launch,
            get_initial_agent_from_workflow=_get_initial_agent_from_workflow,
            get_repo_for_project=get_repo,
            resolve_tier_for_issue=_resolve_tier_for_issue,
            invoke_copilot_agent=invoke_copilot_agent,
        ):
            return

        _handle_new_task(
            filepath=filepath,
            content=content,
            task_type=task_type,
            project_name=str(project_name),
            project_root=str(project_root),
            config=config,
            base_dir=BASE_DIR,
            logger=logger,
            emit_alert=emit_alert,
            get_repo_for_project=get_repo,
            get_tasks_active_dir=get_tasks_active_dir,
            refine_issue_content=_refine_issue_content,
            extract_inline_task_name=_extract_inline_task_name,
            slugify=slugify,
            generate_issue_name=generate_issue_name,
            get_sop_tier=get_sop_tier,
            render_checklist_from_workflow=_render_checklist_from_workflow,
            render_fallback_checklist=_render_fallback_checklist,
            create_issue=_create_issue,
            rename_task_file_and_sync_issue_body=_rename_task_file_and_sync_issue_body,
            get_workflow_state_plugin=get_workflow_state_plugin,
            workflow_state_plugin_kwargs=_WORKFLOW_STATE_PLUGIN_KWARGS,
            start_workflow=start_workflow,
            get_initial_agent_from_workflow=_get_initial_agent_from_workflow,
            invoke_copilot_agent=invoke_copilot_agent,
        )

    except Exception as e:
        logger.error(f"Failed to process {filepath}: {e}")


def _process_filesystem_inbox_once(base_dir: str) -> None:
    """Scan and process filesystem inbox tasks once."""
    nexus_dir_name = get_nexus_dir_name()
    pattern = os.path.join(base_dir, "**", nexus_dir_name, "inbox", "*", "*.md")
    files = glob.glob(pattern, recursive=True)

    for filepath in files:
        process_file(filepath)


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
    # Run one immediate recovery pass on startup so restarts do not leave
    # RUNNING workflow steps orphaned until the first periodic check.
    check_stuck_agents()
    _run_processor_loop(
        logger=logger,
        base_dir=BASE_DIR,
        sleep_interval=SLEEP_INTERVAL,
        check_interval=60,
        get_inbox_storage_backend=get_inbox_storage_backend,
        drain_postgres_inbox_queue=_drain_postgres_inbox_queue,
        process_filesystem_inbox_once=_process_filesystem_inbox_once,
        check_stuck_agents=check_stuck_agents,
        check_agent_comments=check_agent_comments,
        check_completed_agents=check_completed_agents,
        merge_queue_auto_merge_once=_merge_queue_auto_merge_once,
        runtime_state=PROCESSOR_RUNTIME_STATE,
        time_module=time,
    )


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
            logger.error(
                "Failed processing Postgres inbox task id=%s: %s", task.id, exc, exc_info=True
            )
            with contextlib.suppress(Exception):
                mark_task_failed(task.id, str(exc))


if __name__ == "__main__":
    main()
