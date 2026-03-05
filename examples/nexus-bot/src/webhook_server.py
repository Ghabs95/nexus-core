#!/usr/bin/env python3
"""
Nexus Webhook Server - Receives and processes Git webhook events

This service replaces the polling-based Git comment checking with
real-time webhook event processing for faster response times.

Event handlers:
- issues.opened: Convert Git issue to markdown task in .nexus/inbox/<project>/ for triage
- issue_comment.created: Detect workflow completion and chain to next agent
- pull_request.opened/synchronized: Notify about new PRs
- pull_request_review.submitted: Notify about PR reviews
"""

import asyncio
import logging
import os
import sys
import time

from flask import Flask, jsonify, redirect, request, send_from_directory

try:
    from flask_socketio import SocketIO
except ImportError:

    class SocketIO:  # type: ignore[no-redef]
        """Minimal fallback used when flask-socketio is unavailable (tests/dev)."""

        def __init__(self, app, *args, **kwargs):
            self.app = app

        def emit(self, *args, **kwargs):
            return None

        def run(self, app, *args, **kwargs):
            return app.run(*args, **kwargs)


# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nexus.core.project.repo_utils import project_repos_from_config as _project_repos
from nexus.core.workspace import WorkspaceManager

from config import (
    BASE_DIR,
    LOGS_DIR,
    NEXUS_ACCESS_SYNC_INTERVAL_MINUTES,
    NEXUS_AUTH_ENABLED,
    NEXUS_STORAGE_BACKEND,
    NEXUS_CORE_STORAGE_DIR,
    NEXUS_STORAGE_DSN,
    NEXUS_WORKFLOW_BACKEND,
    PROJECT_CONFIG,
    WEBHOOK_PORT,
    WEBHOOK_SECRET,
    get_default_project,
    get_repos,
    get_inbox_dir,
    get_inbox_storage_backend,
    get_tasks_active_dir,
)
from integrations.inbox_queue import enqueue_task
from integrations.notifications import (
    emit_alert,
    send_notification,
)
from orchestration.plugin_runtime import (
    get_webhook_policy_plugin,
    get_workflow_state_plugin,
)
from orchestration.nexus_core_helpers import get_workflow_definition_path
from runtime.agent_launcher import launch_next_agent
from services.webhook_issue_service import handle_issue_opened_event as _handle_issue_opened_event
from services.webhook_comment_service import (
    handle_issue_comment_event as _handle_issue_comment_event,
)
from services.webhook_pr_service import handle_pull_request_event as _handle_pull_request_event
from services.webhook_pr_review_service import (
    handle_pull_request_review_event as _handle_pull_request_review_event,
)
from services.webhook_http_service import process_webhook_request as _process_webhook_request
from services.auth_session_service import (
    complete_github_oauth as _svc_complete_github_oauth,
)
from services.auth_session_service import (
    complete_gitlab_oauth as _svc_complete_gitlab_oauth,
)
from services.auth_session_service import (
    get_session_and_setup_status as _svc_get_session_and_setup_status,
)
from services.auth_session_service import (
    start_oauth_flow as _svc_start_oauth_flow,
)
from services.auth_session_service import (
    store_ai_provider_keys as _svc_store_ai_provider_keys,
)
from services.auth_session_service import (
    store_codex_api_key as _svc_store_codex_api_key,
)
from services.project_access_service import (
    refresh_stale_access_grants as _svc_refresh_stale_access_grants,
)
from services.runtime_mode_service import is_issue_process_running

# Configure logging
os.makedirs(LOGS_DIR, exist_ok=True)


def _build_webhook_logging_handlers() -> list[logging.Handler]:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        handlers.insert(0, logging.FileHandler(os.path.join(LOGS_DIR, "webhook.log")))
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "File logging unavailable for webhook server (%s); using stream handler only.",
            exc,
        )
    return handlers


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=_build_webhook_logging_handlers(),
)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder=os.path.join(os.path.dirname(__file__), "static"))
_socketio_async_mode = str(os.getenv("NEXUS_SOCKETIO_ASYNC_MODE", "threading")).strip().lower()
if _socketio_async_mode not in {"threading", "eventlet", "gevent", "gevent_uwsgi"}:
    _socketio_async_mode = "threading"
