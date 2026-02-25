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

import asyncio
import logging
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
from nexus.core.models import Severity
from nexus.plugins.base import PluginHealthStatus

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


class DiscordEventHandler:
    """Sends Discord embed notifications when workflow events fire.

    Wraps :class:`DiscordNotificationChannel` and subscribes to the
    EventBus for reactive dispatch. Implements ``PluginLifecycle`` for
    health checks.
    """

    def __init__(self, config: dict[str, Any]):
        from nexus.adapters.notifications.discord import DiscordNotificationChannel

        self._discord = DiscordNotificationChannel(
            webhook_url=config.get("webhook_url"),
            bot_token=config.get("bot_token"),
            alert_channel_id=config.get("alert_channel_id"),
        )
        self._alert_channel_id = config.get("alert_channel_id", "")
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
        logger.info("DiscordEventHandler attached to EventBus (%d subscriptions)", len(self._subscriptions))

    def detach(self, bus: EventBus) -> None:
        """Unsubscribe all subscriptions from *bus*."""
        for sub_id in self._subscriptions:
            bus.unsubscribe(sub_id)
        self._subscriptions.clear()

    # -- Handler ------------------------------------------------------------

    async def _handle(self, event: NexusEvent) -> None:
        colour, emoji, label = _EVENT_FORMAT.get(event.event_type, (0x808080, "📌", event.event_type))

        # SystemAlert uses its own format
        if isinstance(event, SystemAlert):
            severity = _SEVERITY_MAP.get(event.severity, Severity.INFO)
            lines = [event.message]
            if event.source:
                lines.append(f"**Source:** {event.source}")
            if event.workflow_id:
                lines.append(f"**Workflow:** `{event.workflow_id}`")
            description = "\n".join(lines)
            message_text = f"{emoji} **{label}**\n{description}"
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
            await self._discord.send_alert(message_text, severity)
            self._last_send_ok = True
        except Exception as exc:
            self._last_send_ok = False
            logger.error("DiscordEventHandler send failed: %s", exc)

    # -- PluginLifecycle ----------------------------------------------------

    async def on_load(self, registry: Any) -> None:
        logger.info("DiscordEventHandler loaded")

    async def on_unload(self) -> None:
        await self._discord.aclose()
        logger.info("DiscordEventHandler unloaded")

    async def health_check(self) -> PluginHealthStatus:
        return PluginHealthStatus(
            healthy=self._last_send_ok,
            name="discord-event-handler",
            details="Last send OK" if self._last_send_ok else "Last send failed",
        )


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
