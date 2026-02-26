"""Host-level state management for Nexus bot runtime.

Handles bot-host concerns only:
- Launched agents tracking
- Tracked issues
- SocketIO transition broadcasting

Workflow and approval state is managed by :mod:`integrations.workflow_state_factory`.
"""

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from config import (
    AGENT_RECENT_WINDOW,
    LAUNCHED_AGENTS_FILE,
    NEXUS_STATE_DIR,
    NEXUS_STORAGE_BACKEND,
    TRACKED_ISSUES_FILE,
    ensure_state_dir,
    ensure_logs_dir,
)
from orchestration.plugin_runtime import get_profiled_plugin

logger = logging.getLogger(__name__)
MERGE_QUEUE_FILE = f"{NEXUS_STATE_DIR}/merge_queue.json"

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


def _run_coro_sync(coro_factory: Callable[[], Any], *, timeout_seconds: float = 10) -> Any:
    """Run a coroutine from sync code, even when already inside an event loop."""
    try:
        asyncio.get_running_loop()
        in_running_loop = True
    except RuntimeError:
        in_running_loop = False

    if not in_running_loop:
        return asyncio.run(coro_factory())

    holder: dict[str, Any] = {"value": None, "error": None}

    def _runner() -> None:
        try:
            holder["value"] = asyncio.run(coro_factory())
        except Exception as exc:  # pragma: no cover - defensive bridge
            holder["error"] = exc

    worker = threading.Thread(target=_runner, daemon=True)
    worker.start()
    worker.join(timeout=timeout_seconds)
    if worker.is_alive():
        raise TimeoutError("Timed out running async host-state operation in worker thread")
    if holder["error"] is not None:
        raise holder["error"]
    return holder["value"]


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
    def emit_step_status_changed(
        issue: str, workflow_id: str, step_id: str, agent_type: str, status: str
    ) -> None:
        """Broadcast a step status change via SocketIO."""
        HostStateManager.emit_transition(
            "step_status_changed",
            {
                "issue": issue,
                "workflow_id": workflow_id,
                "step_id": step_id,
                "agent_type": agent_type,
                "status": status,
                "timestamp": time.time(),
            },
        )

    @staticmethod
    def _load_json_state(path: str, default, ensure_logs: bool = False):
        """Load JSON state — routes to postgres or filesystem based on config."""
        if NEXUS_STORAGE_BACKEND == "postgres":
            backend = _get_storage_backend()
            if not backend:
                raise RuntimeError(
                    "NEXUS_STORAGE_BACKEND=postgres but postgres host-state backend is unavailable"
                )
            key = _host_state_key_from_path(path)
            result = _run_coro_sync(lambda: backend.load_host_state(key))
            return result if result is not None else default

        plugin = _get_state_store_plugin()
        if not plugin:
            return default
        try:
            if ensure_logs:
                ensure_logs_dir()
            else:
                ensure_state_dir()
        except PermissionError as exc:
            logger.warning("State/log directory setup skipped for read %s: %s", path, exc)
        return plugin.load_json(path, default=default)

    @staticmethod
    def _save_json_state(path: str, data, *, context: str, ensure_logs: bool = False) -> None:
        """Save JSON state — routes to postgres or filesystem based on config."""
        if NEXUS_STORAGE_BACKEND == "postgres":
            backend = _get_storage_backend()
            if not backend:
                raise RuntimeError(
                    "NEXUS_STORAGE_BACKEND=postgres but postgres host-state backend is unavailable"
                )
            key = _host_state_key_from_path(path)
            _run_coro_sync(lambda: backend.save_host_state(key, data))
            return

        plugin = _get_state_store_plugin()
        if not plugin:
            logger.error(f"State storage plugin unavailable; cannot save {context}")
            return
        try:
            if ensure_logs:
                ensure_logs_dir()
            else:
                ensure_state_dir()
        except PermissionError as exc:
            logger.warning("State/log directory setup skipped for save %s: %s", path, exc)
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
        data[key] = {"issue": issue_num, "agent": agent_name, "pid": pid, "timestamp": time.time()}
        HostStateManager.save_launched_agents(data)
        logger.info(f"Registered launched agent: {agent_name} (PID: {pid}) for issue #{issue_num}")
        HostStateManager.emit_transition(
            "agent_registered",
            {
                "issue": issue_num,
                "agent": agent_name,
                "pid": pid,
                "timestamp": data[key]["timestamp"],
            },
        )

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
    def load_merge_queue() -> dict[str, dict]:
        """Load persisted merge-queue entries."""
        data = HostStateManager._load_json_state(MERGE_QUEUE_FILE, default={}) or {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def save_merge_queue(data: dict[str, dict]) -> None:
        """Persist merge-queue entries."""
        HostStateManager._save_json_state(
            MERGE_QUEUE_FILE,
            data,
            context="merge queue",
        )

    @staticmethod
    def enqueue_merge_candidate(
        *,
        issue_num: str,
        project: str,
        repo: str,
        pr_url: str,
        review_mode: str,
        source: str = "workflow_complete",
    ) -> dict[str, Any]:
        """Insert or update a merge-queue entry for a PR."""
        pr_key = str(pr_url or "").strip()
        if not pr_key:
            raise ValueError("pr_url is required")

        normalized_mode = str(review_mode or "manual").strip().lower()
        if normalized_mode not in {"manual", "auto"}:
            normalized_mode = "manual"

        now = time.time()
        queue = HostStateManager.load_merge_queue()
        current = queue.get(pr_key, {}) if isinstance(queue.get(pr_key), dict) else {}

        item = {
            "pr_url": pr_key,
            "issue": str(issue_num),
            "project": str(project),
            "repo": str(repo or ""),
            "review_mode": normalized_mode,
            "status": (
                "pending_auto_merge" if normalized_mode == "auto" else "pending_manual_review"
            ),
            "source": str(source or "workflow_complete"),
            "created_at": float(current.get("created_at", now)),
            "updated_at": now,
        }
        queue[pr_key] = item
        HostStateManager.save_merge_queue(queue)
        HostStateManager.emit_transition("merge_queue_updated", item)
        return item

    @staticmethod
    def update_merge_candidate(pr_url: str, **changes: Any) -> dict[str, Any] | None:
        """Update a merge-queue entry by PR URL and persist it."""
        pr_key = str(pr_url or "").strip()
        if not pr_key:
            return None

        queue = HostStateManager.load_merge_queue()
        current = queue.get(pr_key)
        if not isinstance(current, dict):
            return None

        updated = dict(current)
        updated.update({k: v for k, v in changes.items() if v is not None})
        updated["updated_at"] = time.time()
        queue[pr_key] = updated
        HostStateManager.save_merge_queue(queue)
        HostStateManager.emit_transition("merge_queue_updated", updated)
        return updated

    @staticmethod
    def add_tracked_issue(issue_num: int, project: str, description: str) -> None:
        """Add an issue to tracking."""
        data = HostStateManager.load_tracked_issues()
        data[str(issue_num)] = {
            "project": project,
            "description": description,
            "created_at": time.time(),
            "status": "active",
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
