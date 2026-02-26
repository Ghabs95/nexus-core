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

import logging
import os
import re
import sys
import asyncio

from flask import Flask, jsonify, request, send_from_directory
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

from config import (
    BASE_DIR,
    LOGS_DIR,
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
    get_tasks_active_dir,
)
from integrations.notifications import (
    emit_alert,
    send_notification,
)
from orchestration.plugin_runtime import get_webhook_policy_plugin
from runtime.agent_launcher import launch_next_agent

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOGS_DIR, 'webhook.log')),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder=os.path.join(os.path.dirname(__file__), "static"))
socketio = SocketIO(app, async_mode="eventlet", cors_allowed_origins="*")

# Track processed events to avoid duplicates
processed_events = set()


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
            storage_type=("postgres" if NEXUS_WORKFLOW_BACKEND in {"postgres", "both"} else "file"),
            storage_config=(
                {"connection_string": NEXUS_STORAGE_DSN}
                if NEXUS_WORKFLOW_BACKEND in {"postgres", "both"} and NEXUS_STORAGE_DSN
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


def _get_webhook_policy():
    """Get framework webhook policy plugin."""
    return get_webhook_policy_plugin(cache_key="github-webhook-policy:webhook")


def _repo_to_project_key(repo_name: str) -> str:
    """Best-effort mapping from repository full_name to configured project key."""
    policy = _get_webhook_policy()
    return policy.resolve_project_key(
        repo_name,
        PROJECT_CONFIG,
        default_project=get_default_project(),
    )


def _effective_merge_policy(repo_name: str) -> str:
    """Resolve effective merge policy for a repo.

    Returns one of: always, workflow-based, never.
    """
    policy = _get_webhook_policy()
    return policy.resolve_merge_policy(repo_name, PROJECT_CONFIG, default_policy="always")


def _notify_lifecycle(message: str) -> bool:
    """Send lifecycle notification via abstract notifier, fallback to Telegram alert."""
    if send_notification(message):
        return True
    return emit_alert(message, severity="info", source="webhook_server")


def verify_signature(payload_body, signature_header):
    """Verify Git webhook signature."""
    policy = _get_webhook_policy()
    verified = bool(policy.verify_signature(payload_body, signature_header, WEBHOOK_SECRET))
    if not WEBHOOK_SECRET and verified:
        logger.warning("⚠️ WEBHOOK_SECRET not configured - accepting all requests (INSECURE!)")
    if not verified:
        logger.error("❌ Signature verification failed")
    return verified


def handle_issue_opened(payload, event):
    """
    Handle issues.opened events.
    
    Converts Git issue to a markdown task file in .nexus/inbox/<project>/
    for the inbox processor to route to the appropriate agent based on type.
    
    Agent types (abstract roles):
    - triage: Initial issue analysis and classification
    - escalation: High-priority/urgent issues (escalate to senior agent)
    - debug: Bug analysis and root cause
    
    The actual agent implementing each type is defined in the workflow YAML.
    """
    policy = _get_webhook_policy()
    action = event.get("action")
    issue_number = event.get("number", "")
    issue_title = event.get("title", "")
    issue_body = event.get("body", "")
    issue_author = event.get("author", "")
    issue_url = event.get("url", "")
    issue_labels = event.get("labels", [])
    repo_name = event.get("repo", "unknown")
    event.get("closed_by", "unknown")
    
    logger.info(f"📋 New issue: #{issue_number} - {issue_title} by {issue_author}")
    
    # Handle issue close notifications
    if action == "closed":
        message = policy.build_issue_closed_message(event)
        _notify_lifecycle(message)
        return {"status": "issue_closed_notified", "issue": issue_number}

    # Only process open actions for task creation
    if action != "opened":
        return {"status": "ignored", "reason": f"action is {action}, not opened"}
    
    # Skip issues created by Nexus itself (inbox processor → create_issue).
    # These already have an agent launched via the standard task processing path.
    # Detect via workflow labels that create_issue() always applies.
    workflow_labels = [l for l in issue_labels if l.startswith("workflow:")]
    if workflow_labels:
        logger.info(f"⏭️ Skipping self-created issue #{issue_number} (has workflow label: {workflow_labels})")
        return {"status": "ignored", "reason": "self-created issue (has workflow label)"}
    
    # Also skip if an active task file already exists for this issue
    try:
        for _key, _cfg in PROJECT_CONFIG.items():
            if isinstance(_cfg, dict) and repo_name in _project_repos(_key, _cfg, get_repos):
                _ws = os.path.join(BASE_DIR, _cfg.get("workspace", ""))
                _active = get_tasks_active_dir(_ws, _key)
                _task = os.path.join(_active, f"issue_{issue_number}.md")
                if os.path.exists(_task):
                    logger.info(f"⏭️ Skipping issue #{issue_number} — active task file already exists: {_task}")
                    return {"status": "ignored", "reason": "task file already exists"}
                break
    except Exception as e:
        logger.warning(f"Could not check for existing task file: {e}")
    
    # Determine which agent type to route to
    try:
        triage_config = PROJECT_CONFIG.get("issue_triage", {})
        agent_type = triage_config.get("default_agent_type", "triage")
        
        # Check for label-based override
        label_based = triage_config.get("label_based", {})
        for label in issue_labels:
            if label in label_based:
                agent_type = label_based[label]
                logger.info(f"  Label '{label}' → routing to agent_type: {agent_type}")
                break
        
        # Check for repo-specific override
        per_repo = triage_config.get("per_repo", {})
        if repo_name in per_repo:
            agent_type = per_repo[repo_name]
            logger.info(f"  Repository '{repo_name}' → routing to agent_type: {agent_type}")
        
    except Exception as e:
        logger.warning(f"⚠️ Could not load triage config, using default: {e}")
        triage_config = PROJECT_CONFIG.get("issue_triage", {})
        agent_type = triage_config.get("default_agent_type", "triage")
    
    # Create markdown task file for inbox processor
    try:
        from pathlib import Path
        
        # Determine project from repository name
        project_workspace = None
        project_key = None
        for project_key, project_cfg in PROJECT_CONFIG.items():
            if not isinstance(project_cfg, dict):
                continue
            project_repos = _project_repos(project_key, project_cfg, get_repos)
            if repo_name in project_repos:
                project_workspace = project_cfg.get("workspace")
                logger.info(
                    f"📌 Mapped repository '{repo_name}' → project '{project_key}' "
                    f"(workspace: {project_workspace})"
                )
                break
        
        if not project_workspace or not project_key:
            message = (
                f"🚫 No project mapping for repository '{repo_name}'. "
                "Webhook issue task creation blocked to enforce project boundaries."
            )
            logger.error(message)
            emit_alert(message, severity="warning", source="webhook_server")
            return {
                "status": "ignored",
                "reason": "unmapped_repository",
                "repository": repo_name,
                "issue": issue_number,
            }
        
        # Get inbox directory for the project's workspace
        workspace_abs = os.path.join(BASE_DIR, project_workspace)
        inbox_dir = get_inbox_dir(workspace_abs, project_key)
        Path(inbox_dir).mkdir(parents=True, exist_ok=True)
        
        # Create task filename (issue number based)
        task_file = Path(inbox_dir) / f"issue_{issue_number}.md"
        
        # Create markdown content with agent type and source metadata
        # The inbox processor will route this to the appropriate agent based on type
        # SOURCE=webhook tells inbox processor to skip Git issue creation (already exists)
        task_content = f"""# Issue #{issue_number}: {issue_title}

**From:** @{issue_author}  
**URL:** {issue_url}  
**Repository:** {repo_name}  
**Agent Type:** {agent_type}
**Source:** webhook
**Issue Number:** {issue_number}

## Description

{issue_body or "_(No description provided)_"}

## Labels

{', '.join([f"`{l}`" for l in issue_labels]) if issue_labels else "_None_"}

## Status: Ready for {agent_type} agent

This issue will be routed to the {agent_type} agent as defined in the workflow.
The actual agent assignment depends on the current project's workflow configuration.
"""
        
        # Write to file
        task_file.write_text(task_content)
        logger.info(f"✅ Created task file: {task_file} (agent_type: {agent_type})")

        message = policy.build_issue_created_message(event, agent_type)
        _notify_lifecycle(message)
        
        return {
            "status": "task_created",
            "issue": issue_number,
            "task_file": str(task_file),
            "title": issue_title,
            "agent_type": agent_type,
            "repository": repo_name
        }
        
    except Exception as e:
        logger.error(f"❌ Error creating task file for issue #{issue_number}: {e}", exc_info=True)
        emit_alert(f"Issue processing error for #{issue_number}: {str(e)}", severity="error", source="webhook_server")
        return {
            "status": "error",
            "issue": issue_number,
            "error": str(e)
        }


def handle_issue_comment(payload, event):
    """
    Handle issue_comment events.
    
    Detects workflow completion markers in comments and chains to next agent.
    """
    policy = _get_webhook_policy()
    action = event.get("action")
    comment_id = event.get("comment_id")
    comment_body = event.get("comment_body", "")
    issue_number = event.get("issue_number", "")
    comment_author = event.get("comment_author", "")
    issue = event.get("issue", {})
    
    logger.info(f"📝 Issue comment: #{issue_number} by {comment_author} (action: {action})")
    
    # Only process created comments
    if action != "created":
        return {"status": "ignored", "reason": f"action is {action}, not created"}
    
    # Ignore non-copilot comments
    if comment_author != "copilot":
        return {"status": "ignored", "reason": "not from copilot"}
    
    # Check if already processed
    event_key = f"comment_{comment_id}"
    if event_key in processed_events:
        logger.info(f"⏭️ Already processed comment {comment_id}")
        return {"status": "duplicate"}
    
    # Detect workflow completion
    completion_markers = [
        r"workflow\s+complete",
        r"ready\s+for\s+review",
        r"ready\s+to\s+merge",
        r"implementation\s+complete",
        r"all\s+steps\s+completed"
    ]
    
    import re
    is_completion = any(re.search(pattern, comment_body, re.IGNORECASE) 
                       for pattern in completion_markers)
    
    # Look for next agent mention
    next_agent_match = re.search(r'@(\w+)', comment_body)
    next_agent = next_agent_match.group(1) if next_agent_match else None
    
    if is_completion and not next_agent:
        # Workflow completed - check for PR and notify
        logger.info(f"✅ Workflow completion detected for issue #{issue_number}")
        
        # Determine project from issue labels or body
        project = policy.determine_project_from_issue(issue)
        
        # Check for linked PR and notify
        from inbox_processor import check_and_notify_pr
        check_and_notify_pr(issue_number, project)
        
        # Mark as processed
        processed_events.add(event_key)
        return {"status": "workflow_completed", "issue": issue_number}
    
    elif next_agent:
        # Chain to next agent
        logger.info(f"🔗 Chaining to @{next_agent} for issue #{issue_number}")
        
        try:
            pid, _ = launch_next_agent(
                issue_number=issue_number,
                next_agent=next_agent,
                trigger_source="webhook"
            )

            if pid:
                processed_events.add(event_key)
                return {
                    "status": "agent_launched",
                    "issue": issue_number,
                    "next_agent": next_agent
                }
            else:
                return {
                    "status": "launch_failed",
                    "issue": issue_number,
                    "next_agent": next_agent
                }
        except Exception as e:
            logger.error(f"❌ Failed to launch next agent: {e}")
            return {"status": "error", "message": str(e)}
    
    return {"status": "no_action"}


def handle_pull_request(payload, event):
    """Handle pull_request events (opened, synchronized, etc.)."""
    policy = _get_webhook_policy()
    action = event.get("action")
    pr_number = event.get("number")
    pr_title = event.get("title", "")
    pr_author = event.get("author", "")
    event.get("url", "")
    repo_name = event.get("repo", "unknown")
    merged = bool(event.get("merged"))
    event.get("merged_by", "unknown")
    
    logger.info(f"🔀 Pull request #{pr_number}: {action} by {pr_author}")

    if action == "opened":
        message = policy.build_pr_created_message(event)
        _notify_lifecycle(message)

        # Auto-queue the reviewer agent if PR title references an issue
        issue_match = re.search(r"#(\d+)", pr_title or "")
        if issue_match:
            referenced_issue = issue_match.group(1)
            logger.info(
                f"PR #{pr_number} references issue #{referenced_issue} — auto-queuing reviewer"
            )
            try:
                launch_next_agent(
                    referenced_issue, "reviewer", trigger_source="pr_opened"
                )
            except Exception as exc:
                logger.warning(f"Failed to auto-queue reviewer for issue #{referenced_issue}: {exc}")

        return {
            "status": "pr_opened_notified",
            "pr": pr_number,
            "action": action,
        }

    if action == "closed" and merged:
        merge_policy = _effective_merge_policy(repo_name)

        # Notify merge only when manual merge approval is not enforced.
        should_notify = policy.should_notify_pr_merged(merge_policy)
        if should_notify:
            message = policy.build_pr_merged_message(event, merge_policy)
            _notify_lifecycle(message)
            return {
                "status": "pr_merged_notified",
                "pr": pr_number,
                "action": action,
                "merge_policy": merge_policy,
            }

        logger.info(
            "Skipping PR merged notification for #%s due to manual merge policy '%s'",
            pr_number,
            merge_policy,
        )
        return {
            "status": "pr_merged_skipped_manual_review",
            "pr": pr_number,
            "action": action,
            "merge_policy": merge_policy,
        }
    
    # For now, just log - can add PR notifications later
    return {
        "status": "logged",
        "pr": pr_number,
        "action": action
    }


def handle_pull_request_review(payload, event):
    """Handle pull_request_review events."""
    pr_number = event.get("pr_number")
    review_state = event.get("review_state")
    reviewer = event.get("reviewer", "")
    
    logger.info(f"👀 PR review #{pr_number}: {review_state} by {reviewer}")
    
    # For now, just log - can add review notifications later
    return {
        "status": "logged",
        "pr": pr_number,
        "state": review_state
    }


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint."""
    return jsonify({
        "status": "healthy",
        "service": "nexus-webhook",
        "version": "1.0.0"
    }), 200


@app.route('/completion', methods=['POST'])
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
        pid, _ = launch_next_agent(
            issue_number, next_agent, trigger_source="push_completion"
        )
        return jsonify({
            "status": "queued" if pid else "skipped",
            "issue_number": issue_number,
            "next_agent": next_agent,
        }), 200
    except Exception as exc:
        logger.error(f"Failed to queue next agent from push completion: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route('/api/v1/completion', methods=['POST'])
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
        return jsonify({
            "status": "error",
            "message": "issue_number and agent_type are required",
        }), 400

    logger.info(
        "📬 API v1 completion received: issue #%s, agent=%s",
        issue_number, agent_type,
    )

    try:
        from nexus.core.completion_store import CompletionStore

        store = _get_completion_store()
        dedup_key = store.save(issue_number, agent_type, data)
        return jsonify({
            "status": "created",
            "issue_number": issue_number,
            "agent_type": agent_type,
            "dedup_key": dedup_key,
        }), 201
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


@app.route('/webhook', methods=['POST'])
def webhook():
    """Main webhook endpoint for GitHub events."""
    
    # Verify signature
    signature = request.headers.get('X-Hub-Signature-256')
    if not verify_signature(request.data, signature):
        logger.error("❌ Webhook signature verification failed")
        return jsonify({"error": "Invalid signature"}), 403
    
    # Parse event type
    event_type = request.headers.get('X-GitHub-Event')
    if not event_type:
        logger.error("❌ No X-GitHub-Event header")
        return jsonify({"error": "No event type"}), 400
    
    payload = request.json
    delivery_id = request.headers.get('X-GitHub-Delivery')
    
    logger.info(f"📨 Webhook received: {event_type} (delivery: {delivery_id})")
    
    # Route to appropriate handler
    try:
        policy = _get_webhook_policy()
        dispatched = policy.dispatch_event(event_type, payload)
        route = dispatched.get("route")
        event = dispatched.get("event", {})

        if route == "issues":
            result = handle_issue_opened(payload, event)
        elif route == "issue_comment":
            result = handle_issue_comment(payload, event)
        elif route == "pull_request":
            result = handle_pull_request(payload, event)
        elif route == "pull_request_review":
            result = handle_pull_request_review(payload, event)
        elif route == "ping":
            logger.info("🏓 Ping received")
            result = {"status": "pong"}
        else:
            logger.info(f"⏭️ Unhandled event type: {event_type}")
            result = {"status": "unhandled", "event_type": event_type}
        
        return jsonify(result), 200
    
    except Exception as e:
        logger.error(f"❌ Error processing webhook: {e}", exc_info=True)
        emit_alert(f"Webhook Error: {str(e)}", severity="error", source="webhook_server")
        return jsonify({"error": str(e)}), 500


@app.route('/', methods=['GET'])
def index():
    """Root endpoint - basic info."""
    return jsonify({
        "service": "Nexus Nexus Webhook Server",
        "version": "1.0.0",
        "endpoints": {
            "/webhook": "POST - Git webhook events",
            "/health": "GET - Health check",
            "/visualizer": "GET - Real-time workflow visualizer dashboard"
        }
    }), 200


@app.route('/visualizer', methods=['GET'])
def visualizer():
    """Serve the real-time workflow visualizer dashboard."""
    return send_from_directory(app.static_folder, "visualizer.html")


@app.route('/visualizer/snapshot', methods=['GET'])
def visualizer_snapshot():
    """Return a snapshot payload for initial visualizer rendering."""
    records = _collect_visualizer_snapshot()
    return jsonify({
        "count": len(records),
        "workflows": records,
    }), 200


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
        host='0.0.0.0',
        port=port,
        debug=False,
    )


if __name__ == "__main__":
    main()
