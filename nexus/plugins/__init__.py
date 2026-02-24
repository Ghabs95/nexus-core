"""Plugin interfaces and registry for Nexus Core."""

from nexus.plugins.base import PluginKind, PluginSpec, make_plugin_spec
from nexus.plugins.builtin import (
    AgentLaunchPolicyPlugin,
    AIOrchestrator,
    AIProvider,
    GitHubIssueCLIPlugin,
    JsonStateStorePlugin,
    RateLimitedError,
    RuntimeOpsPlugin,
    TelegramInteractivePlugin,
    TelegramNotificationPlugin,
    ToolUnavailableError,
    WorkflowMonitorPolicyPlugin,
    WorkflowPolicyPlugin,
    WorkflowStateEnginePlugin,
)
from nexus.plugins.registry import (
    PluginNotFoundError,
    PluginRegistrationError,
    PluginRegistry,
)

__all__ = [
    "PluginKind",
    "PluginSpec",
    "make_plugin_spec",
    "AIOrchestrator",
    "AIProvider",
    "AgentLaunchPolicyPlugin",
    "RateLimitedError",
    "ToolUnavailableError",
    "GitHubIssueCLIPlugin",
    "JsonStateStorePlugin",
    "RuntimeOpsPlugin",
    "TelegramInteractivePlugin",
    "TelegramNotificationPlugin",
    "WorkflowMonitorPolicyPlugin",
    "WorkflowPolicyPlugin",
    "WorkflowStateEnginePlugin",
    "PluginRegistry",
    "PluginRegistrationError",
    "PluginNotFoundError",
]
