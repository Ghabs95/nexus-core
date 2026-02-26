"""Structured Logging decorator for Storage Backend.

Provides structured JSON logging for all audit events,
optimized for ingestion by Loki via Promtail or Fluentbit.
"""

import json
import logging
from datetime import datetime
from typing import Any

from nexus.adapters.storage.base import StorageBackend
from nexus.core.models import AuditEvent, Workflow, WorkflowState


class StructuredLogAuditBackend(StorageBackend):
    """
    Storage Backend decorator that emits structured JSON logs.

    Wraps an existing storage backend (like Postgres or FileStorage)
    and intercepts `append_audit_event` to emit structured logs
    for external observability tools like Loki or elasticsearch.
    """

    def __init__(
        self,
        backend: StorageBackend,
        logger_name: str = "nexus.audit.structured",
        extra_labels: dict[str, str] | None = None,
    ):
        """
        Initialize structured log decorator.

        Args:
            backend: The underlying StorageBackend to delegate to.
            logger_name: The logger to emit structured JSON logs to.
            extra_labels: Additional key-value pairs to include in every log payload
                          (e.g., {"app": "nexus", "env": "prod"}).
        """
        self._backend = backend
        self._logger = logging.getLogger(logger_name)
        self._extra_labels = extra_labels or {}

        # Ensure the logger doesn't propagate up if we only want it writing to a specific handler
        # Usually configured by the host application.

    async def append_audit_event(self, event: AuditEvent) -> None:
        """Append audit event and emit structured JSON log."""
        # 1. Delegate to underlying storage
        await self._backend.append_audit_event(event)

        # 2. Emit structured log
        try:
            log_payload = {
                "loki_type": "audit_event",
                "event_type": event.event_type,
                "workflow_id": event.workflow_id,
                "timestamp": event.timestamp.isoformat(),
                "user_id": event.user_id,
                "data": event.data,
            }
            # Inject any static labels configured (e.g., app, env)
            log_payload.update(self._extra_labels)

            # For Promtail/Fluentbit to scrape easily
            self._logger.info(json.dumps(log_payload))
        except Exception as e:
            # Never let telemetry failure break the workflow system
            logging.getLogger(__name__).warning(f"Failed to emit structured audit log: {e}")

    # --- Delegated Methods ---

    async def save_workflow(self, workflow: Workflow) -> None:
        await self._backend.save_workflow(workflow)

    async def load_workflow(self, workflow_id: str) -> Workflow | None:
        return await self._backend.load_workflow(workflow_id)

    async def list_workflows(
        self, state: WorkflowState | None = None, limit: int = 100
    ) -> list[Workflow]:
        return await self._backend.list_workflows(state, limit)

    async def delete_workflow(self, workflow_id: str) -> bool:
        return await self._backend.delete_workflow(workflow_id)

    async def get_audit_log(
        self, workflow_id: str, since: datetime | None = None
    ) -> list[AuditEvent]:
        return await self._backend.get_audit_log(workflow_id, since)

    async def save_agent_metadata(
        self, workflow_id: str, agent_name: str, metadata: dict[str, Any]
    ) -> None:
        await self._backend.save_agent_metadata(workflow_id, agent_name, metadata)

    async def get_agent_metadata(self, workflow_id: str, agent_name: str) -> dict[str, Any] | None:
        return await self._backend.get_agent_metadata(workflow_id, agent_name)

    async def cleanup_old_workflows(self, older_than_days: int = 30) -> int:
        return await self._backend.cleanup_old_workflows(older_than_days)

    async def save_completion(
        self, issue_number: str, agent_type: str, data: dict[str, Any]
    ) -> str:
        return await self._backend.save_completion(issue_number, agent_type, data)

    async def list_completions(self, issue_number: str | None = None) -> list[dict[str, Any]]:
        return await self._backend.list_completions(issue_number)

    async def save_host_state(self, key: str, data: dict[str, Any]) -> None:
        await self._backend.save_host_state(key, data)

    async def load_host_state(self, key: str) -> dict[str, Any] | None:
        return await self._backend.load_host_state(key)
