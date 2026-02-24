"""Built-in plugin: JSON/line-based state storage helpers."""

import contextlib
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


class JsonStateStorePlugin:
    """Store and retrieve JSON and line-based state files."""

    def __init__(self, config: dict[str, Any]):
        self.base_dir = config.get("base_dir")

    def load_json(self, path: str, default: Any | None = None) -> Any:
        """Load JSON payload from file path."""
        resolved = self._resolve(path)
        if not os.path.exists(resolved):
            return default
        try:
            with open(resolved, encoding="utf-8") as file_handle:
                return json.load(file_handle)
        except Exception as exc:
            logger.error("Failed to load JSON state from %s: %s", resolved, exc)
            return default

    def save_json(self, path: str, data: Any) -> bool:
        """Save JSON payload to file path atomically (write-then-rename)."""
        resolved = self._resolve(path)
        tmp = resolved + ".tmp"
        try:
            self._ensure_parent_dir(resolved)
            with open(tmp, "w", encoding="utf-8") as file_handle:
                json.dump(data, file_handle, indent=2)
            os.replace(tmp, resolved)
            return True
        except Exception as exc:
            logger.error("Failed to save JSON state to %s: %s", resolved, exc)
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            return False

    def append_line(self, path: str, line: str) -> bool:
        """Append a single line to a file."""
        resolved = self._resolve(path)
        try:
            self._ensure_parent_dir(resolved)
            with open(resolved, "a", encoding="utf-8") as file_handle:
                file_handle.write(line)
            return True
        except Exception as exc:
            logger.error("Failed to append line to %s: %s", resolved, exc)
            return False

    def read_lines(self, path: str) -> list[str]:
        """Read all lines from a text file."""
        resolved = self._resolve(path)
        if not os.path.exists(resolved):
            return []
        try:
            with open(resolved, encoding="utf-8") as file_handle:
                return file_handle.readlines()
        except Exception as exc:
            logger.error("Failed to read lines from %s: %s", resolved, exc)
            return []

    def _resolve(self, path: str) -> str:
        """Resolve file path against optional base directory."""
        if os.path.isabs(path) or not self.base_dir:
            return path
        return os.path.join(self.base_dir, path)

    @staticmethod
    def _ensure_parent_dir(path: str) -> None:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)


def register_plugins(registry) -> None:
    """Register built-in JSON state store plugin."""
    from nexus.plugins import PluginKind

    registry.register_factory(
        kind=PluginKind.STORAGE_BACKEND,
        name="json-state-store",
        version="0.1.0",
        factory=lambda config: JsonStateStorePlugin(config),
        description="JSON and line-based state storage helper",
    )
