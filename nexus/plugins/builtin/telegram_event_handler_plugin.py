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
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from nexus.core.events import (
    AgentTimeout,
    EventBus,
    NexusEvent,
    StepCompleted,
    StepFailed,
    SystemAlert,
    WorkflowCancelled,
    WorkflowCompleted,
    WorkflowFailed,
    WorkflowStarted,
)
from nexus.plugins.base import PluginHealthStatus, PluginLifecycle

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


class TelegramEventHandler:
    """Sends Telegram messages when workflow events fire.

    Implements :class:`PluginLifecycle` so the registry can perform
    health checks against the Telegram Bot API.
    """

    def __init__(self, config: dict[str, Any]):
        from nexus.plugins.builtin.telegram_notification_plugin import (
            TelegramNotificationPlugin,
        )

        self._telegram = TelegramNotificationPlugin(config)
        self._chat_id = str(config.get("chat_id", ""))
        self._subscriptions: list[str] = []
        self._last_send_ok: bool = True

    # -- EventBus wiring ---------------------------------------------------

    def attach(self, bus: EventBus) -> None:
        """Subscribe to relevant events on *bus*."""
        self._subscriptions.append(bus.subscribe("workflow.started", self._handle))
        self._subscriptions.append(bus.subscribe("workflow.completed", self._handle))
        self._subscriptions.append(bus.subscribe("workflow.failed", self._handle))
        self._subscriptions.append(bus.subscribe("workflow.cancelled", self._handle))
        self._subscriptions.append(bus.subscribe("step.failed", self._handle))
        self._subscriptions.append(bus.subscribe("agent.timeout", self._handle))
        self._subscriptions.append(bus.subscribe("system.alert", self._handle))
        logger.info("TelegramEventHandler attached to EventBus (%d subscriptions)", len(self._subscriptions))

    def detach(self, bus: EventBus) -> None:
        """Unsubscribe all subscriptions from *bus*."""
        for sub_id in self._subscriptions:
            bus.unsubscribe(sub_id)
        self._subscriptions.clear()

    # -- Handler ------------------------------------------------------------

    async def _handle(self, event: NexusEvent) -> None:
        # SystemAlert uses its own format
        if isinstance(event, SystemAlert):
            icon = _SEVERITY_ICON.get(event.severity, "ℹ️")
            lines = [f"{icon} {event.message}"]
            if event.source:
                lines.append(f"Source: `{event.source}`")
            if event.workflow_id:
                lines.append(f"Workflow: `{event.workflow_id}`")
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
            ok = self._telegram.send_message_sync(text, parse_mode="Markdown")
            self._last_send_ok = bool(ok)
        except Exception as exc:
            self._last_send_ok = False
            logger.error("TelegramEventHandler send failed: %s", exc)

    # -- PluginLifecycle ----------------------------------------------------

    async def on_load(self, registry: Any) -> None:
        logger.info("TelegramEventHandler loaded")

    async def on_unload(self) -> None:
        logger.info("TelegramEventHandler unloaded")

    async def health_check(self) -> PluginHealthStatus:
        return PluginHealthStatus(
            healthy=self._last_send_ok,
            name="telegram-event-handler",
            details="Last send OK" if self._last_send_ok else "Last send failed",
        )


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
