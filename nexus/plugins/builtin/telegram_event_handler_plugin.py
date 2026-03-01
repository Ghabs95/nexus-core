"""Telegram Event Handler Plugin.

Subscribes to the EventBus and sends Telegram notifications on key
workflow lifecycle events. Registered as a ``PluginKind.EVENT_HANDLER``
plugin.

Usage::

    from nexus.core.events import EventBus
    from nexus.plugins.builtin.telegram_event_handler_plugin import TelegramEventHandler

    bus = EventBus()
    handler = TelegramEventHandler({
        "bot_token": "123:ABC...",
        "chat_id": "-100123456789",
    })
    handler.attach(bus)
"""

import logging
from typing import Any

from nexus.core.events import (
    AlertAction,
    AgentTimeout,
    NexusEvent,
    StepCompleted,
    StepFailed,
    SystemAlert,
    WorkflowFailed,
)
from nexus.plugins.builtin.base_chat_event_handler import BaseChatEventHandler

logger = logging.getLogger(__name__)

# Event → (emoji, label)
_EVENT_FORMAT: dict[str, tuple[str, str]] = {
    "workflow.started": ("🚀", "Workflow Started"),
    "workflow.completed": ("✅", "Workflow Completed"),
    "workflow.failed": ("❌", "Workflow Failed"),
    "workflow.cancelled": ("🛑", "Workflow Cancelled"),
    "step.completed": ("✔️", "Step Completed"),
    "step.failed": ("⚠️", "Step Failed"),
    "agent.timeout": ("⏰", "Agent Timeout"),
    "system.alert": ("🔔", "Alert"),
}

_SEVERITY_ICON: dict[str, str] = {
    "info": "ℹ️",
    "warning": "⚠️",
    "error": "❌",
    "critical": "🚨",
}


def _build_alert_keyboard(
    actions: list[AlertAction],
) -> dict[str, list[list[dict[str, str]]]] | None:
    """Build Telegram inline keyboard markup from alert actions."""
    if not actions:
        return None

    rows: list[list[dict[str, str]]] = []
    current_row: list[dict[str, str]] = []
    for action in actions:
        label = str(getattr(action, "label", "")).strip()
        callback_data = str(getattr(action, "callback_data", "")).strip()
        url = str(getattr(action, "url", "")).strip()
        if not label:
            continue
        if not callback_data and not url:
            continue
        button: dict[str, str] = {"text": label}
        if url:
            button["url"] = url
        else:
            button["callback_data"] = callback_data
        current_row.append(button)
        if len(current_row) == 2:
            rows.append(current_row)
            current_row = []

    if current_row:
        rows.append(current_row)
    if not rows:
        return None
    return {"inline_keyboard": rows}


class TelegramEventHandler(BaseChatEventHandler):
    """Sends Telegram messages when workflow events fire.

    Implements :class:`PluginLifecycle` via BaseChatEventHandler.
    """

    def __init__(self, config: dict[str, Any]):
        super().__init__("telegram-event-handler")
        from nexus.plugins.builtin.telegram_notification_plugin import (
            TelegramNotificationPlugin,
        )

        self._telegram = TelegramNotificationPlugin(config)
        self._chat_id = str(config.get("chat_id", ""))

    # -- Handler ------------------------------------------------------------

    async def _handle(self, event: NexusEvent) -> None:
        # SystemAlert uses its own format
        reply_markup = None
        if isinstance(event, SystemAlert):
            icon = _SEVERITY_ICON.get(event.severity, "ℹ️")
            lines = [f"{icon} {event.message}"]
            if event.source:
                lines.append(f"Source: `{event.source}`")
            if event.workflow_id:
                lines.append(f"Workflow: `{event.workflow_id}`")
            if event.project_key:
                lines.append(f"Project: `{event.project_key}`")
            if event.issue_number:
                lines.append(f"Issue: `#{event.issue_number}`")
            reply_markup = _build_alert_keyboard(getattr(event, "actions", []))
        else:
            emoji, label = _EVENT_FORMAT.get(event.event_type, ("📌", event.event_type))
            lines = [f"{emoji} *{label}*"]
            if event.workflow_id:
                lines.append(f"Workflow: `{event.workflow_id}`")

            # Add event-specific details
            if isinstance(event, WorkflowFailed):
                lines.append(f"Error: {event.error}")
            elif isinstance(event, StepFailed):
                lines.append(f"Step: {event.step_name} (#{event.step_num})")
                lines.append(f"Error: {event.error}")
            elif isinstance(event, StepCompleted):
                lines.append(f"Step: {event.step_name} (#{event.step_num})")
            elif isinstance(event, AgentTimeout):
                lines.append(f"Agent: {event.agent_name}")
                if event.pid:
                    lines.append(f"PID: {event.pid}")

            if event.data:
                for k, v in event.data.items():
                    lines.append(f"{k}: `{v}`")

        text = "\n".join(lines)

        try:
            ok = self._telegram.send_message_sync(
                text,
                parse_mode="Markdown",
                reply_markup=reply_markup,
            )
            self._last_send_ok = bool(ok)
        except Exception as exc:
            self._last_send_ok = False
            logger.error("TelegramEventHandler send failed: %s", exc)


# -- Plugin registration ---------------------------------------------------


def register_plugins(registry: Any) -> None:
    """Register Telegram event handler plugin."""
    from nexus.plugins.base import PluginKind

    registry.register_factory(
        kind=PluginKind.EVENT_HANDLER,
        name="telegram-event-handler",
        version="1.0.0",
        factory=lambda config: TelegramEventHandler(config),
        description="Sends Telegram notifications on workflow events via EventBus",
    )
