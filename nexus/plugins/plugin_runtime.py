"""Dynamic plugin hot-reload support for Nexus Core.

Provides :class:`HotReloadWatcher`, which monitors a directory for ``.py`` file
changes and reloads matching plugins into a :class:`PluginRegistry` without
restarting any core services.

Requires the ``watchdog`` package (``pip install nexus-core[hotreload]``).
"""

from __future__ import annotations

import importlib.util
import logging
import types
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Deferred import so that the rest of nexus-core stays importable even when
# watchdog is not installed (hot-reload is an optional feature).
try:
    from watchdog.events import FileSystemEvent, FileSystemEventHandler
    from watchdog.observers import Observer

    _WATCHDOG_AVAILABLE = True
except ImportError:  # pragma: no cover
    _WATCHDOG_AVAILABLE = False
    FileSystemEvent = object  # type: ignore[assignment,misc]
    FileSystemEventHandler = object  # type: ignore[assignment,misc]
    Observer = None  # type: ignore[assignment,misc]


class HotReloadWatcher:
    """Watch a directory for ``.py`` file changes and reload plugins.

    The watcher is opt-in and must be explicitly started with :meth:`start`.
    It has no effect on existing code paths that do not use it.

    .. warning::

        All ``.py`` files inside ``watch_dir`` are executed as Python code
        with the application's full privileges.  Only point this watcher at
        directories that contain **trusted** source files.

    **Limitations:**

    - Only the **top-level** directory is monitored (non-recursive).  Plugin
      files placed in sub-directories will not be detected.
    - Module isolation applies to the plugin file itself only.  If a plugin
      imports other modules those are loaded through the normal
      ``sys.modules`` cache and will **not** be re-executed on reload.
      Hot-reload works best for self-contained plugin files.
    - Plugin ``register_plugins()`` functions must call
      ``registry.register()`` / ``registry.register_factory()`` with
      ``force=True``; otherwise subsequent reloads raise
      :class:`~nexus.plugins.registry.PluginRegistrationError` (the error
      is caught and logged, but the reload is skipped).

    Example::

        from nexus.plugins.plugin_runtime import HotReloadWatcher
        watcher = HotReloadWatcher(registry, watch_dir="/my/plugins")
        watcher.start()
        ...
        watcher.stop()

    Plugin files must expose one of:

    - A ``register_plugins(registry)`` function (``RegistryContributor``
      protocol), or
    - A top-level callable that accepts a :class:`PluginRegistry`.

    Example plugin file using ``force=True`` for hot-reload compatibility::

        from nexus.plugins.base import PluginKind

        def register_plugins(registry):
            registry.register_factory(
                PluginKind.STORAGE_BACKEND, "my-plugin", "1.0", _factory,
                force=True,
            )

    Args:
        registry: The :class:`~nexus.plugins.registry.PluginRegistry` to update.
        watch_dir: Path to the directory containing plugin ``.py`` files.
    """

    def __init__(
        self,
        registry: "PluginRegistry",  # noqa: F821 – forward ref
        watch_dir: "str | Path",
    ) -> None:
        if not _WATCHDOG_AVAILABLE:
            raise ImportError(
                "watchdog is required for hot-reload. "
                "Install it with: pip install 'nexus-core[hotreload]'"
            )
        from nexus.plugins.registry import PluginRegistry  # local import avoids circulars

        if not isinstance(registry, PluginRegistry):
            raise TypeError(f"Expected PluginRegistry, got {type(registry).__name__}")

        self._registry = registry
        self._watch_dir = Path(watch_dir).resolve()
        self._observer: Optional[Observer] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the filesystem observer in a background thread."""
        if self._observer is not None and self._observer.is_alive():
            logger.warning("HotReloadWatcher is already running")
            return

        if not self._watch_dir.exists():
            raise FileNotFoundError(f"Watch directory does not exist: {self._watch_dir}")

        handler = _PluginFileEventHandler(self._registry, self._watch_dir)
        self._observer = Observer()
        self._observer.schedule(handler, str(self._watch_dir), recursive=False)
        self._observer.start()
        logger.info("HotReloadWatcher started, watching %s", self._watch_dir)

    def stop(self) -> None:
        """Stop the filesystem observer and clean up."""
        if self._observer is None:
            return
        self._observer.stop()
        self._observer.join()
        self._observer = None
        logger.info("HotReloadWatcher stopped")

    def is_running(self) -> bool:
        """Return True if the background observer thread is alive."""
        return self._observer is not None and self._observer.is_alive()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

if _WATCHDOG_AVAILABLE:

    class _PluginFileEventHandler(FileSystemEventHandler):
        """Handles file-system events and triggers registry refresh."""

        def __init__(self, registry: "PluginRegistry", watch_dir: Path) -> None:  # noqa: F821
            super().__init__()
            self._registry = registry
            self._watch_dir = watch_dir

        # watchdog calls on_modified for both file modifications and moves
        def on_modified(self, event: FileSystemEvent) -> None:
            if getattr(event, "is_directory", False):
                return
            src: str = getattr(event, "src_path", "")
            if src.endswith(".py"):
                self._reload_plugin_file(Path(src))

        on_created = on_modified  # also handle newly dropped files

        def _reload_plugin_file(self, plugin_path: Path) -> None:
            """Load a plugin file and update the registry."""
            module = _load_module_from_path(plugin_path)
            if module is None:
                return

            # Try RegistryContributor protocol first
            register_fn = getattr(module, "register_plugins", None)
            if callable(register_fn):
                try:
                    register_fn(self._registry)
                    logger.info("Hot-reloaded plugin file: %s", plugin_path)
                except Exception as exc:
                    logger.warning("Failed to register plugins from %s: %s", plugin_path, exc)
                return

            # Fallback: module itself is callable
            if callable(module):
                try:
                    module(self._registry)
                    logger.info("Hot-reloaded plugin file (callable): %s", plugin_path)
                except Exception as exc:
                    logger.warning("Failed to load callable plugin %s: %s", plugin_path, exc)
                return

            logger.warning(
                "Plugin file %s does not expose register_plugins() and is not callable; skipping",
                plugin_path,
            )

else:
    # Stub so tests can import the module without watchdog installed
    class _PluginFileEventHandler:  # type: ignore[no-redef]
        pass


def _load_module_from_path(path: Path) -> Optional[types.ModuleType]:
    """Load a Python module from *path* using a fresh module object.

    Uses ``importlib.util.spec_from_file_location`` + ``module_from_spec`` so
    each load is fully isolated — no stale references leak through
    ``sys.modules``.
    """
    module_name = f"_nexus_hotreload_{path.stem}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            logger.warning("Cannot create module spec from %s", path)
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        return module
    except Exception as exc:
        logger.warning("Error loading plugin module %s: %s", path, exc)
        return None