socketio = SocketIO(app, async_mode=_socketio_async_mode, cors_allowed_origins="*")

# Track processed events to avoid duplicates
processed_events = set()
_last_acl_sync_at = 0.0


def _run_acl_sync_if_due() -> None:
    global _last_acl_sync_at
    if not NEXUS_AUTH_ENABLED:
        return
    interval_seconds = max(60, int(NEXUS_ACCESS_SYNC_INTERVAL_MINUTES) * 60)
    now = time.time()
    if _last_acl_sync_at and (now - _last_acl_sync_at) < interval_seconds:
        return
    try:
        result = _svc_refresh_stale_access_grants(limit=200)
        _last_acl_sync_at = now
        if int(result.get("processed") or 0) > 0:
            logger.info("Auth ACL sync: %s", result)
    except Exception as exc:
        logger.warning("Periodic ACL sync failed: %s", exc)


def _collect_visualizer_snapshot() -> list[dict]:
    """Return a best-effort snapshot of mapped workflows for visualizer bootstrap."""
    try:
        from integrations.workflow_state_factory import get_workflow_state
        from orchestration.plugin_runtime import get_workflow_state_plugin

        workflow_state = get_workflow_state()
        mappings = workflow_state.load_all_mappings() or {}
        if not isinstance(mappings, dict) or not mappings:
            return []

        workflow_plugin = get_workflow_state_plugin(
            storage_dir=NEXUS_CORE_STORAGE_DIR,
            storage_type=("postgres" if NEXUS_WORKFLOW_BACKEND == "postgres" else "file"),
            storage_config=(
                {"connection_string": NEXUS_STORAGE_DSN}
                if NEXUS_WORKFLOW_BACKEND == "postgres" and NEXUS_STORAGE_DSN
                else {}
            ),
            issue_to_workflow_id=lambda n: workflow_state.get_workflow_id(n),
            clear_pending_approval=lambda n: workflow_state.clear_pending_approval(n),
            cache_key="workflow:state-engine:visualizer-snapshot",
        )

        async def _load() -> list[dict]:
            records: list[dict] = []
            for issue_num, workflow_id in sorted(mappings.items(), key=lambda kv: str(kv[0])):
                try:
                    status = await workflow_plugin.get_workflow_status(str(issue_num))
                except Exception:
                    status = None

                records.append(
                    {
                        "issue": str(issue_num),
                        "workflow_id": str(workflow_id),
                        "status": status or {},
                    }
                )
            return records

        return asyncio.run(_load())
    except Exception as exc:
        logger.warning("Failed to collect visualizer snapshot: %s", exc)
        return []


# Register SocketIO emitter with HostStateManager for real-time transition broadcasting
try:
    from state_manager import set_socketio_emitter

    set_socketio_emitter(lambda event, data: socketio.emit(event, data, namespace="/visualizer"))
    logger.info("✅ SocketIO emitter registered with HostStateManager")
except Exception as _e:
    logger.warning(f"⚠️ Could not register SocketIO emitter: {_e}")


@socketio.on("connect", namespace="/visualizer")
def _visualizer_socket_connect():
    logger.info("Visualizer Socket.IO client connected")


@socketio.on("disconnect", namespace="/visualizer")
def _visualizer_socket_disconnect():
    logger.info("Visualizer Socket.IO client disconnected")


def _get_webhook_policy():
    """Get framework webhook policy plugin."""
    return get_webhook_policy_plugin(cache_key="git-webhook-policy:webhook")


def _repo_to_project_key(repo_name: str) -> str:
    """Best-effort mapping from repository full_name to configured project key."""
    policy = _get_webhook_policy()
    return policy.resolve_project_key(
        repo_name,
        PROJECT_CONFIG,
        default_project=get_default_project(),
    )


