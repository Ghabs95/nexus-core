"""Host-level state management for Nexus bot runtime.

Handles bot-host concerns only:
- Launched agents tracking
- Tracked issues
- SocketIO transition broadcasting

Workflow and approval state is managed by :mod:`integrations.workflow_state_factory`.
"""
import logging
import time
from collections.abc import Callable
from typing import Any

from config import (
    AGENT_RECENT_WINDOW,
    LAUNCHED_AGENTS_FILE,
    NEXUS_STORAGE_BACKEND,
    TRACKED_ISSUES_FILE,
    ensure_state_dir,
    ensure_logs_dir,
)
from orchestration.plugin_runtime import get_profiled_plugin

logger = logging.getLogger(__name__)

# Optional SocketIO emitter injected at startup by webhook_server.py.
# Signature: (event_name: str, data: dict) -> None
_socketio_emitter: Callable[[str, Any], None] | None = None


def set_socketio_emitter(emitter: Callable[[str, Any], None]) -> None:
    """Register a SocketIO emit function for real-time transition broadcasting."""
    global _socketio_emitter
    _socketio_emitter = emitter


def _get_state_store_plugin():
    """Return shared JSON state store plugin instance."""
    return get_profiled_plugin(
        "state_store_default",
        cache_key="state:json-store",
    )


def _get_storage_backend():
    """Return the postgres StorageBackend instance (cached)."""
    if not hasattr(_get_storage_backend, "_instance"):
        try:
            from integrations.workflow_state_factory import get_storage_backend
            _get_storage_backend._instance = get_storage_backend()
        except Exception as exc:
            logger.warning("Could not get postgres storage backend for host state: %s", exc)
            _get_storage_backend._instance = None
    return _get_storage_backend._instance


def _host_state_key_from_path(path: str) -> str:
    """Derive a stable key from a filesystem path.

    E.g. ``/opt/nexus/.nexus/state/launched_agents.json`` -> ``launched_agents``.
    """
    import os
    return os.path.splitext(os.path.basename(path))[0]


