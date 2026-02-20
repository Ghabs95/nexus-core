"""Top-level AdapterRegistry — configuration-driven factory for all adapters.

Usage::

    from nexus.adapters.registry import AdapterRegistry

    registry = AdapterRegistry()

    # Register custom types
    registry.register_storage("postgres", PostgreSQLStorageBackend)
    registry.register_git("gitlab", GitLabPlatform)

    # Create instances from config dict
    storage = registry.create_storage("postgres", connection_string=db_url)
    git     = registry.create_git("gitlab", token=gl_token, repo="org/proj")

Or load the whole adapter stack from a YAML/dict config section::

    adapters = registry.from_config({
        "storage": {"type": "file", "base_path": "./data"},
        "git":     {"type": "github", "repo": "org/proj"},
        "notifications": [
            {"type": "slack", "token": "xoxb-…", "default_channel": "#ops"},
        ],
    })
"""
import logging
from typing import Any, Dict, List, Optional, Type

from nexus.adapters.ai.base import AIProvider
from nexus.adapters.git.base import GitPlatform
from nexus.adapters.notifications.base import NotificationChannel
from nexus.adapters.storage.base import StorageBackend

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy loader helpers — avoid hard imports of optional extras at module load
# ---------------------------------------------------------------------------


def _load_builtin_storage(type_name: str) -> Optional[Type[StorageBackend]]:
    if type_name == "file":
        from nexus.adapters.storage.file import FileStorage
        return FileStorage
    if type_name in ("postgres", "postgresql"):
        from nexus.adapters.storage.postgres import PostgreSQLStorageBackend
        return PostgreSQLStorageBackend
    return None


def _load_builtin_git(type_name: str) -> Optional[Type[GitPlatform]]:
    if type_name == "github":
        from nexus.adapters.git.github import GitHubPlatform
        return GitHubPlatform
    if type_name == "gitlab":
        from nexus.adapters.git.gitlab import GitLabPlatform
        return GitLabPlatform
    return None


def _load_builtin_notifications(type_name: str) -> Optional[Type[NotificationChannel]]:
    if type_name == "slack":
        from nexus.adapters.notifications.slack import SlackNotificationChannel
        return SlackNotificationChannel
    return None


def _load_builtin_ai(type_name: str) -> Optional[Type[AIProvider]]:
    if type_name == "copilot":
        from nexus.adapters.ai.copilot_provider import CopilotCLIProvider
        return CopilotCLIProvider
    if type_name == "gemini":
        from nexus.adapters.ai.gemini_provider import GeminiCLIProvider
        return GeminiCLIProvider
    if type_name == "openai":
        from nexus.adapters.ai.openai_provider import OpenAIProvider
        return OpenAIProvider
    return None


# ---------------------------------------------------------------------------
# AdapterRegistry
# ---------------------------------------------------------------------------