def _effective_review_mode(repo_name: str) -> str:
    """Resolve effective merge review mode for a repo.

    Returns one of: manual, auto.
    """
    policy = _get_webhook_policy()
    return policy.resolve_review_mode(repo_name, PROJECT_CONFIG, default_mode="manual")


def _resolve_git_dir_for_repo(repo_name: str) -> str | None:
    project_key = _repo_to_project_key(repo_name)
    project_cfg = PROJECT_CONFIG.get(project_key, {})
    if not isinstance(project_cfg, dict):
        return None

    workspace_rel = str(project_cfg.get("workspace", "") or "").strip()
    if not workspace_rel:
        return None

    workspace_abs = os.path.join(BASE_DIR, workspace_rel)
    repo_basename = str(repo_name or "").strip().split("/")[-1]
    if not repo_basename:
        return None

    # Either workspace is the repo root, or repo lives under workspace/{repo}.
    if os.path.isdir(os.path.join(workspace_abs, ".git")):
        if os.path.basename(workspace_abs.rstrip(os.sep)) == repo_basename:
            return workspace_abs

    candidate = os.path.join(workspace_abs, repo_basename)
    if os.path.isdir(os.path.join(candidate, ".git")):
        return candidate
    return None


def _cleanup_worktree_for_issue(repo_name: str, issue_number: str) -> bool:
    git_dir = _resolve_git_dir_for_repo(repo_name)
    if not git_dir:
        logger.info(
            "Skipping webhook worktree cleanup for issue #%s: could not resolve git dir for %s",
            issue_number,
            repo_name,
        )
        return False

    return bool(
        WorkspaceManager.cleanup_worktree_safe(
            base_repo_path=git_dir,
            issue_number=str(issue_number),
            is_issue_agent_running=lambda value: is_issue_process_running(
                value, cache_key="runtime-ops:webhook"
            ),
            require_clean=True,
        )
    )


def _notify_lifecycle(message: str) -> bool:
    """Send lifecycle notification via abstract notifier, fallback to Telegram alert."""
    if send_notification(message):
        return True
    return emit_alert(message, severity="info", source="webhook_server")


def _get_runtime_workflow_plugin():
    """Build workflow-state plugin for webhook-triggered manual resets."""
    from integrations.workflow_state_factory import get_workflow_state

    workflow_state = get_workflow_state()
    return get_workflow_state_plugin(
        storage_dir=NEXUS_CORE_STORAGE_DIR,
        storage_type=("postgres" if NEXUS_WORKFLOW_BACKEND == "postgres" else "file"),
        storage_config=(
            {"connection_string": NEXUS_STORAGE_DSN}
            if NEXUS_WORKFLOW_BACKEND == "postgres" and NEXUS_STORAGE_DSN
            else {}
        ),
        issue_to_workflow_id=lambda n: workflow_state.get_workflow_id(n),
        issue_to_workflow_map_setter=lambda n, w: workflow_state.map_issue(n, w),
        workflow_definition_path_resolver=get_workflow_definition_path,
        clear_pending_approval=lambda n: workflow_state.clear_pending_approval(n),
        cache_key="workflow:state-engine:webhook-runtime",
    )


def _reset_workflow_to_agent(issue_number: str, agent_ref: str) -> bool:
    """Realign workflow RUNNING step before manual webhook-driven launch."""
    try:
        workflow_plugin = _get_runtime_workflow_plugin()
    except Exception as exc:
        logger.warning(
            "Could not initialize workflow plugin for manual override reset issue #%s -> %s: %s",
            issue_number,
            agent_ref,
            exc,
        )
        return False

    try:
        return bool(
            asyncio.run(workflow_plugin.reset_to_agent_for_issue(str(issue_number), str(agent_ref)))
        )
    except Exception as exc:
        logger.warning(
            "Manual override reset failed for issue #%s -> %s: %s",
            issue_number,
            agent_ref,
            exc,
        )
        return False


