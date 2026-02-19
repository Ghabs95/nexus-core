"""Plugin registry and entry-point loading for Nexus Core."""

import logging
from importlib.metadata import entry_points
from typing import Any, Callable, Dict, List, Optional, Tuple

from nexus.plugins.base import (
    PluginKind,
    PluginSpec,
    RegistryContributor,
    make_plugin_spec,
    normalize_plugin_name,
)

logger = logging.getLogger(__name__)


class PluginRegistrationError(Exception):
    """Raised when plugin registration fails."""


class PluginNotFoundError(Exception):
    """Raised when a requested plugin is not registered."""


class PluginRegistry:
    """Holds plugin specs and instantiates plugin implementations."""

    def __init__(self):
        self._plugins: Dict[Tuple[PluginKind, str], PluginSpec] = {}

    def register(self, spec: PluginSpec) -> None:
        """Register a plugin spec."""
        key = (spec.kind, normalize_plugin_name(spec.name))
        if key in self._plugins:
            existing = self._plugins[key]
            raise PluginRegistrationError(
                f"Plugin already registered: kind={spec.kind.value} name={spec.name} "
                f"existing_version={existing.version}"
            )
        self._plugins[key] = spec
        logger.info(
            "Registered plugin: kind=%s name=%s version=%s",
            spec.kind.value,
            spec.name,
            spec.version,
        )

    def register_factory(
        self,
        kind: PluginKind,
        name: str,
        version: str,
        factory: Callable[[Dict[str, Any]], Any],
        description: str = "",
    ) -> None:
        """Convenience method to register a plugin from primitive values."""
        self.register(make_plugin_spec(kind, name, version, factory, description))

    def create(self, kind: PluginKind, name: str, config: Optional[Dict[str, Any]] = None) -> Any:
        """Instantiate a plugin by kind/name."""
        key = (kind, normalize_plugin_name(name))
        spec = self._plugins.get(key)
        if not spec:
            raise PluginNotFoundError(f"No plugin found: kind={kind.value} name={name}")
        return spec.factory(config or {})

    def get_spec(self, kind: PluginKind, name: str) -> Optional[PluginSpec]:
        """Get plugin spec by kind/name."""
        return self._plugins.get((kind, normalize_plugin_name(name)))

    def list_specs(self, kind: Optional[PluginKind] = None) -> List[PluginSpec]:
        """List registered plugin specs, optionally filtered by kind."""
        specs = self._plugins.values()
        if kind:
            specs = (spec for spec in specs if spec.kind == kind)
        return sorted(specs, key=lambda spec: (spec.kind.value, spec.name))

    def has_plugin(self, kind: PluginKind, name: str) -> bool:
        """Check whether a plugin is registered."""
        return (kind, normalize_plugin_name(name)) in self._plugins

    def load_entrypoint_plugins(self, group: str = "nexus_core.plugins") -> int:
        """Load plugins from setuptools entry points.

        Supported entry point object shapes:
        - object implementing RegistryContributor (register_plugins)
        - callable accepting PluginRegistry and registering plugins
        """
        loaded = 0
        for entry_point in _iter_entry_points(group):
            try:
                loaded_obj = entry_point.load()
                if isinstance(loaded_obj, RegistryContributor):
                    loaded_obj.register_plugins(self)
                    loaded += 1
                    continue

                if callable(loaded_obj):
                    loaded_obj(self)
                    loaded += 1
                    continue

                raise PluginRegistrationError(
                    f"Unsupported entry point object for {entry_point.name}: {type(loaded_obj).__name__}"
                )
            except Exception as exc:
                logger.warning("Failed loading plugin entry point %s: %s", entry_point.name, exc)
        return loaded


def _iter_entry_points(group: str):
    """Yield entry points across Python versions."""
    discovered = entry_points()
    if hasattr(discovered, "select"):
        return discovered.select(group=group)
    return discovered.get(group, [])
