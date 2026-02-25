"""Enhanced notifications with inline keyboards for Nexus.

Provides rich Telegram notifications with interactive buttons for quick actions.
"""
import logging
import re
from typing import Sequence

from config import TELEGRAM_CHAT_ID, TELEGRAM_TOKEN, get_github_repo
from orchestration.plugin_runtime import get_profiled_plugin

logger = logging.getLogger(__name__)


def _normalize_telegram_markdown(message: str, parse_mode: str) -> str:
    """Normalize markdown for Telegram legacy Markdown mode.

    Telegram's legacy Markdown expects *bold* rather than **bold**.
    """
    if parse_mode != "Markdown" or not message:
        return message
    return re.sub(r"\*\*(.+?)\*\*", r"*\1*", message)


def _get_notification_plugin():
    """Return shared Telegram notification plugin instance."""
    return get_profiled_plugin(
        "notification_telegram",
        overrides={
            "bot_token": TELEGRAM_TOKEN,
            "chat_id": TELEGRAM_CHAT_ID,
        },
        cache_key="notification:telegram-default",
    )


class InlineKeyboard:
    """Builder for Telegram inline keyboards."""
    
    def __init__(self):
        """Initialize keyboard builder."""
        self.rows: list[list[dict]] = []
    
    def add_button(self, text: str, callback_data: str | None = None, url: str | None = None):
        """
        Add a button to the current row.
        
        Args:
            text: Button label text
            callback_data: Callback data for button press (ignored if url provided)
            url: Optional URL to open (makes button a URL button)
        
        Returns:
            Self for chaining
        """
        if not self.rows:
            self.rows.append([])
        
        button = {"text": text}
        if url:
            button["url"] = url
        elif callback_data:
            button["callback_data"] = callback_data
        else:
            raise ValueError("Either callback_data or url must be provided")
        
        self.rows[-1].append(button)
        return self
    
    def new_row(self):
        """Start a new row of buttons."""
        self.rows.append([])
        return self
    
    def build(self) -> dict:
        """
        Build the keyboard structure.
        
        Returns:
            Inline keyboard markup dict
        """
        return {"inline_keyboard": self.rows}