def verify_signature(payload_body, signature_header, gitlab_token_header=None):
    """Verify Git webhook signature/token."""
    policy = _get_webhook_policy()
    verified = bool(
        policy.verify_signature(payload_body, signature_header, WEBHOOK_SECRET, gitlab_token_header)
    )
    if not WEBHOOK_SECRET and verified:
        logger.warning("⚠️ WEBHOOK_SECRET not configured - accepting all requests (INSECURE!)")
    if not verified:
        logger.error("❌ Signature verification failed")
    return verified


def handle_issue_opened(payload, event):
    """
    Handle issues.opened events.

    Converts Git issue into an inbox task (Postgres queue or filesystem inbox)
    for the inbox processor to route to the appropriate agent based on type.

    Agent types (abstract roles):
    - triage: Initial issue analysis and classification
    - escalation: High-priority/urgent issues (escalate to senior agent)
    - debug: Bug analysis and root cause

    The actual agent implementing each type is defined in the workflow YAML.
    """
    policy = _get_webhook_policy()
    return _handle_issue_opened_event(
        event=event,
        logger=logger,
        policy=policy,
        notify_lifecycle=_notify_lifecycle,
        emit_alert=emit_alert,
        project_config=PROJECT_CONFIG,
        base_dir=BASE_DIR,
        project_repos=_project_repos,
        get_repos=get_repos,
        get_tasks_active_dir=get_tasks_active_dir,
        get_inbox_dir=get_inbox_dir,
        get_inbox_storage_backend=get_inbox_storage_backend,
        enqueue_task=enqueue_task,
        cleanup_worktree_for_issue=_cleanup_worktree_for_issue,
    )


def handle_issue_comment(payload, event):
    """
    Handle issue_comment events.

    Detects workflow completion markers in comments and chains to next agent.
    """
    policy = _get_webhook_policy()
    from inbox_processor import check_and_notify_pr

    return _handle_issue_comment_event(
        event=event,
        logger=logger,
        policy=policy,
        processed_events=processed_events,
        launch_next_agent=launch_next_agent,
        check_and_notify_pr=check_and_notify_pr,
        reset_workflow_to_agent=_reset_workflow_to_agent,
    )


def handle_pull_request(payload, event):
    """Handle pull_request events (opened, synchronized, etc.)."""
    policy = _get_webhook_policy()
    return _handle_pull_request_event(
        event=event,
        logger=logger,
        policy=policy,
        notify_lifecycle=_notify_lifecycle,
        effective_review_mode=_effective_review_mode,
        launch_next_agent=launch_next_agent,
        cleanup_worktree_for_issue=_cleanup_worktree_for_issue,
    )


def handle_pull_request_review(payload, event):
    """Handle pull_request_review events."""
    return _handle_pull_request_review_event(event=event, logger=logger)


