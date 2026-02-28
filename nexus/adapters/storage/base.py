"""Base interface for storage backends."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from nexus.core.models import AuditEvent, Workflow, WorkflowState


class StorageBackend(ABC):
    """Abstract storage backend for workflow state and audit logs."""

    @abstractmethod
    async def save_workflow(self, workflow: Workflow) -> None:
        """Persist workflow state."""
        pass

    @abstractmethod
    async def load_workflow(self, workflow_id: str) -> Workflow | None:
        """Load workflow state by ID."""
        pass

    @abstractmethod
    async def list_workflows(
        self, state: WorkflowState | None = None, limit: int = 100
    ) -> list[Workflow]:
        """List workflows, optionally filtered by state."""
        pass

    @abstractmethod
    async def delete_workflow(self, workflow_id: str) -> bool:
        """Delete workflow. Returns True if existed."""
        pass

    @abstractmethod
    async def append_audit_event(self, event: AuditEvent) -> None:
        """Append an audit log entry."""
        pass

    @abstractmethod
    async def get_audit_log(
        self, workflow_id: str, since: datetime | None = None
    ) -> list[AuditEvent]:
        """Get audit log for a workflow."""
        pass

    @abstractmethod
    async def save_agent_metadata(
        self, workflow_id: str, agent_name: str, metadata: dict[str, Any]
    ) -> None:
        """Save agent execution metadata (PID, timestamp, etc.)."""
        pass

    @abstractmethod
    async def get_agent_metadata(self, workflow_id: str, agent_name: str) -> dict[str, Any] | None:
        """Get agent execution metadata."""
        pass

    @abstractmethod
    async def cleanup_old_workflows(self, older_than_days: int = 30) -> int:
        """Delete workflows older than specified days. Returns count deleted."""
        pass

    # --- Completion storage ---

    async def save_completion(
        self, issue_number: str, agent_type: str, data: dict[str, Any]
    ) -> str:
        """Persist an agent completion summary.

        Args:
            issue_number: GitHub issue number.
            agent_type: Agent that produced the completion.
            data: Full completion payload (matches CompletionSummary schema).

        Returns:
            Dedup key for idempotent processing.
        """
        raise NotImplementedError("save_completion is not implemented by this storage backend")

    async def list_completions(self, issue_number: str | None = None) -> list[dict[str, Any]]:
        """List completion summaries, optionally filtered by issue.

        Returns newest-first for each issue (only latest per issue).
        """
        raise NotImplementedError("list_completions is not implemented by this storage backend")

    # --- Host state storage ---

    async def save_host_state(self, key: str, data: dict[str, Any]) -> None:
        """Persist a host state blob (e.g. launched_agents, tracked_issues)."""
        raise NotImplementedError("save_host_state is not implemented by this storage backend")

    async def load_host_state(self, key: str) -> dict[str, Any] | None:
        """Load a host state blob by key. Returns None if not found."""
        raise NotImplementedError("load_host_state is not implemented by this storage backend")

    # --- Issue workflow mapping / approval state storage ---

    async def map_issue_to_workflow(self, issue_num: str, workflow_id: str) -> None:
        """Persist a mapping between an issue number and workflow id."""
        raise NotImplementedError(
            "map_issue_to_workflow is not implemented by this storage backend"
        )

    async def get_workflow_id_for_issue(self, issue_num: str) -> str | None:
        """Return workflow id mapped to the issue number, if present."""
        raise NotImplementedError(
            "get_workflow_id_for_issue is not implemented by this storage backend"
        )

    async def remove_issue_workflow_mapping(self, issue_num: str) -> None:
        """Delete persisted issue->workflow mapping for the issue number."""
        raise NotImplementedError(
            "remove_issue_workflow_mapping is not implemented by this storage backend"
        )

    async def load_issue_workflow_mappings(self) -> dict[str, str]:
        """Load all issue->workflow mappings."""
        raise NotImplementedError(
            "load_issue_workflow_mappings is not implemented by this storage backend"
        )

    async def set_pending_workflow_approval(
        self,
        issue_num: str,
        step_num: int,
        step_name: str,
        approvers: list[str],
        approval_timeout: int,
    ) -> None:
        """Persist pending approval state for an issue workflow step."""
        raise NotImplementedError(
            "set_pending_workflow_approval is not implemented by this storage backend"
        )

    async def clear_pending_workflow_approval(self, issue_num: str) -> None:
        """Remove persisted pending approval state for an issue."""
        raise NotImplementedError(
            "clear_pending_workflow_approval is not implemented by this storage backend"
        )

    async def get_pending_workflow_approval(self, issue_num: str) -> dict[str, Any] | None:
        """Load pending approval state for an issue."""
        raise NotImplementedError(
            "get_pending_workflow_approval is not implemented by this storage backend"
        )

    async def load_pending_workflow_approvals(self) -> dict[str, dict[str, Any]]:
        """Load all pending approval states."""
        raise NotImplementedError(
            "load_pending_workflow_approvals is not implemented by this storage backend"
        )
