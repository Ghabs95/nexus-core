import asyncio
import logging
import signal
from typing import Any

from nexus.adapters.notifications.interactive import InteractiveClientPlugin
from nexus.adapters.registry import AdapterRegistry
from nexus.core.command_bridge import CommandRouter
from nexus.core.config.bootstrap import initialize_runtime

# Setup logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO, force=True
)
logger = logging.getLogger(__name__)

initialize_runtime(configure_logging=False)

from nexus.core.config import ORCHESTRATOR_CONFIG, PROJECT_CONFIG

# Centralized dependency injection factories
from dependencies import (
    get_hands_free_routing_handler_deps,
)
from nexus.core.orchestration.ai_orchestrator import get_orchestrator


async def main() -> None:
    """Initialize and run all configured interactive client plugins."""
    # 1. Initialize core services
    get_orchestrator(ORCHESTRATOR_CONFIG)
    registry = AdapterRegistry()

    # 2. Extract client configs from PROJECT_CONFIG
    client_configs: dict[str, Any] = PROJECT_CONFIG.get("interactive_clients", {})
    if not client_configs:
        logger.error("No interactive_clients configured in project_config.yaml")
        return

    # 3. Load plugins via registry
    plugins: dict[str, InteractiveClientPlugin] = {}
    for client_id, config in client_configs.items():
        if not config.get("enabled", True):
            continue

        plugin_type = config.get("plugin")
        if not plugin_type:
            logger.warning(f"Client {client_id} missing 'plugin' type.")
            continue

        try:
            logger.info(f"Loading interactive plugin: {plugin_type} for {client_id}")
            # The registry builds and configures the plugin
            plugin = registry.create_interactive(plugin_type, config=config)
            if plugin:
                plugins[client_id] = plugin
            else:
                logger.error(f"Failed to load plugin {plugin_type} for {client_id}")
        except Exception as e:
            logger.exception(f"Error loading client {client_id}: {e}")

    if not plugins:
        logger.error("No interactive plugins successfully loaded. Exiting.")
        return

    # 4. Bind unified handlers to all loaded plugins
    command_router = CommandRouter(default_source_platform="telegram")
    for client_id, plugin in plugins.items():
        logger.info(f"Binding handlers for client: {client_id}")
        _bind_handlers(plugin, command_router)

    # 5. Handle shutdown elegantly
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _signal_handler(_signum: int, _frame: Any) -> None:
        logger.info("Shutdown signal received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _signal_handler)

    # 6. Start all plugins concurrently
    logger.info(f"Starting {len(plugins)} interactive plugins...")

    start_tasks = []
    for client_id, plugin in plugins.items():
        # Depending on the plugin, this might block (like telegram long-polling)
        # or it might just setup background tasks.
        start_tasks.append(asyncio.create_task(plugin.start()))
    stop_task = asyncio.create_task(stop_event.wait())

    try:
        # Wait until we get a stop signal or one of the bots hard-crashes
        done, pending = await asyncio.wait(
            {stop_task, *start_tasks}, return_when=asyncio.FIRST_COMPLETED
        )
        logger.info("Main loop terminated. Stopping plugins...")
        for task in pending:
            task.cancel()
        for task in done:
            if task is stop_task:
                continue
            if task.cancelled():
                continue
            if exc := task.exception():
                logger.error("Interactive plugin task exited with error: %s", exc)
    finally:
        # 7. Teardown
        for client_id, plugin in plugins.items():
            try:
                await plugin.stop()
            except Exception as e:
                logger.error(f"Error stopping plugin {client_id}: {e}")


def _bind_handlers(plugin: InteractiveClientPlugin, command_router: CommandRouter) -> None:
    """Bind all platform-agnostic handlers to a specific plugin instance."""

    routing_deps = get_hands_free_routing_handler_deps()
    command_router.bind_plugin(plugin)

    # Catch-all text message handler
    from nexus.core.handlers.inbox_routing_handler import route_hands_free_text

    async def _message_handler(
        *,
        user_id: str,
        text: str,
        raw_event: Any = None,
        **_kwargs: Any,
    ) -> None:
        attachments = _kwargs.get("attachments")
        ctx = command_router.build_context(
            client=plugin,
            user_id=str(user_id or ""),
            text=str(text or ""),
            args=[],
            raw_event=raw_event,
            user_state={},
            attachments=attachments,
        )
        await route_hands_free_text(ctx, routing_deps)

    plugin.register_message_handler(_message_handler)


if __name__ == "__main__":
    asyncio.run(main())