def send_notification(
    message: str,
    parse_mode: str = "Markdown",
    keyboard: InlineKeyboard | None = None
) -> bool:
    """
    Send a notification to Telegram with optional inline keyboard.
    
    Args:
        message: Message text
        parse_mode: Parse mode (Markdown or HTML)
        keyboard: Optional inline keyboard
    
    Returns:
        True if sent successfully
    """
    plugin = _get_notification_plugin()
    message = _normalize_telegram_markdown(message, parse_mode)
    reply_markup = keyboard.build() if keyboard else None

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not configured, skipping notification")
        return False
    if not plugin:
        logger.error("Telegram notification plugin unavailable")
        return False

    try:
        if not hasattr(plugin, "send_message_sync"):
            logger.error("Telegram notification plugin missing send_message_sync")
            return False

        return bool(
            plugin.send_message_sync(
                message=message,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
        )
    except Exception as e:
        logger.error(f"Failed to send notification: {e}")
        return False


def notify_agent_needs_input(issue_number: str, agent: str, preview: str, project: str = "nexus") -> bool:
    """
    Send notification that an agent needs input.
    
    Args:
        issue_number: GitHub issue number
        agent: Agent name
        preview: Preview of the agent's question
        project: Project name (default: nexus)
    
    Returns:
        True if sent successfully
    """
    message = (
        f"📋 **Agent Needs Input**\n\n"
        f"Issue: #{issue_number}\n"
        f"Agent: @{agent}\n\n"
        f"Preview:\n{preview}"
    )
    
    keyboard = (
        InlineKeyboard()
        .add_button("📝 View Full", callback_data=f"logs_{issue_number}")
        .add_button("🔗 GitHub", url=f"https://github.com/{get_github_repo(project)}/issues/{issue_number}")
        .new_row()
        .add_button("✍️ Respond", callback_data=f"respond_{issue_number}")
    )
    
    return send_notification(message, keyboard=keyboard)


def notify_workflow_started(issue_number: str, project: str, tier: str, task_type: str) -> bool:
    """
    Send notification that a workflow has started.
    
    Args:
        issue_number: GitHub issue number
        project: Project name
        tier: Workflow tier (full, shortened, fast-track)
        task_type: Task type (feature, bug, hotfix, etc.)
    
    Returns:
        True if sent successfully
    """
    tier_emoji = {
        "full": "🟡",
        "shortened": "🟠",
        "fast-track": "🟢"
    }
    
    message = (
        f"🚀 **Workflow Started**\n\n"
        f"Issue: #{issue_number}\n"
        f"Project: {project}\n"
        f"Type: {task_type}\n"
        f"Tier: {tier_emoji.get(tier, '⚪')} {tier}"
    )
    
    keyboard = (
        InlineKeyboard()
        .add_button("👀 Logs", callback_data=f"logs_{issue_number}")
        .add_button("📊 Status", callback_data=f"status_{issue_number}")
        .new_row()
        .add_button("🔗 GitHub", url=f"https://github.com/{get_github_repo(project)}/issues/{issue_number}")
        .add_button("⏸️ Pause", callback_data=f"pause_{issue_number}")
    )
    
    return send_notification(message, keyboard=keyboard)


def notify_agent_completed(
    issue_number: str,
    completed_agent: str | None = None,
    next_agent: str = "",
    project: str = "nexus",
    agent_name: str | None = None,
) -> bool:
    """
    Send notification that an agent completed and next one started.
    
    Args:
        issue_number: GitHub issue number
        completed_agent: Agent that just completed
        next_agent: Agent that's starting next
        project: Project name (default: nexus)
        agent_name: Legacy alias for completed_agent
    
    Returns:
        True if sent successfully
    """
    resolved_completed_agent = completed_agent or agent_name or "agent"
    resolved_next_agent = next_agent or "unknown"

    message = (
        f"✅ **Agent Completed → Auto-Chain**\n\n"
        f"Issue: #{issue_number}\n"
        f"Completed: @{resolved_completed_agent}\n"
        f"Next: @{resolved_next_agent}"
    )
    
    keyboard = (
        InlineKeyboard()
        .add_button("📝 View Logs", callback_data=f"logs_{issue_number}")
        .add_button("🔗 GitHub", url=f"https://github.com/{get_github_repo(project)}/issues/{issue_number}")
        .new_row()
        .add_button("⏸️ Pause Chain", callback_data=f"pause_{issue_number}")
        .add_button("🛑 Stop", callback_data=f"stop_{issue_number}")
    )
    
    return send_notification(message, keyboard=keyboard)


def notify_agent_timeout(issue_number: str, agent: str, will_retry: bool, project: str = "nexus") -> bool:
    """
    Send notification about agent timeout.
    
    Args:
        issue_number: GitHub issue number
        agent: Agent name
        will_retry: Whether the agent will be retried
        project: Project name (default: nexus)
    
    Returns:
        True if sent successfully
    """
    if will_retry:
        message = (
            f"⚠️ **Agent Timeout → Retrying**\n\n"
            f"Issue: #{issue_number}\n"
            f"Agent: @{agent}\n"
            f"Status: Process killed, retry scheduled"
        )
        
        keyboard = (
            InlineKeyboard()
            .add_button("📝 View Logs", callback_data=f"logs_{issue_number}")
            .add_button("🔗 GitHub", url=f"https://github.com/{get_github_repo(project)}/issues/{issue_number}")
            .new_row()
            .add_button("🔄 Reprocess Now", callback_data=f"reprocess_{issue_number}")
            .add_button("🛑 Stop", callback_data=f"stop_{issue_number}")
        )
    else:
        message = (
            f"❌ **Agent Failed → Max Retries**\n\n"
            f"Issue: #{issue_number}\n"
            f"Agent: @{agent}\n"
            f"Status: Manual intervention required"
        )
        
        keyboard = (
            InlineKeyboard()
            .add_button("📝 View Logs", callback_data=f"logs_{issue_number}")
            .add_button("🔗 GitHub", url=f"https://github.com/{get_github_repo(project)}/issues/{issue_number}")
            .new_row()
            .add_button("🔄 Reprocess", callback_data=f"reprocess_{issue_number}")
            .add_button("🛑 Stop Workflow", callback_data=f"stop_{issue_number}")
        )
    
    return send_notification(message, keyboard=keyboard)


def notify_workflow_completed(
    issue_number: str,
    project: str,
    pr_urls: Sequence[str] | None = None,
) -> bool:
    """
    Send notification that a workflow completed successfully.
    
    Args:
        issue_number: GitHub issue number
        project: Project name
        pr_urls: Optional PR URLs if found
    
    Returns:
        True if sent successfully
    """
    normalized_pr_urls = [str(url) for url in (pr_urls or []) if str(url).strip()]
    if normalized_pr_urls:
        first_pr_url = normalized_pr_urls[0]
        pr_lines = "\n".join(f"🔗 PR: {url}" for url in normalized_pr_urls)
        message = (
            f"🎉 **Workflow Completed**\n\n"
            f"Issue: #{issue_number}\n"
            f"Project: {project}\n"
            f"PRs: {len(normalized_pr_urls)}\n\n"
            f"All workflow steps completed. **Ready for review!**\n\n"
            f"🔗 Issue: https://github.com/{get_github_repo(project)}/issues/{issue_number}\n"
            f"{pr_lines}"
        )
        
        keyboard = (
            InlineKeyboard()
            .add_button("🔗 View PR", url=first_pr_url)
            .add_button("🔗 View Issue", url=f"https://github.com/{get_github_repo(project)}/issues/{issue_number}")
            .new_row()
            .add_button("✅ Approve", callback_data=f"approve_{issue_number}")
            .add_button("📝 Request Changes", callback_data=f"reject_{issue_number}")
            .new_row()
            .add_button("📝 Full Logs", callback_data=f"logsfull_{issue_number}")
            .add_button("📊 Audit", callback_data=f"audit_{issue_number}")
        )
    else:
        message = (
            f"🎉 **Workflow Completed**\n\n"
            f"Issue: #{issue_number}\n"
            f"Project: {project}\n"
            f"Status: All agents finished\n\n"
            f"⚠️ No PR found - implementation may be in progress."
        )
        
        keyboard = (
            InlineKeyboard()
            .add_button("📝 View Full Logs", callback_data=f"logsfull_{issue_number}")
            .add_button("🔗 GitHub", url=f"https://github.com/{get_github_repo(project)}/issues/{issue_number}")
            .new_row()
            .add_button("📊 View Audit Trail", callback_data=f"audit_{issue_number}")
        )
    
    return send_notification(message, keyboard=keyboard)


def notify_implementation_requested(issue_number: str, requester: str, project: str = "nexus") -> bool:
    """
    Send notification that implementation was requested.
    
    Args:
        issue_number: GitHub issue number
        requester: Who requested the implementation
        project: Project name (default: nexus)
    
    Returns:
        True if sent successfully
    """
    message = (
        f"🛠️ **Implementation Requested**\n\n"
        f"Issue: #{issue_number}\n"
        f"Requester: {requester}\n"
        f"Status: Awaiting approval"
    )
    
    keyboard = (
        InlineKeyboard()
        .add_button("✅ Approve", callback_data=f"approve_{issue_number}")
        .add_button("❌ Reject", callback_data=f"reject_{issue_number}")
        .new_row()
        .add_button("📝 View Details", callback_data=f"logs_{issue_number}")
        .add_button("🔗 GitHub", url=f"https://github.com/{get_github_repo(project)}/issues/{issue_number}")
    )
    
    return send_notification(message, keyboard=keyboard)


def notify_approval_required(
    issue_number: str,
    step_num: int,
    step_name: str,
    agent: str,
    approvers: list[str],
    project: str = "nexus",
) -> bool:
    """
    Send notification that a workflow step is awaiting human approval.
    
    Args:
        issue_number: GitHub issue number
        step_num: Step number requiring approval
        step_name: Step name requiring approval
        agent: Agent that would execute the step
        approvers: List of required approvers (shown informatively)
        project: Project name (default: nexus)
    
    Returns:
        True if sent successfully
    """
    approvers_text = ", ".join(f"@{a}" for a in approvers) if approvers else "any admin"
    message = (
        f"⏳ **Approval Required**\n\n"
        f"Issue: #{issue_number}\n"
        f"Step {step_num}: {step_name}\n"
        f"Agent: @{agent}\n"
        f"Approvers: {approvers_text}\n\n"
        f"Approve to let the workflow continue, or deny to stop it."
    )

    keyboard = (
        InlineKeyboard()
        .add_button(
            "✅ Approve",
            callback_data=f"wfapprove_{issue_number}_{step_num}",
        )
        .add_button(
            "❌ Deny",
            callback_data=f"wfdeny_{issue_number}_{step_num}",
        )
        .new_row()
        .add_button(
            "🔗 GitHub",
            url=f"https://github.com/{get_github_repo(project)}/issues/{issue_number}",
        )
    )

    return send_notification(message, keyboard=keyboard)


# ---------------------------------------------------------------------------
# EventBus-based alerting
# ---------------------------------------------------------------------------

def emit_alert(
    message: str,
    severity: str = "info",
    source: str = "",
    workflow_id: str | None = None,
) -> bool:
    """Emit a :class:`SystemAlert` on the shared EventBus.

    All attached event-handler plugins (Telegram, Discord, …) will receive
    the alert.  If the EventBus has no ``system.alert`` subscribers, the
    function falls back to the direct Telegram plugin so that alerts are
    never silently swallowed.

    Args:
        message:     Plain-text alert body.
        severity:    ``info``, ``warning``, ``error``, or ``critical``.
        source:      Originating module name (informational).
        workflow_id: Optional workflow context.

    Returns:
        ``True`` if the alert was delivered by at least one channel.
    """
    import asyncio

    from nexus.core.events import SystemAlert

    try:
        from orchestration.nexus_core_helpers import get_event_bus
        bus = get_event_bus()
    except Exception:
        bus = None

    # Try EventBus path first
    if bus and bus.subscriber_count("system.alert") > 0:
        event = SystemAlert(
            message=message,
            severity=severity,
            source=source,
            workflow_id=workflow_id,
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        try:
            if loop and loop.is_running():
                # Already in an async context — schedule as a task
                loop.create_task(bus.emit(event))
            else:
                asyncio.run(bus.emit(event))
            return True
        except Exception as exc:
            logger.warning("EventBus emit failed, falling back to direct send: %s", exc)

    # Fallback: direct Telegram plugin (guarantees alert delivery)
    plugin = _get_notification_plugin()
    normalized = _normalize_telegram_markdown(message, "Markdown")
    if plugin:
        try:
            if hasattr(plugin, "send_alert_sync"):
                return bool(plugin.send_alert_sync(normalized, severity=severity))
            if hasattr(plugin, "send_message_sync"):
                return bool(plugin.send_message_sync(normalized, parse_mode="Markdown"))
        except Exception as exc:
            logger.warning("Direct Telegram alert failed: %s", exc)

    logger.warning("No alert channel available: %s", message[:120])
    return False

