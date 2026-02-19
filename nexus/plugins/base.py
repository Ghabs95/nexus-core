"""Plugin protocols and metadata for Nexus Core extension points."""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Protocol, runtime_checkable

from nexus.adapters.ai.base import AIProvider
from nexus.adapters.git.base import GitPlatform
from nexus.adapters.notifications.base import NotificationChannel
from nexus.adapters.storage.base import StorageBackend


class PluginKind(Enum):
    """Supported plugin extension kinds."""

    AI_PROVIDER = "ai_provider"
    GIT_PLATFORM = "git_platform"
    NOTIFICATION_CHANNEL = "notification_channel"
    STORAGE_BACKEND = "storage_backend"
    INPUT_ADAPTER = "input_adapter"


@dataclass(frozen=True)
class PluginSpec:
    """Registration metadata for a plugin implementation."""

    kind: PluginKind
    name: str
    version: str
    factory: Callable[[Dict[str, Any]], Any]
    description: str = ""


@runtime_checkable
class RegistryContributor(Protocol):
    """Protocol for entry-point objects that can register plugins."""

    def register_plugins(self, registry: "PluginRegistry") -> None:
        """Register one or more plugins in the provided registry."""


@runtime_checkable
class AIProviderPlugin(Protocol):
    """Protocol for AI provider plugin factories."""

    def __call__(self, config: Dict[str, Any]) -> AIProvider:
        """Build and return an AIProvider implementation."""


@runtime_checkable
class GitPlatformPlugin(Protocol):
    """Protocol for Git platform plugin factories."""

    def __call__(self, config: Dict[str, Any]) -> GitPlatform:
        """Build and return a GitPlatform implementation."""


@runtime_checkable
class NotificationChannelPlugin(Protocol):
    """Protocol for notification channel plugin factories."""

    def __call__(self, config: Dict[str, Any]) -> NotificationChannel:
        """Build and return a NotificationChannel implementation."""


@runtime_checkable
class StorageBackendPlugin(Protocol):
    """Protocol for storage backend plugin factories."""

    def __call__(self, config: Dict[str, Any]) -> StorageBackend:
        """Build and return a StorageBackend implementation."""


def normalize_plugin_name(name: str) -> str:
    """Normalize plugin names for lookup and deduplication."""

    return name.strip().lower().replace("_", "-")


def make_plugin_spec(
    kind: PluginKind,
    name: str,
    version: str,
    factory: Callable[[Dict[str, Any]], Any],
    description: str = "",
) -> PluginSpec:
    """Create a normalized plugin spec."""

    return PluginSpec(
        kind=kind,
        name=normalize_plugin_name(name),
        version=version,
        factory=factory,
        description=description,
    )
