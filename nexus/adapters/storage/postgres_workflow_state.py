"""Postgres-backed WorkflowStateStore compatibility wrapper."""

from __future__ import annotations

from nexus.adapters.storage.postgres import PostgreSQLStorageBackend
from nexus.adapters.storage.workflow_state_adapter import StorageWorkflowStateStore


class PostgresWorkflowStateStore:
    """Backward-compatible postgres WorkflowStateStore via StorageBackend."""

    def __init__(
        self,
        connection_string: str,
        pool_size: int = 5,
        echo: bool = False,
    ) -> None:
        self._storage = PostgreSQLStorageBackend(
            connection_string=connection_string,
            pool_size=pool_size,
            echo=echo,
        )
        self._adapter = StorageWorkflowStateStore(self._storage)

    def map_issue(self, issue_num: str, workflow_id: str) -> None:
        self._adapter.map_issue(issue_num, workflow_id)

    def get_workflow_id(self, issue_num: str) -> str | None:
        return self._adapter.get_workflow_id(issue_num)

    def remove_mapping(self, issue_num: str) -> None:
        self._adapter.remove_mapping(issue_num)

    def load_all_mappings(self) -> dict[str, str]:
        return self._adapter.load_all_mappings()

    def set_pending_approval(
        self,
        issue_num: str,
        step_num: int,
        step_name: str,
        approvers: list[str],
        approval_timeout: int,
    ) -> None:
        self._adapter.set_pending_approval(
            issue_num=issue_num,
            step_num=step_num,
            step_name=step_name,
            approvers=approvers,
            approval_timeout=approval_timeout,
        )

    def clear_pending_approval(self, issue_num: str) -> None:
        self._adapter.clear_pending_approval(issue_num)

    def get_pending_approval(self, issue_num: str) -> dict | None:
        return self._adapter.get_pending_approval(issue_num)

    def load_all_approvals(self) -> dict[str, dict]:
        return self._adapter.load_all_approvals()

    def close(self) -> None:
        """Dispose underlying SQLAlchemy engine resources."""
        self._storage.close()

    def __del__(self) -> None:  # pragma: no cover - defensive finalizer
        try:
            self.close()
        except Exception:
            pass
