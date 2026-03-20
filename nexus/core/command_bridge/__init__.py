"""Framework command bridge for external command surfaces like OpenClaw."""

from nexus.core.command_bridge.http import (
    CommandBridgeConfig,
    create_command_bridge_app,
    run_command_bridge_server,
)
from nexus.core.command_bridge.models import CommandRequest, CommandResult, RequesterContext
from nexus.core.command_bridge.router import CommandRouter

__all__ = [
    "CommandBridgeConfig",
    "CommandRequest",
    "CommandResult",
    "CommandRouter",
    "RequesterContext",
    "create_command_bridge_app",
    "run_command_bridge_server",
]
