"""Singleton factory for :class:`WorkflowStateStore`.

Selects the backend based on ``NEXUS_STORAGE_TYPE``:

- ``postgres`` → :class:`PostgresWorkflowStateStore` (requires ``NEXUS_STORAGE_DSN``)
- ``file`` (default) → :class:`FileWorkflowStateStore` backed by
    ``NEXUS_CORE_STORAGE_DIR``

Includes post-hook broadcasting via SocketIO when configured.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from config import NEXUS_CORE_STORAGE_DIR
from nexus.core.workflow_state import WorkflowStateStore

logger = logging.getLogger(__name__)

_instance: WorkflowStateStore | None = None


class _BroadcastingStore:
    """Thin decorator that adds SocketIO ``emit_transition`` calls."""

    def __init__(self, inner: WorkflowStateStore) -> None:
        self._inner = inner

    # ── Workflow mapping (with broadcast) ───────────────────────────

    def map_issue(self, issue_num: str, workflow_id: str) -> None:
        self._inner.map_issue(issue_num, workflow_id)
        self._emit("workflow_mapped", {
            "issue": issue_num,
            "workflow_id": workflow_id,
            "timestamp": time.time(),
        })

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
            issue_num, step_num, step_name, approvers, approval_timeout,
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
    storage_type = os.getenv("NEXUS_STORAGE_TYPE", "file").lower()

    if storage_type == "postgres":
        dsn = os.getenv("NEXUS_STORAGE_DSN", "")
        if not dsn:
            logger.warning(
                "NEXUS_STORAGE_TYPE=postgres but NEXUS_STORAGE_DSN is empty; "
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
    logger.info("Using FileWorkflowStateStore (base_path=%s)", NEXUS_CORE_STORAGE_DIR)
    return FileWorkflowStateStore(base_path=Path(NEXUS_CORE_STORAGE_DIR))  # type: ignore[return-value]


def get_workflow_state() -> WorkflowStateStore:
    """Return the shared :class:`WorkflowStateStore` singleton."""
    global _instance
    if _instance is None:
        inner = _build_inner_store()
        _instance = _BroadcastingStore(inner)  # type: ignore[assignment]
    return _instance

