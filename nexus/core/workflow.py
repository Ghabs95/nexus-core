"""Basic workflow engine - simplified version for MVP."""
import logging
import re
from datetime import datetime
from typing import Any, Dict, Optional

import yaml

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
    
    Provides a minimal YAML-based loader that maps a workflow definition
    into Nexus Core's Workflow/WorkflowStep models.
    """

    @staticmethod
    def from_yaml(
        yaml_path: str,
        workflow_id: Optional[str] = None,
        name_override: Optional[str] = None,
        description_override: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Workflow:
        """Load workflow from a YAML file and return a Workflow object."""
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return WorkflowDefinition.from_dict(
            data,
            workflow_id=workflow_id,
            name_override=name_override,
            description_override=description_override,
            metadata=metadata,
        )

    @staticmethod
    def from_dict(
        data: Dict[str, Any],
        workflow_id: Optional[str] = None,
        name_override: Optional[str] = None,
        description_override: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Workflow:
        """Load workflow from a dict and return a Workflow object."""
        if not isinstance(data, dict):
            raise ValueError("Workflow definition must be a dict")

        name = name_override or data.get("name", "Unnamed Workflow")
        description = description_override or data.get("description", "")
        version = data.get("version", "1.0")

        resolved_id = workflow_id or WorkflowDefinition._slugify(name)
        if not resolved_id:
            raise ValueError("Workflow ID could not be resolved")

        steps_data = data.get("steps", [])
        if not isinstance(steps_data, list) or not steps_data:
            raise ValueError("Workflow definition must include a non-empty steps list")

        steps = []
        for idx, step_data in enumerate(steps_data, start=1):
            if not isinstance(step_data, dict):
                raise ValueError(f"Step {idx} must be a dict")

            agent_type = step_data.get("agent_type", "agent")
            step_name = step_data.get("id") or step_data.get("name") or f"step_{idx}"
            step_desc = step_data.get("description", "")
            prompt_template = step_data.get("prompt_template") or step_desc or "Execute step"

            agent = Agent(
                name=agent_type,
                display_name=step_data.get("name", agent_type),
                description=step_desc or f"Step {idx}",
                timeout=data.get("timeout_seconds", 600),
                max_retries=2,
            )

            inputs_data = step_data.get("inputs", {})
            if isinstance(inputs_data, list):
                normalized_inputs = {}
                for entry in inputs_data:
                    if isinstance(entry, dict):
                        normalized_inputs.update(entry)
                inputs_data = normalized_inputs

            steps.append(
                WorkflowStep(
                    step_num=idx,
                    name=WorkflowDefinition._slugify(step_name) or step_name,
                    agent=agent,
                    prompt_template=prompt_template,
                    condition=step_data.get("condition"),
                    inputs=inputs_data,
                )
            )

        workflow_metadata = {"definition": data}
        if metadata:
            workflow_metadata.update(metadata)

        return Workflow(
            id=resolved_id,
            name=name,
            version=version,
            description=description,
            steps=steps,
            metadata=workflow_metadata,
        )

    @staticmethod
    def _slugify(text: str) -> str:
        """Convert text into a safe workflow ID."""
        value = re.sub(r"[^a-zA-Z0-9_-]+", "-", text.strip().lower())
        return value.strip("-")
