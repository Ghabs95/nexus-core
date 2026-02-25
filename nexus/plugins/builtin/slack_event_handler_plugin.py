"""Slack Event Handler Plugin.

Subscribes to the EventBus and sends Slack mrkdwn notifications on key
workflow lifecycle events. Registered as a ``PluginKind.EVENT_HANDLER``
plugin.

Uses the existing :class:`SlackNotificationChannel` adapter which supports
both bot-token and incoming-webhook modes.

Usage::

    from nexus.core.events import EventBus
    from nexus.plugins.builtin.slack_event_handler_plugin import SlackEventHandler

    bus = EventBus()
    handler = SlackEventHandler({
        "bot_token": "xoxb-...",
        "default_channel": "#ops",
    })
    handler.attach(bus)
"""

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

# Event type → (emoji, label)
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

_SEVERITY_MAP: dict[str, Severity] = {
    "info": Severity.INFO,
    "warning": Severity.WARNING,
    "error": Severity.ERROR,
    "critical": Severity.ERROR,
}


class SlackEventHandler:
    """Sends Slack mrkdwn notifications when workflow events fire.

    Wraps :class:`SlackNotificationChannel` and subscribes to the EventBus
    for reactive dispatch. Implements ``PluginLifecycle`` for health checks.
    """

    def __init__(self, config: dict[str, Any]):
        from nexus.adapters.notifications.slack import SlackNotificationChannel

        self._slack = SlackNotificationChannel(
            token=config.get("bot_token", ""),
            default_channel=config.get("default_channel", "#general"),
            webhook_url=config.get("webhook_url"),
        )
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
        logger.info("SlackEventHandler attached to EventBus (%d subscriptions)", len(self._subscriptions))

    def detach(self, bus: EventBus) -> None:
        """Unsubscribe all subscriptions from *bus*."""
        for sub_id in self._subscriptions:
            bus.unsubscribe(sub_id)
        self._subscriptions.clear()

    # -- Handler ------------------------------------------------------------

    async def _handle(self, event: NexusEvent) -> None:
        emoji, label = _EVENT_FORMAT.get(event.event_type, ("📌", event.event_type))

        if isinstance(event, SystemAlert):
            severity = _SEVERITY_MAP.get(event.severity, Severity.INFO)
            lines = [event.message]
            if event.source:
                lines.append(f"*Source:* {event.source}")
            if event.workflow_id:
                lines.append(f"*Workflow:* `{event.workflow_id}`")
        else:
            lines: list[str] = []
            if event.workflow_id:
                lines.append(f"*Workflow:* `{event.workflow_id}`")

            if isinstance(event, WorkflowFailed):
                lines.append(f"*Error:* {event.error}")
                severity = Severity.ERROR
            elif isinstance(event, StepFailed):
                lines.append(f"*Step:* {event.step_name} (#{event.step_num})")
                lines.append(f"*Error:* {event.error}")
                severity = Severity.WARNING
            elif isinstance(event, StepCompleted):
                lines.append(f"*Step:* {event.step_name} (#{event.step_num})")
                severity = Severity.INFO
            elif isinstance(event, AgentTimeout):
                lines.append(f"*Agent:* {event.agent_name}")
                if event.pid:
                    lines.append(f"*PID:* {event.pid}")
                severity = Severity.WARNING
            elif isinstance(event, WorkflowCancelled):
                severity = Severity.WARNING
            else:
                severity = Severity.INFO

            if event.data:
                for k, v in event.data.items():
                    lines.append(f"*{k}:* `{v}`")

        description = "\n".join(lines) if lines else "No additional details."
        message_text = f"{emoji} *{label}*\n{description}"

        try:
            await self._slack.send_alert(message_text, severity)
            self._last_send_ok = True
        except Exception as exc:
            self._last_send_ok = False
            logger.error("SlackEventHandler send failed: %s", exc)

    # -- PluginLifecycle ----------------------------------------------------

    async def on_load(self, registry: Any) -> None:
        logger.info("SlackEventHandler loaded")

    async def on_unload(self) -> None:
        logger.info("SlackEventHandler unloaded")

    async def health_check(self) -> PluginHealthStatus:
        return PluginHealthStatus(
            healthy=self._last_send_ok,
            name="slack-event-handler",
            details="Last send OK" if self._last_send_ok else "Last send failed",
        )


# -- Plugin registration ---------------------------------------------------

def register_plugins(registry: Any) -> None:
    """Register Slack event handler plugin."""
    from nexus.plugins.base import PluginKind

    registry.register_factory(
        kind=PluginKind.EVENT_HANDLER,
        name="slack-event-handler",
        version="1.0.0",
        factory=lambda config: SlackEventHandler(config),
        description="Sends Slack mrkdwn notifications on workflow events via EventBus",
    )
