"""Basic workflow engine - simplified version for MVP."""
import logging
from datetime import datetime
from typing import Optional

from nexus.adapters.storage.base import StorageBackend
from nexus.core.models import (
    AuditEvent,
    Workflow,
    WorkflowState,
    StepStatus,
)

logger = logging.getLogger(__name__)


class WorkflowEngine:
    """
    Core workflow orchestration engine.
    
    Handles workflow execution, state management, and step progression.
    """

    def __init__(self, storage: StorageBackend):
        """
        Initialize workflow engine.
        
        Args:
            storage: Storage backend for persistence
        """
        self.storage = storage

    async def create_workflow(self, workflow: Workflow) -> Workflow:
        """Create and persist a new workflow."""
        workflow.state = WorkflowState.PENDING
        workflow.created_at = datetime.utcnow()
        workflow.updated_at = datetime.utcnow()
        
        await self.storage.save_workflow(workflow)
        await self._audit(workflow.id, "WORKFLOW_CREATED", {"name": workflow.name})
        
        logger.info(f"Created workflow {workflow.id}: {workflow.name}")
        return workflow

    async def get_workflow(self, workflow_id: str) -> Optional[Workflow]:
        """Retrieve a workflow by ID."""
        return await self.storage.load_workflow(workflow_id)

    async def start_workflow(self, workflow_id: str) -> Workflow:
        """Start workflow execution."""
        workflow = await self.storage.load_workflow(workflow_id)
        if not workflow:
            raise ValueError(f"Workflow {workflow_id} not found")
        
        if workflow.state != WorkflowState.PENDING:
            raise ValueError(f"Cannot start workflow in state {workflow.state.value}")
        
        workflow.state = WorkflowState.RUNNING
        workflow.updated_at = datetime.utcnow()
        workflow.current_step = 0
        
        await self.storage.save_workflow(workflow)
        await self._audit(workflow_id, "WORKFLOW_STARTED", {})
        
        logger.info(f"Started workflow {workflow_id}")
        return workflow

    async def pause_workflow(self, workflow_id: str) -> Workflow:
        """Pause workflow execution."""
        workflow = await self.storage.load_workflow(workflow_id)
        if not workflow:
            raise ValueError(f"Workflow {workflow_id} not found")
        
        if workflow.state != WorkflowState.RUNNING:
            raise ValueError(f"Cannot pause workflow in state {workflow.state.value}")
        
        workflow.state = WorkflowState.PAUSED
        workflow.updated_at = datetime.utcnow()
        
        await self.storage.save_workflow(workflow)
        await self._audit(workflow_id, "WORKFLOW_PAUSED", {})
        
        logger.info(f"Paused workflow {workflow_id}")
        return workflow

    async def resume_workflow(self, workflow_id: str) -> Workflow:
        """Resume paused workflow."""
        workflow = await self.storage.load_workflow(workflow_id)
        if not workflow:
            raise ValueError(f"Workflow {workflow_id} not found")
        
        if workflow.state != WorkflowState.PAUSED:
            raise ValueError(f"Cannot resume workflow in state {workflow.state.value}")
        
        workflow.state = WorkflowState.RUNNING
        workflow.updated_at = datetime.utcnow()
        
        await self.storage.save_workflow(workflow)
        await self._audit(workflow_id, "WORKFLOW_RESUMED", {})
        
        logger.info(f"Resumed workflow {workflow_id}")
        return workflow

    async def complete_step(
        self, workflow_id: str, step_num: int, outputs: dict, error: Optional[str] = None
    ) -> Workflow:
        """Mark a step as completed and advance workflow."""
        workflow = await self.storage.load_workflow(workflow_id)
        if not workflow:
            raise ValueError(f"Workflow {workflow_id} not found")
        
        step = workflow.get_step(step_num)
        if not step:
            raise ValueError(f"Step {step_num} not found in workflow")
        
        # Update step
        step.completed_at = datetime.utcnow()
        step.outputs = outputs
        step.error = error
        step.status = StepStatus.FAILED if error else StepStatus.COMPLETED
        
        # Advance to next step or complete workflow
        next_step = workflow.get_next_step()
        if next_step and not error:
            workflow.current_step = next_step.step_num
            next_step.status = StepStatus.RUNNING
            next_step.started_at = datetime.utcnow()
        else:
            # Workflow complete or failed
            workflow.state = WorkflowState.FAILED if error else WorkflowState.COMPLETED
            workflow.completed_at = datetime.utcnow()
        
        workflow.updated_at = datetime.utcnow()
        await self.storage.save_workflow(workflow)
        
        event_type = "STEP_FAILED" if error else "STEP_COMPLETED"
        await self._audit(workflow_id, event_type, {
            "step_num": step_num,
            "step_name": step.name,
            "error": error
        })
        
        logger.info(f"Completed step {step_num} in workflow {workflow_id}")
        return workflow

    async def get_audit_log(self, workflow_id: str) -> list:
        """Get audit log for a workflow."""
        return await self.storage.get_audit_log(workflow_id)

    async def _audit(self, workflow_id: str, event_type: str, data: dict) -> None:
        """Add audit event."""
        event = AuditEvent(
            workflow_id=workflow_id,
            timestamp=datetime.utcnow(),
            event_type=event_type,
            data=data
        )
        await self.storage.append_audit_event(event)


class WorkflowDefinition:
    """
    Workflow definition loader (from YAML/JSON).
    
    This is a placeholder for future YAML/JSON-based workflow definitions.
    For MVP, workflows are created programmatically.
    """

    @staticmethod
    def from_yaml(yaml_path: str) -> Workflow:
        """Load workflow from YAML file (TODO)."""
        raise NotImplementedError("YAML workflow loading not yet implemented")

    @staticmethod
    def from_dict(data: dict) -> Workflow:
        """Load workflow from dict (TODO)."""
        raise NotImplementedError("Dict workflow loading not yet implemented")
