"""Singleton factory for :class:`WorkflowStateStore`.

Selects the backend based on configured workflow backend:

- ``postgres`` → :class:`PostgresWorkflowStateStore` (requires ``NEXUS_STORAGE_DSN``)
- ``file`` (default) → :class:`FileWorkflowStateStore` backed by
    ``NEXUS_CORE_STORAGE_DIR``

Includes post-hook broadcasting via SocketIO when configured.
"""

from __future__ import annotations

import builtins
import logging
import os
import time
from pathlib import Path

import config

from nexus.adapters.storage.base import StorageBackend
from nexus.core.workflow_state import WorkflowStateStore

logger = logging.getLogger(__name__)

_instance: WorkflowStateStore | None = None
_storage_backend_instance: StorageBackend | None = None
_BUILTINS_STORAGE_BACKEND_KEY = "__nexus_storage_backend_instance"


def _cfg(name: str, default: str = "") -> str:
    return str(getattr(config, name, default))


def _resolve_storage_dir() -> str:
    """Resolve workflow/storage directory with runtime overrides.

    Priority:
    1. Explicit environment override (NEXUS_CORE_STORAGE_DIR)
    2. DATA_DIR compatibility fallback (DATA_DIR/nexus-core)
    3. Config constant if present and non-empty
    """
    env_value = str(os.getenv("NEXUS_CORE_STORAGE_DIR", "")).strip()
    if env_value:
        return env_value

    data_dir = str(getattr(config, "DATA_DIR", "")).strip()
    if data_dir:
        return str(Path(data_dir) / "nexus-core")

    cfg_value = _cfg("NEXUS_CORE_STORAGE_DIR").strip()
    if cfg_value:
        return cfg_value

    return str(Path(".nexus") / "nexus-core")


class _BroadcastingStore:
    """Thin decorator that adds SocketIO ``emit_transition`` calls."""

    def __init__(self, inner: WorkflowStateStore) -> None:
        self._inner = inner

    # ── Workflow mapping (with broadcast) ───────────────────────────

    def map_issue(self, issue_num: str, workflow_id: str) -> None:
        self._inner.map_issue(issue_num, workflow_id)
        self._emit(
            "workflow_mapped",
            {
                "issue": issue_num,
                "workflow_id": workflow_id,
                "timestamp": time.time(),
            },
        )

    def get_workflow_id(self, issue_num: str) -> str | None:
        return self._inner.get_workflow_id(issue_num)

    def remove_mapping(self, issue_num: str) -> None:
        self._inner.remove_mapping(issue_num)

    def load_all_mappings(self) -> dict[str, str]:
        return self._inner.load_all_mappings()

    # ── Approval gate (pass-through) ────────────────────────────────

    def set_pending_approval(
        self,
        issue_num: str,
        step_num: int,
        step_name: str,
        approvers: list[str],
        approval_timeout: int,
    ) -> None:
        self._inner.set_pending_approval(
            issue_num,
            step_num,
            step_name,
            approvers,
            approval_timeout,
        )

    def clear_pending_approval(self, issue_num: str) -> None:
        self._inner.clear_pending_approval(issue_num)

    def get_pending_approval(self, issue_num: str) -> dict | None:
        return self._inner.get_pending_approval(issue_num)

    def load_all_approvals(self) -> dict[str, dict]:
        return self._inner.load_all_approvals()

    # ── SocketIO helper ─────────────────────────────────────────────

    @staticmethod
    def _emit(event_type: str, data: dict) -> None:
        from state_manager import _socketio_emitter

        if _socketio_emitter is not None:
            try:
                _socketio_emitter(event_type, data)
            except Exception as exc:
                logger.warning("SocketIO emit failed for %s: %s", event_type, exc)


def _build_inner_store() -> WorkflowStateStore:
    """Build the concrete store based on environment configuration."""
    storage_type = _cfg("NEXUS_WORKFLOW_BACKEND").strip().lower()

    if storage_type == "postgres":
        dsn = _cfg("NEXUS_STORAGE_DSN")
        if not dsn:
            logger.warning(
                "NEXUS_WORKFLOW_BACKEND=postgres but NEXUS_STORAGE_DSN is empty; "
                "falling back to file-based workflow state store"
            )
        else:
            from nexus.adapters.storage.postgres_workflow_state import (
                PostgresWorkflowStateStore,
            )

            logger.info("Using PostgresWorkflowStateStore")
            return PostgresWorkflowStateStore(connection_string=dsn)  # type: ignore[return-value]

    # Default: file-based
    from nexus.adapters.storage.file_workflow_state import FileWorkflowStateStore

    storage_dir = _resolve_storage_dir()
    logger.info("Using FileWorkflowStateStore (base_path=%s)", storage_dir)
    return FileWorkflowStateStore(base_path=Path(storage_dir))  # type: ignore[return-value]


def get_workflow_state() -> WorkflowStateStore:
    """Return the shared :class:`WorkflowStateStore` singleton."""
    global _instance
    if _instance is None:
        inner = _build_inner_store()
        _instance = _BroadcastingStore(inner)  # type: ignore[assignment]
    return _instance


def get_storage_backend() -> StorageBackend:
    """Return shared StorageBackend for host-state persistence.

    Used by :mod:`state_manager` for keys like ``launched_agents`` and
    ``tracked_issues``.
    """
    global _storage_backend_instance
    if _storage_backend_instance is not None:
        return _storage_backend_instance

    # Cross-module singleton guard:
    # in some host setups the same source can be imported under multiple module paths.
    # Store the backend in builtins so duplicate imports still reuse one process instance.
    existing = getattr(builtins, _BUILTINS_STORAGE_BACKEND_KEY, None)
    if existing is not None:
        _storage_backend_instance = existing
        return _storage_backend_instance

    storage_type = _cfg("NEXUS_STORAGE_BACKEND").strip().lower()

    if storage_type == "postgres":
        dsn = _cfg("NEXUS_STORAGE_DSN")
        if not dsn:
            raise ValueError("NEXUS_STORAGE_BACKEND=postgres but NEXUS_STORAGE_DSN is empty")
        from nexus.adapters.storage.postgres import PostgreSQLStorageBackend

        logger.info("Using PostgreSQLStorageBackend for host state")
        _storage_backend_instance = PostgreSQLStorageBackend(
            connection_string=dsn,
        )
        setattr(builtins, _BUILTINS_STORAGE_BACKEND_KEY, _storage_backend_instance)
        return _storage_backend_instance

    from nexus.adapters.storage.file import FileStorage

    storage_dir = _resolve_storage_dir()
    logger.info("Using FileStorage for host state (base_path=%s)", storage_dir)
    _storage_backend_instance = FileStorage(base_path=Path(storage_dir))
    setattr(builtins, _BUILTINS_STORAGE_BACKEND_KEY, _storage_backend_instance)
    return _storage_backend_instance
