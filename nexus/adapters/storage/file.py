"""File-based storage backend (JSON files)."""

import json
import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from nexus.adapters.storage._workflow_serde import dict_to_workflow, workflow_to_dict
from nexus.adapters.storage.base import StorageBackend
from nexus.core.models import AuditEvent, Workflow, WorkflowState

logger = logging.getLogger(__name__)


class FileStorage(StorageBackend):
    """File-based storage using JSON files."""

    def __init__(self, base_path: str | Path):
        """
        Initialize file storage.

        Args:
            base_path: Base directory for storing workflow data
        """
        self.base_path = Path(base_path)
        self.workflows_dir = self.base_path / "workflows"
        self.audit_dir = self.base_path / "audit"
        self.agent_dir = self.base_path / "agents"
        self.completions_dir = self.base_path / "completions"
        self.host_state_dir = self.base_path / "host_state"
        self.workflow_mapping_file = self.base_path / "workflow_mapping.json"
        self.approval_state_file = self.base_path / "approval_state.json"

        # Create directories
        for directory in [
            self.workflows_dir,
            self.audit_dir,
            self.agent_dir,
            self.completions_dir,
            self.host_state_dir,
        ]:
            directory.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, base_dir: Path, filename: str) -> Path:
        """Resolve filename within base_dir and ensure it doesn't escape."""
        # Use Path.name to strip any directory components from the filename
        safe_name = Path(filename).name
        resolved = (base_dir / safe_name).resolve()
        if base_dir.resolve() not in resolved.parents:
            raise ValueError(f"Security: path traversal detected for filename {filename!r}")
        return resolved

    async def save_workflow(self, workflow: Workflow) -> None:
        """Save workflow to JSON file."""
        workflow_file = self._safe_path(self.workflows_dir, f"{workflow.id}.json")

        # Convert workflow to dict (handle dataclasses and enums)
        data = self._workflow_to_dict(workflow)

        try:
            with open(workflow_file, "w") as f:
                json.dump(data, f, indent=2, default=str)
            logger.debug(f"Saved workflow {workflow.id} to {workflow_file}")
        except Exception as e:
            logger.error(f"Failed to save workflow {workflow.id}: {e}")
            raise

    async def load_workflow(self, workflow_id: str) -> Workflow | None:
        """Load workflow from JSON file."""
        try:
            workflow_file = self._safe_path(self.workflows_dir, f"{workflow_id}.json")
        except ValueError:
            return None

        if not workflow_file.exists():
            return None

        try:
            with open(workflow_file) as f:
                data = json.load(f)
            workflow = self._dict_to_workflow(data)
            logger.debug(f"Loaded workflow {workflow_id}")
            return workflow
        except Exception as e:
            logger.error(f"Failed to load workflow {workflow_id}: {e}")
            return None

    async def list_workflows(
        self, state: WorkflowState | None = None, limit: int = 100
    ) -> list[Workflow]:
        """List workflows, optionally filtered by state."""
        workflows = []

        for workflow_file in sorted(
            self.workflows_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
        ):
            if len(workflows) >= limit:
                break

            try:
                with open(workflow_file) as f:
                    data = json.load(f)
                workflow = self._dict_to_workflow(data)

                if state is None or workflow.state == state:
                    workflows.append(workflow)
            except Exception as e:
                logger.warning(f"Failed to load {workflow_file}: {e}")

        return workflows

    async def delete_workflow(self, workflow_id: str) -> bool:
        """Delete workflow file."""
        try:
            workflow_file = self._safe_path(self.workflows_dir, f"{workflow_id}.json")
        except ValueError:
            return False

        if workflow_file.exists():
            workflow_file.unlink()
            logger.info(f"Deleted workflow {workflow_id}")
            return True
        return False

    async def append_audit_event(self, event: AuditEvent) -> None:
        """Append audit event to workflow's audit log file."""
        audit_file = self._safe_path(self.audit_dir, f"{event.workflow_id}.jsonl")

        try:
            event_data = {
                "workflow_id": event.workflow_id,
                "timestamp": event.timestamp.isoformat(),
                "event_type": event.event_type,
                "data": event.data,
                "user_id": event.user_id,
            }

            with open(audit_file, "a") as f:
                f.write(json.dumps(event_data) + "\n")

            logger.debug(f"Appended audit event {event.event_type} to {audit_file}")
        except Exception as e:
            logger.error(f"Failed to append audit event: {e}")
            raise

    async def get_audit_log(
        self, workflow_id: str, since: datetime | None = None
    ) -> list[AuditEvent]:
        """Get audit log for a workflow."""
        try:
            audit_file = self._safe_path(self.audit_dir, f"{workflow_id}.jsonl")
        except ValueError:
            return []

        if not audit_file.exists():
            return []

        events = []
        try:
            with open(audit_file) as f:
                for line in f:
                    if not line.strip():
                        continue

                    data = json.loads(line)
                    timestamp = datetime.fromisoformat(data["timestamp"])

                    if since and timestamp < since:
                        continue

                    event = AuditEvent(
                        workflow_id=data["workflow_id"],
                        timestamp=timestamp,
                        event_type=data["event_type"],
                        data=data["data"],
                        user_id=data.get("user_id"),
                    )
                    events.append(event)

            return events
        except Exception as e:
            logger.error(f"Failed to read audit log for {workflow_id}: {e}")
            return []

    async def save_agent_metadata(
        self, workflow_id: str, agent_name: str, metadata: dict[str, Any]
    ) -> None:
        """Save agent execution metadata."""
        agent_file = self._safe_path(self.agent_dir, f"{workflow_id}_{agent_name}.json")

        try:
            with open(agent_file, "w") as f:
                json.dump(metadata, f, indent=2, default=str)
            logger.debug(f"Saved agent metadata for {agent_name} in workflow {workflow_id}")
        except Exception as e:
            logger.error(f"Failed to save agent metadata: {e}")
            raise

    async def get_agent_metadata(self, workflow_id: str, agent_name: str) -> dict[str, Any] | None:
        """Get agent execution metadata."""
        try:
            agent_file = self._safe_path(self.agent_dir, f"{workflow_id}_{agent_name}.json")
        except ValueError:
            return None

        if not agent_file.exists():
            return None

        try:
            with open(agent_file) as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load agent metadata: {e}")
            return None

    async def cleanup_old_workflows(self, older_than_days: int = 30) -> int:
        """Delete workflows older than specified days."""
        cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
        deleted = 0

        for workflow_file in self.workflows_dir.glob("*.json"):
            try:
                mtime = datetime.fromtimestamp(workflow_file.stat().st_mtime)
                if mtime < cutoff:
                    workflow_file.unlink()
                    deleted += 1
                    logger.debug(f"Deleted old workflow {workflow_file.name}")
            except Exception as e:
                logger.warning(f"Failed to delete {workflow_file}: {e}")

        logger.info(f"Cleaned up {deleted} old workflows")
        return deleted

    async def save_completion(
        self, issue_number: str, agent_type: str, data: dict[str, Any]
    ) -> str:
        """Persist completion summary payload to JSON file."""
        dedup_key = f"{issue_number}:{agent_type}:{data.get('status', 'complete')}"
        completion_file = self._safe_path(self.completions_dir, f"{issue_number}.json")

        payload = {
            **dict(data or {}),
            "_dedup_key": dedup_key,
            "_issue_number": str(issue_number),
            "_agent_type": str(agent_type),
            "_updated_at": datetime.now(UTC).isoformat(),
        }

        with open(completion_file, "w") as f:
            json.dump(payload, f, indent=2, default=str)

        return dedup_key

    async def list_completions(self, issue_number: str | None = None) -> list[dict[str, Any]]:
        """List latest completion payloads, newest first."""
        completion_files: list[Path]

        if issue_number:
            try:
                one_file = self._safe_path(self.completions_dir, f"{issue_number}.json")
                completion_files = [one_file] if one_file.exists() else []
            except ValueError:
                completion_files = []
        else:
            completion_files = sorted(
                self.completions_dir.glob("*.json"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )

        completions: list[dict[str, Any]] = []
        for completion_file in completion_files:
            try:
                with open(completion_file) as f:
                    completions.append(json.load(f))
            except Exception as e:
                logger.warning(f"Failed to load completion {completion_file}: {e}")

        return completions

    async def save_host_state(self, key: str, data: dict[str, Any]) -> None:
        """Persist host state blob by key."""
        host_state_file = self._safe_path(self.host_state_dir, f"{key}.json")
        with open(host_state_file, "w") as f:
            json.dump(data, f, indent=2, default=str)

    async def load_host_state(self, key: str) -> dict[str, Any] | None:
        """Load host state blob by key."""
        try:
            host_state_file = self._safe_path(self.host_state_dir, f"{key}.json")
        except ValueError:
            return None

        if not host_state_file.exists():
            return None

        try:
            with open(host_state_file) as f:
                payload = json.load(f)
            return payload if isinstance(payload, dict) else None
        except Exception as e:
            logger.warning(f"Failed to load host state {host_state_file}: {e}")
            return None

    async def map_issue_to_workflow(self, issue_num: str, workflow_id: str) -> None:
        data = self._read_json_dict(self.workflow_mapping_file)
        data[str(issue_num)] = str(workflow_id)
        self._write_json_dict(self.workflow_mapping_file, data)

    async def get_workflow_id_for_issue(self, issue_num: str) -> str | None:
        data = self._read_json_dict(self.workflow_mapping_file)
        workflow_id = data.get(str(issue_num))
        return str(workflow_id) if isinstance(workflow_id, str) else None

    async def remove_issue_workflow_mapping(self, issue_num: str) -> None:
        data = self._read_json_dict(self.workflow_mapping_file)
        data.pop(str(issue_num), None)
        self._write_json_dict(self.workflow_mapping_file, data)

    async def load_issue_workflow_mappings(self) -> dict[str, str]:
        data = self._read_json_dict(self.workflow_mapping_file)
        return {str(k): str(v) for k, v in data.items() if isinstance(v, str)}

    async def set_pending_workflow_approval(
        self,
        issue_num: str,
        step_num: int,
        step_name: str,
        approvers: list[str],
        approval_timeout: int,
    ) -> None:
        data = self._read_json_dict(self.approval_state_file)
        data[str(issue_num)] = {
            "step_num": int(step_num),
            "step_name": str(step_name),
            "approvers": list(approvers),
            "approval_timeout": int(approval_timeout),
            "requested_at": time.time(),
        }
        self._write_json_dict(self.approval_state_file, data)

    async def clear_pending_workflow_approval(self, issue_num: str) -> None:
        data = self._read_json_dict(self.approval_state_file)
        data.pop(str(issue_num), None)
        self._write_json_dict(self.approval_state_file, data)

    async def get_pending_workflow_approval(self, issue_num: str) -> dict[str, Any] | None:
        data = self._read_json_dict(self.approval_state_file)
        pending = data.get(str(issue_num))
        return pending if isinstance(pending, dict) else None

    async def load_pending_workflow_approvals(self) -> dict[str, dict[str, Any]]:
        data = self._read_json_dict(self.approval_state_file)
        return {str(k): v for k, v in data.items() if isinstance(v, dict)}

    # Helper methods for serialization

    def _workflow_to_dict(self, workflow: Workflow) -> dict[str, Any]:
        """Delegate to shared serde module."""
        return workflow_to_dict(workflow)

    def _step_to_dict(self, step) -> dict[str, Any]:
        """Delegate to shared serde module."""
        from nexus.adapters.storage._workflow_serde import step_to_dict

        return step_to_dict(step)

    def _dict_to_workflow(self, data: dict[str, Any]) -> Workflow:
        """Delegate to shared serde module."""
        return dict_to_workflow(data)

    @staticmethod
    def _read_json_dict(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            with open(path) as f:
                payload = json.load(f)
            return payload if isinstance(payload, dict) else {}
        except Exception as e:
            logger.warning(f"Failed to read JSON dict from {path}: {e}")
            return {}

    @staticmethod
    def _write_json_dict(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".tmp")
        with open(tmp_path, "w") as f:
            json.dump(payload, f, indent=2, default=str)
        tmp_path.replace(path)
