"""File-based storage backend (JSON files)."""
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

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

        # Create directories
        for directory in [self.workflows_dir, self.audit_dir, self.agent_dir]:
            directory.mkdir(parents=True, exist_ok=True)

    async def save_workflow(self, workflow: Workflow) -> None:
        """Save workflow to JSON file."""
        workflow_file = self.workflows_dir / f"{workflow.id}.json"
        
        # Convert workflow to dict (handle dataclasses and enums)
        data = self._workflow_to_dict(workflow)
        
        try:
            with open(workflow_file, "w") as f:
                json.dump(data, f, indent=2, default=str)
            logger.debug(f"Saved workflow {workflow.id} to {workflow_file}")
        except Exception as e:
            logger.error(f"Failed to save workflow {workflow.id}: {e}")
            raise

    async def load_workflow(self, workflow_id: str) -> Optional[Workflow]:
        """Load workflow from JSON file."""
        workflow_file = self.workflows_dir / f"{workflow_id}.json"
        
        if not workflow_file.exists():
            return None
        
        try:
            with open(workflow_file, "r") as f:
                data = json.load(f)
            workflow = self._dict_to_workflow(data)
            logger.debug(f"Loaded workflow {workflow_id}")
            return workflow
        except Exception as e:
            logger.error(f"Failed to load workflow {workflow_id}: {e}")
            return None

    async def list_workflows(
        self, state: Optional[WorkflowState] = None, limit: int = 100
    ) -> List[Workflow]:
        """List workflows, optionally filtered by state."""
        workflows = []
        
        for workflow_file in sorted(self.workflows_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            if len(workflows) >= limit:
                break
            
            try:
                with open(workflow_file, "r") as f:
                    data = json.load(f)
                workflow = self._dict_to_workflow(data)
                
                if state is None or workflow.state == state:
                    workflows.append(workflow)
            except Exception as e:
                logger.warning(f"Failed to load {workflow_file}: {e}")
        
        return workflows

    async def delete_workflow(self, workflow_id: str) -> bool:
        """Delete workflow file."""
        workflow_file = self.workflows_dir / f"{workflow_id}.json"
        
        if workflow_file.exists():
            workflow_file.unlink()
            logger.info(f"Deleted workflow {workflow_id}")
            return True
        return False

    async def append_audit_event(self, event: AuditEvent) -> None:
        """Append audit event to workflow's audit log file."""
        audit_file = self.audit_dir / f"{event.workflow_id}.jsonl"
        
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
        self, workflow_id: str, since: Optional[datetime] = None
    ) -> List[AuditEvent]:
        """Get audit log for a workflow."""
        audit_file = self.audit_dir / f"{workflow_id}.jsonl"
        
        if not audit_file.exists():
            return []
        
        events = []
        try:
            with open(audit_file, "r") as f:
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
        self, workflow_id: str, agent_name: str, metadata: Dict[str, Any]
    ) -> None:
        """Save agent execution metadata."""
        agent_file = self.agent_dir / f"{workflow_id}_{agent_name}.json"
        
        try:
            with open(agent_file, "w") as f:
                json.dump(metadata, f, indent=2, default=str)
            logger.debug(f"Saved agent metadata for {agent_name} in workflow {workflow_id}")
        except Exception as e:
            logger.error(f"Failed to save agent metadata: {e}")
            raise

    async def get_agent_metadata(
        self, workflow_id: str, agent_name: str
    ) -> Optional[Dict[str, Any]]:
        """Get agent execution metadata."""
        agent_file = self.agent_dir / f"{workflow_id}_{agent_name}.json"
        
        if not agent_file.exists():
            return None
        
        try:
            with open(agent_file, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load agent metadata: {e}")
            return None

    async def cleanup_old_workflows(self, older_than_days: int = 30) -> int:
        """Delete workflows older than specified days."""
        cutoff = datetime.utcnow() - timedelta(days=older_than_days)
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

    # Helper methods for serialization

    def _workflow_to_dict(self, workflow: Workflow) -> Dict[str, Any]:
        """Convert Workflow to dict for JSON serialization."""
        return {
            "id": workflow.id,
            "name": workflow.name,
            "version": workflow.version,
            "description": workflow.description,
            "state": workflow.state.value,
            "current_step": workflow.current_step,
            "created_at": workflow.created_at.isoformat(),
            "updated_at": workflow.updated_at.isoformat(),
            "completed_at": workflow.completed_at.isoformat() if workflow.completed_at else None,
            "metadata": workflow.metadata,
            "steps": [self._step_to_dict(step) for step in workflow.steps],
        }

    def _step_to_dict(self, step) -> Dict[str, Any]:
        """Convert WorkflowStep to dict."""
        from nexus.core.models import StepStatus
        
        return {
            "step_num": step.step_num,
            "name": step.name,
            "agent": {
                "name": step.agent.name,
                "display_name": step.agent.display_name,
                "description": step.agent.description,
                "provider_preference": step.agent.provider_preference,
                "timeout": step.agent.timeout,
                "max_retries": step.agent.max_retries,
            },
            "prompt_template": step.prompt_template,
            "condition": step.condition,
            "timeout": step.timeout,
            "retry": step.retry,
            "inputs": step.inputs,
            "outputs": step.outputs,
            "status": step.status.value,
            "started_at": step.started_at.isoformat() if step.started_at else None,
            "completed_at": step.completed_at.isoformat() if step.completed_at else None,
            "error": step.error,
        }

    def _dict_to_workflow(self, data: Dict[str, Any]) -> Workflow:
        """Convert dict to Workflow object."""
        from nexus.core.models import WorkflowStep, Agent, StepStatus
        
        steps = []
        for step_data in data.get("steps", []):
            agent = Agent(
                name=step_data["agent"]["name"],
                display_name=step_data["agent"]["display_name"],
                description=step_data["agent"]["description"],
                provider_preference=step_data["agent"].get("provider_preference"),
                timeout=step_data["agent"].get("timeout", 600),
                max_retries=step_data["agent"].get("max_retries", 3),
            )
            
            step = WorkflowStep(
                step_num=step_data["step_num"],
                name=step_data["name"],
                agent=agent,
                prompt_template=step_data["prompt_template"],
                condition=step_data.get("condition"),
                timeout=step_data.get("timeout"),
                retry=step_data.get("retry"),
                inputs=step_data.get("inputs", {}),
                outputs=step_data.get("outputs", {}),
                status=StepStatus(step_data.get("status", "pending")),
                started_at=datetime.fromisoformat(step_data["started_at"]) if step_data.get("started_at") else None,
                completed_at=datetime.fromisoformat(step_data["completed_at"]) if step_data.get("completed_at") else None,
                error=step_data.get("error"),
            )
            steps.append(step)
        
        return Workflow(
            id=data["id"],
            name=data["name"],
            version=data["version"],
            description=data.get("description", ""),
            steps=steps,
            state=WorkflowState(data["state"]),
            current_step=data.get("current_step", 0),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            completed_at=datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None,
            metadata=data.get("metadata", {}),
        )
