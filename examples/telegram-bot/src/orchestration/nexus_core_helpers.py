"""
Nexus-Core Framework Integration Helpers.

This module provides integration between the original Nexus bot
and the nexus-core workflow framework.
"""
import logging
import os
from typing import Any

from nexus.adapters.git.github import GitHubPlatform
from nexus.adapters.git.gitlab import GitLabPlatform
from nexus.adapters.storage.file import FileStorage
from nexus.core.events import EventBus
from nexus.core.workflow import WorkflowEngine

from audit_store import AuditStore
from config import (
    BASE_DIR,
    NEXUS_CORE_STORAGE_DIR,
    _get_project_config,
    get_default_project,
    get_repo,
    get_gitlab_base_url,
    get_project_platform,
)
from orchestration.plugin_runtime import get_workflow_state_plugin


logger = logging.getLogger(__name__)

# Singleton EventBus shared across the host application
_event_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Get or create the global EventBus instance."""
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus


def get_workflow_engine() -> WorkflowEngine:
    """Get initialized workflow engine instance."""
    storage = FileStorage(base_path=NEXUS_CORE_STORAGE_DIR)
    return WorkflowEngine(storage=storage, event_bus=get_event_bus())


def setup_event_handlers() -> None:
    """Attach event handler plugins to the shared EventBus.

    Reads config from environment variables:
        - TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID → Telegram handler
        - DISCORD_WEBHOOK_URL → Discord handler

    Safe to call multiple times; handlers are only attached once.
    """
    bus = get_event_bus()

    # Already initialized?
    if getattr(setup_event_handlers, "_done", False):
        return
    setup_event_handlers._done = True  # type: ignore[attr-defined]

    import os

    # Telegram
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    tg_chat = os.getenv("TELEGRAM_CHAT_ID", "")
    if tg_token and tg_chat:
        try:
            from nexus.plugins.builtin.telegram_event_handler_plugin import TelegramEventHandler
            handler = TelegramEventHandler({"bot_token": tg_token, "chat_id": tg_chat})
            handler.attach(bus)
            logger.info("Telegram event handler attached to EventBus")
        except Exception as exc:
            logger.warning("Failed to setup Telegram event handler: %s", exc)

    # Discord
    dc_webhook = os.getenv("DISCORD_WEBHOOK_URL", "")
    dc_channel = os.getenv("DISCORD_ALERT_CHANNEL_ID", "")
    if dc_webhook or dc_channel:
        try:
            from nexus.plugins.builtin.discord_event_handler_plugin import DiscordEventHandler
            handler = DiscordEventHandler({
                "webhook_url": dc_webhook or None,
                "alert_channel_id": dc_channel or None,
            })
            handler.attach(bus)
            logger.info("Discord event handler attached to EventBus")
        except Exception as exc:
            logger.warning("Failed to setup Discord event handler: %s", exc)


def get_git_platform(repo: str = None, project_name: str = None):
    """Get initialized Git platform adapter for the project.

    Returns either :class:`GitPlatform` or :class:`GitLabPlatform`.
    """
    project_key = project_name or get_default_project()
    repo_name = repo or get_repo(project_key)
    platform_type = get_project_platform(project_key)

    project_config = _get_project_config().get(project_key, {})
    default_token_var = "GITLAB_TOKEN" if platform_type == "gitlab" else "GITHUB_TOKEN"
    token_var = project_config.get("git_token_var_name", default_token_var)
    token = os.getenv(token_var)

    if platform_type == "gitlab":
        if not token:
            raise ValueError(
                f"{token_var} is required for gitlab projects. "
                f"Missing token for project '{project_key}'."
            )
        return GitLabPlatform(
            token=token,
            repo=repo_name,
            base_url=get_gitlab_base_url(project_key),
        )

    if not token:
        logger.warning(f"{token_var} is missing for project '{project_key}'. Git operations may fail.")
    return GitHubPlatform(repo=repo_name, token=token)


def get_workflow_definition_path(project_name: str) -> str | None:
    """Get workflow definition path for a project with fallback logic.
    
    Priority:
    1. Project-specific override in PROJECT_CONFIG
    2. Global workflow_definition_path in PROJECT_CONFIG
    3. None (caller must abort)
    
    Args:
        project_name: Project name (e.g., 'nexus')
        
    Returns:
        Absolute path to workflow YAML file, or None if not configured
    """
    config = _get_project_config()
    
    # Check project-specific override
    if project_name in config:
        project_config = config[project_name]
        if isinstance(project_config, dict) and "workflow_definition_path" in project_config:
            path = project_config["workflow_definition_path"]
            # Resolve relative paths to absolute
            if path and not os.path.isabs(path):
                path = os.path.join(BASE_DIR, path)
            return path
    
    # Check global workflow_definition_path
    if "workflow_definition_path" in config:
        path = config["workflow_definition_path"]
        # Resolve relative paths to absolute  
        if path and not os.path.isabs(path):
            path = os.path.join(BASE_DIR, path)
        return path
    
    # No workflow definition found
    return None


from integrations.workflow_state_factory import get_workflow_state as _get_wf_state

_WORKFLOW_STATE_PLUGIN_BASE_KWARGS = {
    "storage_dir": NEXUS_CORE_STORAGE_DIR,
    "issue_to_workflow_id": lambda n: _get_wf_state().get_workflow_id(n),
    "issue_to_workflow_map_setter": lambda n, w: _get_wf_state().map_issue(n, w),
    "workflow_definition_path_resolver": get_workflow_definition_path,
    "set_pending_approval": lambda *a: _get_wf_state().set_pending_approval(*a),
    "clear_pending_approval": lambda n: _get_wf_state().clear_pending_approval(n),
    "audit_log": AuditStore.audit_log,
}
_WORKFLOW_STATE_PLUGIN_CACHE_KEY = "workflow:state-engine"


async def create_workflow_for_issue(
    issue_number: str,
    issue_title: str,
    project_name: str,
    tier_name: str,
    task_type: str,
    description: str = ""
) -> str | None:
    """
    Create a nexus-core workflow for a Git issue.
    
    Args:
        issue_number: Git issue number
        issue_title: Issue title (slug)
        project_name: Project name (e.g., 'nxs')
        tier_name: Workflow tier (tier-1-simple, tier-2-standard, etc.)
        task_type: Task type (feature, bug, hotfix, etc.)
        description: Task description
    
    Returns:
        workflow_id if successful, None otherwise
    """
    workflow_plugin = get_workflow_state_plugin(
        **_WORKFLOW_STATE_PLUGIN_BASE_KWARGS,
        repo_key=get_repo(project_name),
        cache_key=_WORKFLOW_STATE_PLUGIN_CACHE_KEY,
    )

    workflow_id = await workflow_plugin.create_workflow_for_issue(
        issue_number=issue_number,
        issue_title=issue_title,
        project_name=project_name,
        tier_name=tier_name,
        task_type=task_type,
        description=description,
    )

    if workflow_id:
        return workflow_id

    workflow_definition_path = get_workflow_definition_path(project_name)
    if not workflow_definition_path:
        msg = (
            f"No workflow_definition_path configured for project '{project_name}'. "
            "Cannot create workflow without a YAML definition."
        )
        logger.error(msg)
        from integrations.notifications import emit_alert
        emit_alert(f"❌ {msg}", severity="error", source="nexus_core_helpers")
    elif not os.path.exists(workflow_definition_path):
        msg = (
            f"Workflow definition not found at: {workflow_definition_path} "
            f"(project: {project_name})"
        )
        logger.error(msg)
        from integrations.notifications import emit_alert
        emit_alert(f"❌ {msg}", severity="error", source="nexus_core_helpers")
    return None


async def start_workflow(workflow_id: str, issue_number: str = None) -> bool:
    """
    Start a workflow.
    
    Args:
        workflow_id: Workflow ID
        issue_number: Optional issue number for Git comment
    
    Returns:
        True if successful
    """
    workflow_plugin = get_workflow_state_plugin(
        **_WORKFLOW_STATE_PLUGIN_BASE_KWARGS,
        cache_key=_WORKFLOW_STATE_PLUGIN_CACHE_KEY,
    )

    success = await workflow_plugin.start_workflow(workflow_id)
    if success and issue_number:
        logger.info(f"Started workflow {workflow_id} for issue #{issue_number}")
    return success


async def pause_workflow(issue_number: str, reason: str = "User requested") -> bool:
    """
    Pause a workflow by issue number.
    
    Args:
        issue_number: Git issue number
        reason: Reason for pausing
    
    Returns:
        True if successful
    """
    workflow_plugin = get_workflow_state_plugin(
        **_WORKFLOW_STATE_PLUGIN_BASE_KWARGS,
        cache_key=_WORKFLOW_STATE_PLUGIN_CACHE_KEY,
    )
    return await workflow_plugin.pause_workflow(issue_number, reason=reason)


async def resume_workflow(issue_number: str) -> bool:
    """
    Resume a paused workflow by issue number.
    
    Args:
        issue_number: Git issue number
    
    Returns:
        True if successful
    """
    workflow_plugin = get_workflow_state_plugin(
        **_WORKFLOW_STATE_PLUGIN_BASE_KWARGS,
        cache_key=_WORKFLOW_STATE_PLUGIN_CACHE_KEY,
    )
    return await workflow_plugin.resume_workflow(issue_number)


async def get_workflow_status(issue_number: str) -> dict | None:
    """
    Get workflow status for an issue.
    
    Args:
        issue_number: Git issue number
    
    Returns:
        Dict with workflow status or None
    """
    workflow_plugin = get_workflow_state_plugin(
        **_WORKFLOW_STATE_PLUGIN_BASE_KWARGS,
        cache_key=_WORKFLOW_STATE_PLUGIN_CACHE_KEY,
    )
    return await workflow_plugin.get_workflow_status(issue_number)


async def handle_approval_gate(
    workflow_id: str,
    issue_number: str,
    step_num: int,
    step_name: str,
    agent_name: str,
    approvers: list[str],
    approval_timeout: int,
    project: str = "nexus",
) -> None:
    """
    Called after complete_step when the next step has approval_required=True.
    Persists the pending approval and sends a Telegram notification.
    
    Args:
        workflow_id: The workflow ID (for reference)
        issue_number: Git issue number
        step_num: Step number awaiting approval
        step_name: Step name awaiting approval
        agent_name: Agent that will run the step when approved
        approvers: List of required approvers
        approval_timeout: Timeout in seconds
        project: Project name
    """
    def _notify_approval_required(**kwargs):
        from integrations.notifications import notify_approval_required

        notify_approval_required(**kwargs)

    workflow_plugin = get_workflow_state_plugin(
        **_WORKFLOW_STATE_PLUGIN_BASE_KWARGS,
        notify_approval_required=_notify_approval_required,
        cache_key=_WORKFLOW_STATE_PLUGIN_CACHE_KEY,
    )

    await workflow_plugin.request_approval_gate(
        workflow_id=workflow_id,
        issue_number=issue_number,
        step_num=step_num,
        step_name=step_name,
        agent_name=agent_name,
        approvers=approvers,
        approval_timeout=approval_timeout,
        project=project,
    )

    logger.info(
        f"Approval gate triggered for issue #{issue_number} "
        f"step {step_num} ({step_name})."
    )


async def complete_step_for_issue(
    issue_number: str,
    completed_agent_type: str,
    outputs: dict[str, Any],
    event_id: str = "",
):
    """Mark the current running step for *issue_number* as complete.

    Delegates to ``WorkflowStateEnginePlugin.complete_step_for_issue()``.
    The engine evaluates router steps automatically, handling conditional
    branches and review/develop loops.

    Args:
        issue_number: Git issue number.
        completed_agent_type: The ``agent_type`` that just finished.
        outputs: Structured outputs from the completion summary (use
            ``CompletionSummary.to_dict()`` or pass a raw dict).
        event_id: Optional deduplication token (comment id / completion hash).

    Returns:
        Updated :class:`~nexus.core.models.Workflow` (inspect ``.state`` and
        ``.active_agent_type`` to determine what to do next), or ``None``
        when no workflow is mapped to the issue.
    """
    workflow_plugin = get_workflow_state_plugin(
        **_WORKFLOW_STATE_PLUGIN_BASE_KWARGS,
        cache_key=_WORKFLOW_STATE_PLUGIN_CACHE_KEY,
    )
    return await workflow_plugin.complete_step_for_issue(
        issue_number=str(issue_number),
        completed_agent_type=completed_agent_type,
        outputs=outputs,
        event_id=event_id,
    )