class AdapterRegistry:
    """Central registry and factory for all nexus-core adapter types.

    Builtins are registered automatically; call the ``register_*`` methods to
    add custom implementations.

    Args:
        auto_register_builtins: If True (default), built-in adapter types are
            resolved lazily from the ``nexus.adapters`` package.
    """

    def __init__(self, auto_register_builtins: bool = True):
        self._auto_builtins = auto_register_builtins

        # Custom overrides: type_name -> class
        self._storage_registry:  Dict[str, Type[StorageBackend]]     = {}
        self._git_registry:       Dict[str, Type[GitPlatform]]        = {}
        self._notif_registry:     Dict[str, Type[NotificationChannel]] = {}
        self._ai_registry:        Dict[str, Type[AIProvider]]          = {}

    # ------------------------------------------------------------------
    # Registration API
    # ------------------------------------------------------------------

    def register_storage(self, type_name: str, cls: Type[StorageBackend]) -> None:
        """Register a custom StorageBackend implementation."""
        self._storage_registry[type_name] = cls
        logger.debug("AdapterRegistry: registered storage %r = %s", type_name, cls.__name__)

    def register_git(self, type_name: str, cls: Type[GitPlatform]) -> None:
        """Register a custom GitPlatform implementation."""
        self._git_registry[type_name] = cls
        logger.debug("AdapterRegistry: registered git %r = %s", type_name, cls.__name__)

    def register_notification(self, type_name: str, cls: Type[NotificationChannel]) -> None:
        """Register a custom NotificationChannel implementation."""
        self._notif_registry[type_name] = cls
        logger.debug("AdapterRegistry: registered notification %r = %s", type_name, cls.__name__)

    def register_ai(self, type_name: str, cls: Type[AIProvider]) -> None:
        """Register a custom AIProvider implementation."""
        self._ai_registry[type_name] = cls
        logger.debug("AdapterRegistry: registered ai %r = %s", type_name, cls.__name__)

    # ------------------------------------------------------------------
    # Factory API
    # ------------------------------------------------------------------

    def create_storage(self, type_name: str, **kwargs: Any) -> StorageBackend:
        """Instantiate a StorageBackend by type name.

        Args:
            type_name: Adapter type (``"file"``, ``"postgres"``).
            **kwargs: Constructor keyword arguments forwarded to the class.

        Returns:
            Configured StorageBackend instance.

        Raises:
            ValueError: If *type_name* is unknown.
        """
        cls = self._resolve("storage", type_name, _load_builtin_storage)
        return cls(**kwargs)

    def create_git(self, type_name: str, **kwargs: Any) -> GitPlatform:
        """Instantiate a GitPlatform by type name.

        Args:
            type_name: Adapter type (``"github"``, ``"gitlab"``).
            **kwargs: Constructor keyword arguments forwarded to the class.
        """
        cls = self._resolve("git", type_name, _load_builtin_git)
        return cls(**kwargs)

    def create_notification(self, type_name: str, **kwargs: Any) -> NotificationChannel:
        """Instantiate a NotificationChannel by type name.

        Args:
            type_name: Adapter type (``"slack"``).
            **kwargs: Constructor keyword arguments forwarded to the class.
        """
        cls = self._resolve("notification", type_name, _load_builtin_notifications)
        return cls(**kwargs)

    def create_ai(self, type_name: str, **kwargs: Any) -> AIProvider:
        """Instantiate an AIProvider by type name.

        Args:
            type_name: Adapter type (``"copilot"``, ``"gemini"``, ``"openai"``).
            **kwargs: Constructor keyword arguments forwarded to the class.
        """
        cls = self._resolve("ai", type_name, _load_builtin_ai)
        return cls(**kwargs)

    # ------------------------------------------------------------------
    # Config-driven bulk construction
    # ------------------------------------------------------------------

    def from_config(self, config: Dict[str, Any]) -> "AdapterConfig":
        """Construct all adapter instances from a config dict.

        Expected shape::

            {
                "storage": {"type": "file", "base_path": "./data"},
                "git":     {"type": "github", "repo": "org/project"},
                "notifications": [
                    {"type": "slack", "token": "xoxb-…", "default_channel": "#ops"},
                ],
                "ai": [
                    {"type": "copilot"},
                    {"type": "openai", "api_key": "sk-…"},
                ],
            }

        Returns:
            :class:`AdapterConfig` with ``.storage``, ``.git``,
            ``.notifications``, ``.ai_providers`` populated.
        """
        storage: Optional[StorageBackend] = None
        if "storage" in config:
            cfg = dict(config["storage"])
            t = cfg.pop("type")
            storage = self.create_storage(t, **cfg)

        git: Optional[GitPlatform] = None
        if "git" in config:
            cfg = dict(config["git"])
            t = cfg.pop("type")
            git = self.create_git(t, **cfg)

        notifications: List[NotificationChannel] = []
        for entry in config.get("notifications", []):
            cfg = dict(entry)
            t = cfg.pop("type")
            notifications.append(self.create_notification(t, **cfg))

        ai_providers: List[AIProvider] = []
        for entry in config.get("ai", []):
            cfg = dict(entry)
            t = cfg.pop("type")
            ai_providers.append(self.create_ai(t, **cfg))

        return AdapterConfig(
            storage=storage,
            git=git,
            notifications=notifications,
            ai_providers=ai_providers,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve(self, category: str, type_name: str, builtin_loader) -> type:
        """Look up a registry dict; fall back to builtin loader."""
        registry_map = {
            "storage": self._storage_registry,
            "git": self._git_registry,
            "notification": self._notif_registry,
            "ai": self._ai_registry,
        }
        registry = registry_map.get(category, {})
        if type_name in registry:
            return registry[type_name]
        if self._auto_builtins:
            cls = builtin_loader(type_name)
            if cls is not None:
                return cls
        raise ValueError(
            f"Unknown {category} adapter type {type_name!r}. "
            f"Register it with registry.register_{category}('{type_name}', YourClass)."
        )


# ---------------------------------------------------------------------------
# AdapterConfig — lightweight container for a resolved adapter set
# ---------------------------------------------------------------------------


class AdapterConfig:
    """Container for a fully resolved set of adapter instances.

    Created by :meth:`AdapterRegistry.from_config`.
    """

    def __init__(
        self,
        storage: Optional[StorageBackend] = None,
        git: Optional[GitPlatform] = None,
        notifications: Optional[List[NotificationChannel]] = None,
        ai_providers: Optional[List[AIProvider]] = None,
    ):
        self.storage = storage
        self.git = git
        self.notifications: List[NotificationChannel] = notifications or []
        self.ai_providers: List[AIProvider] = ai_providers or []

    def __repr__(self) -> str:
        return (
            f"AdapterConfig("
            f"storage={type(self.storage).__name__ if self.storage else None}, "
            f"git={type(self.git).__name__ if self.git else None}, "
            f"notifications={[type(n).__name__ for n in self.notifications]}, "
            f"ai={[type(a).__name__ for a in self.ai_providers]})"
        )
