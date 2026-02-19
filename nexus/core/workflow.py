"""Basic workflow engine - simplified version for MVP."""
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

import yaml

from nexus.adapters.storage.base import StorageBackend
from nexus.core.models import (
    Agent,
    AuditEvent,
    Workflow,
    WorkflowState,
    WorkflowStep,
    StepStatus,
)

logger = logging.getLogger(__name__)

# Type aliases for transition callbacks.
# on_step_transition(workflow, next_step, completed_step_outputs) -> None
OnStepTransition = Callable[[Workflow, WorkflowStep, dict], Awaitable[None]]
# on_workflow_complete(workflow, last_step_outputs) -> None
OnWorkflowComplete = Callable[[Workflow, dict], Awaitable[None]]


class WorkflowEngine:
    """
    Core workflow orchestration engine.
    
    Handles workflow execution, state management, and step progression.
    """

    def __init__(
        self,
        storage: StorageBackend,
        on_step_transition: Optional[OnStepTransition] = None,
        on_workflow_complete: Optional[OnWorkflowComplete] = None,
    ):
        """
        Initialize workflow engine.
        
        Args:
            storage: Storage backend for persistence
            on_step_transition: Async callback invoked when the next step is ready.
                Signature: ``async (workflow, next_step, completed_step_outputs) -> None``
            on_workflow_complete: Async callback invoked when the workflow finishes.
                Signature: ``async (workflow, last_step_outputs) -> None``
        """
        self.storage = storage
        self._on_step_transition = on_step_transition
        self._on_workflow_complete = on_workflow_complete

    async def create_workflow(self, workflow: Workflow) -> Workflow:
        """Create and persist a new workflow."""
        workflow.state = WorkflowState.PENDING
        workflow.created_at = datetime.now(timezone.utc)
        workflow.updated_at = datetime.now(timezone.utc)
        
        # Apply workflow-level approval gates to steps
        workflow.apply_approval_gates()
        
        await self.storage.save_workflow(workflow)
        await self._audit(workflow.id, "WORKFLOW_CREATED", {"name": workflow.name})
        
        logger.info(f"Created workflow {workflow.id}: {workflow.name}")
        return workflow

    async def get_workflow(self, workflow_id: str) -> Optional[Workflow]:
        """Retrieve a workflow by ID."""
        workflow = await self.storage.load_workflow(workflow_id)
        if workflow:
            # Ensure approval gates are applied (in case workflow was saved before gates existed)
            workflow.apply_approval_gates()
        return workflow

    async def start_workflow(self, workflow_id: str) -> Workflow:
        """Start workflow execution."""
        workflow = await self.storage.load_workflow(workflow_id)
        if not workflow:
            raise ValueError(f"Workflow {workflow_id} not found")
        
        if workflow.state != WorkflowState.PENDING:
            raise ValueError(f"Cannot start workflow in state {workflow.state.value}")
        
        workflow.state = WorkflowState.RUNNING
        workflow.updated_at = datetime.now(timezone.utc)
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
        workflow.updated_at = datetime.now(timezone.utc)
        
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
        workflow.updated_at = datetime.now(timezone.utc)
        
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
        step.completed_at = datetime.now(timezone.utc)
        step.outputs = outputs
        step.error = error
        step.status = StepStatus.FAILED if error else StepStatus.COMPLETED
        
        activated_step: Optional[WorkflowStep] = None

        if not error:
            # Build context from all completed steps for condition evaluation
            context = self._build_step_context(workflow)
            
            # Walk forward, skipping steps whose conditions evaluate to False
            next_step = workflow.get_next_step()
            while next_step:
                if self._evaluate_condition(next_step.condition, context):
                    # Condition passed (or no condition) – run this step
                    workflow.current_step = next_step.step_num
                    next_step.status = StepStatus.RUNNING
                    next_step.started_at = datetime.now(timezone.utc)
                    activated_step = next_step
                    break
                else:
                    # Condition failed – skip this step
                    next_step.status = StepStatus.SKIPPED
                    next_step.completed_at = datetime.now(timezone.utc)
                    await self._audit(workflow_id, "STEP_SKIPPED", {
                        "step_num": next_step.step_num,
                        "step_name": next_step.name,
                        "condition": next_step.condition,
                        "reason": f"Condition evaluated to False: {next_step.condition}",
                    })
                    logger.info(
                        f"Skipped step {next_step.step_num} ({next_step.name}) in workflow "
                        f"{workflow_id}: condition '{next_step.condition}' was False"
                    )
                    workflow.current_step = next_step.step_num
                    next_step = workflow.get_next_step()
            else:
                # No more steps
                workflow.state = WorkflowState.COMPLETED
                workflow.completed_at = datetime.now(timezone.utc)
        else:
            # Workflow failed
            workflow.state = WorkflowState.FAILED
            workflow.completed_at = datetime.now(timezone.utc)
        
        workflow.updated_at = datetime.now(timezone.utc)
        await self.storage.save_workflow(workflow)
        
        event_type = "STEP_FAILED" if error else "STEP_COMPLETED"
        await self._audit(workflow_id, event_type, {
            "step_num": step_num,
            "step_name": step.name,
            "error": error
        })
        
        logger.info(f"Completed step {step_num} in workflow {workflow_id}")

        # --- Fire transition callbacks ---
        if not error and activated_step and self._on_step_transition:
            try:
                await self._on_step_transition(workflow, activated_step, outputs)
            except Exception as exc:
                logger.error(
                    f"on_step_transition callback failed for workflow {workflow_id}, "
                    f"step {activated_step.step_num}: {exc}"
                )
        elif workflow.state == WorkflowState.COMPLETED and self._on_workflow_complete:
            try:
                await self._on_workflow_complete(workflow, outputs)
            except Exception as exc:
                logger.error(
                    f"on_workflow_complete callback failed for workflow {workflow_id}: {exc}"
                )

        return workflow

    def _build_step_context(self, workflow: Workflow) -> Dict[str, Any]:
        """Build evaluation context from all completed/skipped step outputs."""
        context: Dict[str, Any] = {}
        for step in workflow.steps:
            if step.status in (StepStatus.COMPLETED, StepStatus.SKIPPED):
                context[step.name] = step.outputs
                # Expose the most-recently-completed step as `result`
                if step.status == StepStatus.COMPLETED:
                    context["result"] = step.outputs
        return context

    def _evaluate_condition(self, condition: Optional[str], context: Dict[str, Any]) -> bool:
        """
        Evaluate a Python expression against the step context.

        Returns True when:
        - condition is None or empty (no condition → always run)
        - the expression evaluates to a truthy value

        Returns False when the expression evaluates to a falsy value.
        Logs a warning and returns True (safe default) if the expression raises.
        """
        if not condition:
            return True
        try:
            result = eval(condition, {"__builtins__": {}}, context)  # noqa: S307
            return bool(result)
        except Exception as exc:
            logger.warning(
                f"Condition evaluation error for '{condition}': {exc}. Defaulting to True."
            )
            return True

    async def get_audit_log(self, workflow_id: str) -> list:
        """Get audit log for a workflow."""
        return await self.storage.get_audit_log(workflow_id)

    async def _audit(self, workflow_id: str, event_type: str, data: dict) -> None:
        """Add audit event."""
        event = AuditEvent(
            workflow_id=workflow_id,
            timestamp=datetime.now(timezone.utc),
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

        # Parse workflow-level approval settings
        monitoring = data.get("monitoring", {})
        require_human_merge_approval = True  # Default to safe option
        if isinstance(monitoring, dict):
            require_human_merge_approval = monitoring.get("require_human_merge_approval", True)
        
        # Also check top-level (backwards compatibility)
        if "require_human_merge_approval" in data:
            require_human_merge_approval = data.get("require_human_merge_approval", True)

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

        workflow = Workflow(
            id=resolved_id,
            name=name,
            version=version,
            description=description,
            steps=steps,
            metadata=workflow_metadata,
            require_human_merge_approval=require_human_merge_approval,
        )
        
        # Apply workflow-level approval gates to all steps
        workflow.apply_approval_gates()
        
        return workflow

    @staticmethod
    def to_prompt_context(yaml_path: str) -> str:
        """Render workflow steps as a prompt-friendly checklist.

        Reads the YAML file and returns a Markdown formatted list of steps
        with their ``agent_type`` names, suitable for embedding in agent
        prompts so agents know the full workflow and use correct step names.

        Args:
            yaml_path: Path to the workflow YAML file.

        Returns:
            Formatted Markdown text, or empty string on error.
        """
        try:
            with open(yaml_path, "r") as f:
                data = yaml.safe_load(f)

            steps = data.get("steps", [])
            if not steps:
                return ""

            basename = os.path.basename(yaml_path)
            lines: List[str] = [f"**Workflow Steps (from {basename}):**\n"]
            for idx, step_data in enumerate(steps, 1):
                agent_type = step_data.get("agent_type", "unknown")
                name = step_data.get("name", step_data.get("id", f"Step {idx}"))
                desc = step_data.get("description", "")
                # Skip router steps — they're internal
                if agent_type == "router":
                    continue
                lines.append(f"- {idx}. **{name}** — `{agent_type}` : {desc}")

            lines.append(
                "\n**CRITICAL:** Use ONLY the agent_type names listed above. "
                "DO NOT use old agent names or reference other workflow YAML files."
            )
            return "\n".join(lines)
        except Exception as exc:
            logger.warning(f"Could not render workflow prompt context from {yaml_path}: {exc}")
            return ""

    @staticmethod
    def _slugify(text: str) -> str:
        """Convert text into a safe workflow ID."""
        value = re.sub(r"[^a-zA-Z0-9_-]+", "-", text.strip().lower())
        return value.strip("-")