class HostStateManager:
    """Manages host-level persistent state for the Nexus bot runtime.

    This class covers *bot-host* concerns only — launched agent tracking,
    issue tracking, and SocketIO broadcast.  For workflow / approval state
    see :func:`integrations.workflow_state_factory.get_workflow_state`.
    """

    @staticmethod
    def emit_transition(event_type: str, data: dict) -> None:
        """Broadcast a state transition via SocketIO (no-op if emitter not registered)."""
        if _socketio_emitter is not None:
            try:
                _socketio_emitter(event_type, data)
            except Exception as exc:
                logger.warning(f"SocketIO emit failed for {event_type}: {exc}")

    @staticmethod
    def _load_json_state(path: str, default, ensure_logs: bool = False):
        """Load JSON state — routes to postgres or filesystem based on config."""
        if NEXUS_STORAGE_BACKEND == "postgres":
            backend = _get_storage_backend()
            if backend:
                import asyncio
                key = _host_state_key_from_path(path)
                result = asyncio.run(backend.load_host_state(key))
                return result if result is not None else default
            logger.warning("Postgres backend unavailable, falling back to filesystem")

        if ensure_logs:
            ensure_logs_dir()
        else:
            ensure_state_dir()

        plugin = _get_state_store_plugin()
        if not plugin:
            return default
        return plugin.load_json(path, default=default)

    @staticmethod
    def _save_json_state(path: str, data, *, context: str, ensure_logs: bool = False) -> None:
        """Save JSON state — routes to postgres or filesystem based on config."""
        if NEXUS_STORAGE_BACKEND == "postgres":
            backend = _get_storage_backend()
            if backend:
                import asyncio
                key = _host_state_key_from_path(path)
                asyncio.run(backend.save_host_state(key, data))
                return
            logger.warning("Postgres backend unavailable, falling back to filesystem for %s", context)

        if ensure_logs:
            ensure_logs_dir()
        else:
            ensure_state_dir()

        plugin = _get_state_store_plugin()
        if not plugin:
            logger.error(f"State storage plugin unavailable; cannot save {context}")
            return
        plugin.save_json(path, data)

    @staticmethod
    def load_launched_agents(recent_only: bool = True) -> dict[str, dict]:
        """Load launched agents from persistent storage.

        Args:
            recent_only: When True (default), filter to entries within
                AGENT_RECENT_WINDOW. Pass False in dead-agent detection so
                that crashed agents older than the window are still caught.
        """
        data = HostStateManager._load_json_state(LAUNCHED_AGENTS_FILE, default={}) or {}
        if not recent_only:
            return data
        cutoff = time.time() - AGENT_RECENT_WINDOW
        return {k: v for k, v in data.items() if v.get("timestamp", 0) > cutoff}

    @staticmethod
    def save_launched_agents(data: dict[str, dict]) -> None:
        """Save launched agents to persistent storage."""
        HostStateManager._save_json_state(
            LAUNCHED_AGENTS_FILE,
            data,
            context="launched agents",
        )

    @staticmethod
    def get_last_tier_for_issue(issue_num: str) -> str | None:
        """Get the last known workflow tier for an issue from launched_agents.

        Unlike :meth:`load_launched_agents`, this reads without the recency
        cutoff so that tier information persists across slow agent executions.

        Returns:
            Tier name (e.g. ``"full"``, ``"fast-track"``) or ``None``.
        """
        data = HostStateManager._load_json_state(LAUNCHED_AGENTS_FILE, default={}) or {}
        entry = data.get(str(issue_num))
        if entry and isinstance(entry, dict):
            return entry.get("tier")
        return None

    @staticmethod
    def register_launched_agent(issue_num: str, agent_name: str, pid: int) -> None:
        """Register a newly launched agent."""
        data = HostStateManager.load_launched_agents()
        key = f"{issue_num}_{agent_name}"
        data[key] = {
            "issue": issue_num,
            "agent": agent_name,
            "pid": pid,
            "timestamp": time.time()
        }
        HostStateManager.save_launched_agents(data)
        logger.info(f"Registered launched agent: {agent_name} (PID: {pid}) for issue #{issue_num}")
        HostStateManager.emit_transition("agent_registered", {
            "issue": issue_num,
            "agent": agent_name,
            "pid": pid,
            "timestamp": data[key]["timestamp"],
        })

    @staticmethod
    def was_recently_launched(issue_num: str, agent_name: str) -> bool:
        """Check if agent was recently launched (within 2-minute window)."""
        data = HostStateManager.load_launched_agents()
        key = f"{issue_num}_{agent_name}"
        return key in data

    @staticmethod
    def load_tracked_issues() -> dict[str, dict]:
        """Load tracked issues from file."""
        return HostStateManager._load_json_state(TRACKED_ISSUES_FILE, default={})

    @staticmethod
    def save_tracked_issues(data: dict[str, dict]) -> None:
        """Save tracked issues to file."""
        HostStateManager._save_json_state(
            TRACKED_ISSUES_FILE,
            data,
            context="tracked issues",
        )

    @staticmethod
    def add_tracked_issue(issue_num: int, project: str, description: str) -> None:
        """Add an issue to tracking."""
        data = HostStateManager.load_tracked_issues()
        data[str(issue_num)] = {
            "project": project,
            "description": description,
            "created_at": time.time(),
            "status": "active"
        }
        HostStateManager.save_tracked_issues(data)
        logger.info(f"Added tracked issue: #{issue_num} ({project})")

    @staticmethod
    def remove_tracked_issue(issue_num: int) -> None:
        """Remove an issue from tracking."""
        data = HostStateManager.load_tracked_issues()
        data.pop(str(issue_num), None)
        HostStateManager.save_tracked_issues(data)
        logger.info(f"Removed tracked issue: #{issue_num}")

    @staticmethod
    def get_workflow_id_for_issue(issue_num: str) -> str | None:
        """Return workflow id mapped to an issue number (compatibility helper)."""
        try:
            from integrations.workflow_state_factory import get_workflow_state

            return get_workflow_state().get_workflow_id(str(issue_num))
        except Exception:
            return None

