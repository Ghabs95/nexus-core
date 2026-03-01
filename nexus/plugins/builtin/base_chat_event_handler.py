"""Base class for Chat Event Handlers."""

import logging
from typing import Any

from nexus.core.events import EventBus, NexusEvent
from nexus.plugins.base import PluginHealthStatus

logger = logging.getLogger(__name__)


class BaseChatEventHandler:
    """Base class for chat-based event handlers (Discord, Telegram).

    Handles EventBus subscriptions and standard PluginLifecycle health checks.
    Subclasses must implement `_handle(event: NexusEvent)` and set
    `self._last_send_ok` based on successful message delivery.
    """

    def __init__(self, name: str):
        self.name = name
        self._subscriptions: list[str] = []
        self._last_send_ok: bool = True

    # -- EventBus wiring ---------------------------------------------------

    def attach(self, bus: EventBus) -> None:
        """Subscribe to relevant events on *bus*."""
        for event_name in [
            "workflow.started",
            "workflow.completed",
            "workflow.failed",
            "workflow.cancelled",
            "step.failed",
            "agent.timeout",
            "system.alert",
        ]:
            self._subscriptions.append(bus.subscribe(event_name, self._handle))

        # Step completed is only handled if explicitly needed or we can just subscribe all. Both Discord and Telegram had it.
        # Wait, Discord had step.failed but also step.completed in the handle logic?
        # Let's check: Discord 'attach' subscribes to step.failed but NOT step.completed!
        # Telegram 'attach' subscribes to step.failed but NOT step.completed! (Wait, looking at the code above, both only subscribe to step.failed, although the handle method has a check for StepCompleted).
        # Let's just subscribe to what was there.
        # Discord: workflow.started, workflow.completed, workflow.failed, workflow.cancelled, step.failed, agent.timeout, system.alert
        # Telegram: workflow.started, workflow.completed, workflow.failed, workflow.cancelled, step.failed, agent.timeout, system.alert

        logger.info(
            "%s attached to EventBus (%d subscriptions)", self.name, len(self._subscriptions)
        )

    def detach(self, bus: EventBus) -> None:
        """Unsubscribe all subscriptions from *bus*."""
        for sub_id in self._subscriptions:
            bus.unsubscribe(sub_id)
        self._subscriptions.clear()

    # -- Handler ------------------------------------------------------------

    async def _handle(self, event: NexusEvent) -> None:
        """Process an incoming event. Must be implemented by subclasses."""
        raise NotImplementedError

    # -- PluginLifecycle ----------------------------------------------------

    async def on_load(self, registry: Any) -> None:
        logger.info("%s loaded", self.name)

    async def on_unload(self) -> None:
        logger.info("%s unloaded", self.name)

    async def health_check(self) -> PluginHealthStatus:
        return PluginHealthStatus(
            healthy=self._last_send_ok,
            name=self.name,
            details="Last send OK" if self._last_send_ok else "Last send failed",
        )