def _render_auth_message(title: str, body: str, *, status_code: int = 200):
    html = f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{title}</title>
    <style>
      body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 2rem; color: #0f172a; }}
      main {{ max-width: 720px; margin: 0 auto; }}
      .card {{ border: 1px solid #cbd5e1; border-radius: 12px; padding: 1rem 1.2rem; background: #f8fafc; }}
      code {{ background: #e2e8f0; padding: 0.1rem 0.3rem; border-radius: 4px; }}
      input[type=text] {{ width: 100%; padding: 0.6rem; border-radius: 8px; border: 1px solid #94a3b8; }}
      button {{ margin-top: 0.8rem; background: #0f766e; color: white; border: none; padding: 0.7rem 1rem; border-radius: 8px; cursor: pointer; }}
      small {{ color: #475569; }}
    </style>
  </head>
  <body>
    <main>
      <h1>{title}</h1>
      <div class="card">{body}</div>
    </main>
  </body>
</html>"""
    return html, status_code, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/auth/start", methods=["GET"])
def auth_start():
    if not NEXUS_AUTH_ENABLED:
        return _render_auth_message(
            "Auth Disabled",
            "Auth onboarding is disabled in this environment.",
            status_code=404,
        )
    session_id = str(request.args.get("session", "")).strip()
    if not session_id:
        return _render_auth_message("Invalid Request", "Missing <code>session</code> parameter.", status_code=400)
    provider = str(request.args.get("provider", "")).strip().lower()
    if not provider:
        provider = "gitlab" if os.getenv("NEXUS_GITLAB_CLIENT_ID") else "github"
    try:
        oauth_url, _state = _svc_start_oauth_flow(session_id, provider=provider)
    except Exception as exc:
        return _render_auth_message("Login Error", f"Failed to start OAuth: <code>{exc}</code>", status_code=400)
    return redirect(oauth_url, code=302)


@app.route("/auth/github/callback", methods=["GET"])
def auth_github_callback():
    if not NEXUS_AUTH_ENABLED:
        return _render_auth_message(
            "Auth Disabled",
            "Auth onboarding is disabled in this environment.",
            status_code=404,
        )
    code = str(request.args.get("code", "")).strip()
    state = str(request.args.get("state", "")).strip()
    if not code or not state:
        return _render_auth_message(
            "OAuth Error",
            "Missing <code>code</code> or <code>state</code> in callback.",
            status_code=400,
        )
    try:
        result = _svc_complete_github_oauth(code=code, state=state)
    except Exception as exc:
        return _render_auth_message("OAuth Error", f"{exc}", status_code=400)

    session_id = str(result.get("session_id") or "").strip()
    grants_count = int(result.get("grants_count") or 0)
    github_login = str(result.get("github_login") or "").strip()
    form_body = f"""
<p>GitHub login linked successfully as <strong>{github_login or "unknown"}</strong>.</p>
<p>Project grants resolved: <strong>{grants_count}</strong>.</p>
<form method="post" action="/auth/ai-keys">
  <input type="hidden" name="session_id" value="{session_id}" />
  <label for="codex_api_key"><strong>Codex/OpenAI API Key</strong></label>
  <input id="codex_api_key" name="codex_api_key" type="text" placeholder="sk-..." />
  <label for="gemini_api_key"><strong>Gemini API Key (optional)</strong></label>
  <input id="gemini_api_key" name="gemini_api_key" type="text" placeholder="AIza..." />
  <label for="claude_api_key"><strong>Claude API Key (optional)</strong></label>
  <input id="claude_api_key" name="claude_api_key" type="text" placeholder="sk-ant-..." />
  <label style="display:block; margin-top:0.8rem;">
    <input type="checkbox" name="use_copilot" value="1" checked />
    Use Copilot with this GitHub account (no extra key)
  </label>
  <button type="submit">Save Keys</button>
  <p><small>Add at least one key (Codex/Gemini/Claude), or keep Copilot enabled. Keys are encrypted at rest and used only for your own task execution.</small></p>
</form>
"""
    return _render_auth_message("Complete Setup", form_body, status_code=200)


@app.route("/auth/gitlab/callback", methods=["GET"])
def auth_gitlab_callback():
    if not NEXUS_AUTH_ENABLED:
        return _render_auth_message(
            "Auth Disabled",
            "Auth onboarding is disabled in this environment.",
            status_code=404,
        )
    code = str(request.args.get("code", "")).strip()
    state = str(request.args.get("state", "")).strip()
    if not code or not state:
        return _render_auth_message(
            "OAuth Error",
            "Missing <code>code</code> or <code>state</code> in callback.",
            status_code=400,
        )
    try:
        result = _svc_complete_gitlab_oauth(code=code, state=state)
    except Exception as exc:
        return _render_auth_message("OAuth Error", f"{exc}", status_code=400)

    session_id = str(result.get("session_id") or "").strip()
    grants_count = int(result.get("grants_count") or 0)
    gitlab_username = str(result.get("gitlab_username") or "").strip()
    form_body = f"""
<p>GitLab account linked successfully as <strong>{gitlab_username or "unknown"}</strong>.</p>
<p>Project grants resolved: <strong>{grants_count}</strong>.</p>
<form method="post" action="/auth/ai-keys">
  <input type="hidden" name="session_id" value="{session_id}" />
  <label for="codex_api_key"><strong>Codex/OpenAI API Key</strong></label>
  <input id="codex_api_key" name="codex_api_key" type="text" placeholder="sk-..." />
  <label for="gemini_api_key"><strong>Gemini API Key (optional)</strong></label>
  <input id="gemini_api_key" name="gemini_api_key" type="text" placeholder="AIza..." />
  <label for="claude_api_key"><strong>Claude API Key (optional)</strong></label>
  <input id="claude_api_key" name="claude_api_key" type="text" placeholder="sk-ant-..." />
  <label style="display:block; margin-top:0.8rem;">
    <input type="checkbox" name="use_copilot" value="1" />
    Use Copilot with a linked GitHub account (optional)
  </label>
  <button type="submit">Save Keys</button>
  <p><small>Add at least one key (Codex/Gemini/Claude), or enable Copilot if your GitHub account is linked. Keys are encrypted at rest and used only for your own task execution.</small></p>
</form>
"""
    return _render_auth_message("Complete Setup", form_body, status_code=200)


@app.route("/auth/codex-key", methods=["POST"])
def auth_codex_key():
    if not NEXUS_AUTH_ENABLED:
        return _render_auth_message(
            "Auth Disabled",
            "Auth onboarding is disabled in this environment.",
            status_code=404,
        )
    payload = request.get_json(silent=True) if request.is_json else {}
    payload = payload if isinstance(payload, dict) else {}
    session_id = str(request.form.get("session_id") or payload.get("session_id") or "").strip()
    api_key = str(request.form.get("api_key") or payload.get("api_key") or "").strip()
    if not session_id or not api_key:
        return _render_auth_message(
            "Invalid Request",
            "Both <code>session_id</code> and <code>api_key</code> are required.",
            status_code=400,
        )
    try:
        result = _svc_store_codex_api_key(session_id=session_id, api_key=api_key)
    except Exception as exc:
        return _render_auth_message("Credential Error", f"{exc}", status_code=400)

    ready = bool(result.get("ready"))
    grants = int(result.get("project_access_count") or 0)
    body = (
        "<p>✅ Setup completed successfully.</p>"
        if ready
        else "<p>⚠️ Credentials saved, but setup is not fully ready yet.</p>"
    )
    body += (
        f"<p>Project access count: <strong>{grants}</strong>.</p>"
        "<p>Go back to Discord or Telegram and run <code>/setup-status</code> or <code>/setup_status</code>.</p>"
    )
    return _render_auth_message("Setup Complete", body, status_code=200)


@app.route("/auth/ai-keys", methods=["POST"])
def auth_ai_keys():
    if not NEXUS_AUTH_ENABLED:
        return _render_auth_message(
            "Auth Disabled",
            "Auth onboarding is disabled in this environment.",
            status_code=404,
        )
    payload = request.get_json(silent=True) if request.is_json else {}
    payload = payload if isinstance(payload, dict) else {}
    session_id = str(request.form.get("session_id") or payload.get("session_id") or "").strip()
    codex_api_key = str(request.form.get("codex_api_key") or payload.get("codex_api_key") or "").strip()
    gemini_api_key = str(
        request.form.get("gemini_api_key") or payload.get("gemini_api_key") or ""
    ).strip()
    claude_api_key = str(
        request.form.get("claude_api_key") or payload.get("claude_api_key") or ""
    ).strip()
    raw_use_copilot = request.form.get("use_copilot")
    if raw_use_copilot is None:
        raw_use_copilot = payload.get("use_copilot")
    use_copilot = False
    if isinstance(raw_use_copilot, bool):
        use_copilot = raw_use_copilot
    elif raw_use_copilot is not None:
        use_copilot = str(raw_use_copilot).strip().lower() in {"1", "true", "yes", "on"}
    if not session_id:
        return _render_auth_message(
            "Invalid Request",
            "Field <code>session_id</code> is required.",
            status_code=400,
        )
    try:
        result = _svc_store_ai_provider_keys(
            session_id=session_id,
            codex_api_key=codex_api_key or None,
            gemini_api_key=gemini_api_key or None,
            claude_api_key=claude_api_key or None,
            allow_copilot=use_copilot,
        )
    except Exception as exc:
        return _render_auth_message("Credential Error", f"{exc}", status_code=400)

    ready = bool(result.get("ready"))
    grants = int(result.get("project_access_count") or 0)
    body = (
        "<p>✅ Setup completed successfully.</p>"
        if ready
        else "<p>⚠️ Credentials saved, but setup is not fully ready yet.</p>"
    )
    body += (
        f"<p>Project access count: <strong>{grants}</strong>.</p>"
        "<p>Go back to Discord or Telegram and run <code>/setup-status</code> or <code>/setup_status</code>.</p>"
    )
    return _render_auth_message("Setup Complete", body, status_code=200)


@app.route("/auth/result", methods=["GET"])
def auth_result():
    if not NEXUS_AUTH_ENABLED:
        return jsonify({"enabled": False, "status": "disabled"}), 200
    session_id = str(request.args.get("session", "")).strip()
    if not session_id:
        return jsonify({"status": "error", "message": "session query parameter is required"}), 400
    payload = _svc_get_session_and_setup_status(session_id)
    return jsonify(payload), 200


@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint."""
    _run_acl_sync_if_due()
    return jsonify({"status": "healthy", "service": "nexus-webhook", "version": "1.0.0"}), 200


@app.route("/completion", methods=["POST"])
def completion():
    """Push-based completion endpoint.

    Agents POST their completion JSON here instead of writing a file,
    enabling instant handoff without polling latency.

    Expected payload:
        {
            "issue_number": "42",
            "agent_type": "developer",
            "next_agent": "reviewer",
            "summary": "..."
        }
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
    except Exception:
        data = {}

    issue_number = str(data.get("issue_number", "")).strip()
    next_agent = str(data.get("next_agent", "")).strip()
    agent_type = str(data.get("agent_type", "unknown")).strip()
    data.get("summary", "")

    if not issue_number or not next_agent:
        return jsonify({"status": "error", "message": "issue_number and next_agent required"}), 400

    logger.info(
        f"📬 Push completion received: issue #{issue_number}, "
        f"agent={agent_type}, next={next_agent}"
    )

    try:
        pid, _ = launch_next_agent(issue_number, next_agent, trigger_source="push_completion")
        return (
            jsonify(
                {
                    "status": "queued" if pid else "skipped",
                    "issue_number": issue_number,
                    "next_agent": next_agent,
                }
            ),
            200,
        )
    except Exception as exc:
        logger.error(f"Failed to queue next agent from push completion: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/v1/completion", methods=["POST"])
def api_v1_completion():
    """Persist-and-acknowledge completion endpoint (postgres backend).

    Agents POST their completion JSON here when ``NEXUS_STORAGE_BACKEND``
    is ``postgres``.  The payload is persisted to the ``nexus_completions``
    table and acknowledged with ``201 Created``.

    The orchestrator loop (``scan_and_process_completions``) handles
    auto-chaining separately — this endpoint only persists.
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
    except Exception:
        return jsonify({"status": "error", "message": "Invalid JSON"}), 400

    issue_number = str(data.get("issue_number", "")).strip()
    agent_type = str(data.get("agent_type", "")).strip()

    if not issue_number or not agent_type:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "issue_number and agent_type are required",
                }
            ),
            400,
        )

    logger.info(
        "📬 API v1 completion received: issue #%s, agent=%s",
        issue_number,
        agent_type,
    )

    try:
        from nexus.core.completion_store import CompletionStore

        store = _get_completion_store()
        dedup_key = store.save(issue_number, agent_type, data)
        return (
            jsonify(
                {
                    "status": "created",
                    "issue_number": issue_number,
                    "agent_type": agent_type,
                    "dedup_key": dedup_key,
                }
            ),
            201,
        )
    except Exception as exc:
        logger.error("Failed to persist completion: %s", exc, exc_info=True)
        return jsonify({"status": "error", "message": str(exc)}), 500


def _get_completion_store():
    """Lazy-singleton for the CompletionStore."""
    if not hasattr(_get_completion_store, "_instance"):
        from nexus.core.completion_store import CompletionStore

        backend = NEXUS_STORAGE_BACKEND
        storage = None
        if backend == "postgres":
            from integrations.workflow_state_factory import get_storage_backend

            storage = get_storage_backend()

        _get_completion_store._instance = CompletionStore(
            backend=backend,
            storage=storage,
            base_dir=BASE_DIR,
        )
    return _get_completion_store._instance


@app.route("/webhook", methods=["POST"])
def webhook():
    """Main webhook endpoint for GitHub events."""
    _run_acl_sync_if_due()
    body, status = _process_webhook_request(
        payload_body=request.data,
        headers=dict(request.headers),
        payload_json=request.json,
        logger=logger,
        verify_signature=verify_signature,
        get_webhook_policy=_get_webhook_policy,
        handle_issue_opened=handle_issue_opened,
        handle_issue_comment=handle_issue_comment,
        handle_pull_request=handle_pull_request,
        handle_pull_request_review=handle_pull_request_review,
        emit_alert=emit_alert,
    )
    return jsonify(body), status


@app.route("/", methods=["GET"])
def index():
    """Root endpoint - basic info."""
    return (
        jsonify(
            {
                "service": "Nexus Nexus Webhook Server",
                "version": "1.0.0",
                "endpoints": {
                    "/webhook": "POST - Git webhook events",
                    "/health": "GET - Health check",
                    "/visualizer": "GET - Real-time workflow visualizer dashboard",
                    "/auth/start": "GET - Begin OAuth onboarding (GitHub/GitLab)",
                    "/auth/github/callback": "GET - GitHub OAuth callback",
                    "/auth/gitlab/callback": "GET - GitLab OAuth callback",
                    "/auth/codex-key": "POST - Save user Codex key",
                    "/auth/ai-keys": "POST - Save user AI provider keys",
                    "/auth/result": "GET - Onboarding session status",
                },
            }
        ),
        200,
    )


@app.route("/visualizer", methods=["GET"])
def visualizer():
    """Serve the real-time workflow visualizer dashboard."""
    return send_from_directory(app.static_folder, "visualizer.html")


@app.route("/visualizer/snapshot", methods=["GET"])
def visualizer_snapshot():
    """Return a snapshot payload for initial visualizer rendering."""
    records = _collect_visualizer_snapshot()
    return (
        jsonify(
            {
                "count": len(records),
                "workflows": records,
            }
        ),
        200,
    )


def main():
    """Start the webhook server."""
    port = WEBHOOK_PORT
    logger.info(f"🚀 Starting webhook server on port {port}")

    # Initialize event handlers (including SocketIO bridge)
    try:
        from orchestration.nexus_core_helpers import setup_event_handlers

        setup_event_handlers()
        logger.info("✅ Event handlers initialized")
    except Exception as e:
        logger.warning(f"⚠️ Could not initialize event handlers: {e}")

    logger.info(f"📍 Webhook URL: http://localhost:{port}/webhook")
    logger.info(f"📊 Visualizer: http://localhost:{port}/visualizer")

    if not WEBHOOK_SECRET:
        logger.warning("⚠️ WEBHOOK_SECRET not configured - signature verification disabled!")

    # Run with eventlet for WebSocket support
    socketio.run(
        app,
        host="0.0.0.0",
        port=port,
        debug=False,
        allow_unsafe_werkzeug=True,
    )


if __name__ == "__main__":
    main()
