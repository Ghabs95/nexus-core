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
