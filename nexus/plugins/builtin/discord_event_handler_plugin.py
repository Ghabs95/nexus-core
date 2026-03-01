"""Discord Event Handler Plugin.

Subscribes to the EventBus and sends Discord embed notifications on key
workflow lifecycle events. Registered as a ``PluginKind.EVENT_HANDLER``
plugin.

Uses the existing :class:`DiscordNotificationChannel` adapter, which
supports both webhook-only and bot-token modes.

Usage::

    from nexus.core.events import EventBus
    from nexus.plugins.builtin.discord_event_handler_plugin import DiscordEventHandler

    bus = EventBus()
    handler = DiscordEventHandler({
        "webhook_url": "https://discord.com/api/webhooks/...",
        "alert_channel_id": "123456789",
    })
    handler.attach(bus)
"""

import logging
from typing import Any

from nexus.adapters.notifications.base import Button, Message
from nexus.core.events import (
    AlertAction,
    AgentTimeout,
    NexusEvent,
    StepCompleted,
    StepFailed,
    SystemAlert,
    WorkflowCancelled,
    WorkflowFailed,
)
from nexus.core.models import Severity
from nexus.plugins.builtin.base_chat_event_handler import BaseChatEventHandler

logger = logging.getLogger(__name__)

# Event type → (colour hex, emoji, label)
_EVENT_FORMAT: dict[str, tuple[int, str, str]] = {
    "workflow.started": (0x36A64F, "🚀", "Workflow Started"),
    "workflow.completed": (0x36A64F, "✅", "Workflow Completed"),
    "workflow.failed": (0xFF0000, "❌", "Workflow Failed"),
    "workflow.cancelled": (0xFF6B35, "🛑", "Workflow Cancelled"),
    "step.completed": (0x36A64F, "✔️", "Step Completed"),
    "step.failed": (0xFFB347, "⚠️", "Step Failed"),
    "agent.timeout": (0xFF6B35, "⏰", "Agent Timeout"),
    "system.alert": (0x808080, "🔔", "Alert"),
}

_SEVERITY_MAP: dict[str, Severity] = {
    "info": Severity.INFO,
    "warning": Severity.WARNING,
    "error": Severity.ERROR,
    "critical": Severity.ERROR,
}


class DiscordEventHandler(BaseChatEventHandler):
    """Sends Discord embed notifications when workflow events fire.

    Wraps :class:`DiscordNotificationChannel` and subscribes to the
    EventBus via BaseChatEventHandler. Implements ``PluginLifecycle`` for
    health checks.
    """

    def __init__(self, config: dict[str, Any]):
        super().__init__("discord-event-handler")
        from nexus.adapters.notifications.discord import DiscordNotificationChannel

        self._discord = DiscordNotificationChannel(
            webhook_url=config.get("webhook_url"),
            bot_token=config.get("bot_token"),
            alert_channel_id=config.get("alert_channel_id"),
        )
        self._alert_channel_id = config.get("alert_channel_id", "")

    # -- Handler ------------------------------------------------------------

    async def _handle(self, event: NexusEvent) -> None:
        colour, emoji, label = _EVENT_FORMAT.get(
            event.event_type, (0x808080, "📌", event.event_type)
        )
        send_as_message = False
        buttons: list[Button] = []

        # SystemAlert uses its own format
        if isinstance(event, SystemAlert):
            severity = _SEVERITY_MAP.get(event.severity, Severity.INFO)
            lines = [event.message]
            if event.source:
                lines.append(f"**Source:** {event.source}")
            if event.workflow_id:
                lines.append(f"**Workflow:** `{event.workflow_id}`")
            if event.project_key:
                lines.append(f"**Project:** `{event.project_key}`")
            if event.issue_number:
                lines.append(f"**Issue:** `#{event.issue_number}`")

            action_hints: list[str] = []
            for action in list(getattr(event, "actions", []) or []):
                if not isinstance(action, AlertAction):
                    continue
                label_text = str(action.label or "").strip()
                callback_data = str(action.callback_data or "").strip()
                url = str(action.url or "").strip()
                if not label_text:
                    continue
                if url:
                    buttons.append(
                        Button(label=label_text, callback_data=callback_data or label_text, url=url)
                    )
                elif callback_data:
                    action_hints.append(f"`{label_text}` → `{callback_data}`")
            if action_hints:
                lines.append("**Actions:** " + " | ".join(action_hints))

            description = "\n".join(lines)
            message_text = f"{emoji} **{label}**\n{description}"
            has_webhook = bool(getattr(self._discord, "_webhook_url", None))
            send_as_message = bool(buttons) and (has_webhook or bool(self._alert_channel_id))
        else:
            # Build embed description
            lines: list[str] = []
            if event.workflow_id:
                lines.append(f"**Workflow:** `{event.workflow_id}`")

            if isinstance(event, WorkflowFailed):
                lines.append(f"**Error:** {event.error}")
                severity = Severity.ERROR
            elif isinstance(event, StepFailed):
                lines.append(f"**Step:** {event.step_name} (#{event.step_num})")
                lines.append(f"**Error:** {event.error}")
                severity = Severity.WARNING
            elif isinstance(event, StepCompleted):
                lines.append(f"**Step:** {event.step_name} (#{event.step_num})")
                severity = Severity.INFO
            elif isinstance(event, AgentTimeout):
                lines.append(f"**Agent:** {event.agent_name}")
                if event.pid:
                    lines.append(f"**PID:** {event.pid}")
                severity = Severity.WARNING
            elif isinstance(event, WorkflowCancelled):
                severity = Severity.WARNING
            else:
                severity = Severity.INFO

            if event.data:
                for k, v in event.data.items():
                    lines.append(f"**{k}:** `{v}`")

            description = "\n".join(lines) if lines else "No additional details."
            message_text = f"{emoji} **{label}**\n{description}"

        try:
            if send_as_message:
                await self._discord.send_message(
                    self._alert_channel_id or "alerts",
                    Message(
                        text=message_text,
                        severity=severity,
                        buttons=buttons or None,
                    ),
                )
            else:
                await self._discord.send_alert(message_text, severity)
            self._last_send_ok = True
        except Exception as exc:
            self._last_send_ok = False
            logger.error("DiscordEventHandler send failed: %s", exc)

    # -- PluginLifecycle ----------------------------------------------------

    async def on_unload(self) -> None:
        await self._discord.aclose()
        await super().on_unload()


# -- Plugin registration ---------------------------------------------------


def register_plugins(registry: Any) -> None:
    """Register Discord event handler plugin."""
    from nexus.plugins.base import PluginKind

    registry.register_factory(
        kind=PluginKind.EVENT_HANDLER,
        name="discord-event-handler",
        version="1.0.0",
        factory=lambda config: DiscordEventHandler(config),
        description="Sends Discord embed notifications on workflow events via EventBus",
    )
