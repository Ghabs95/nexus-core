"""Workflow ↔ dict serialization helpers shared across storage backends.

Both :class:`FileStorage` and :class:`PostgreSQLStorageBackend` (and any
future backends) import from here so the serialization logic lives in one
place.
"""
from datetime import datetime
from typing import Any, Dict

from nexus.core.models import (
    Agent,
    StepStatus,
    Workflow,
    WorkflowState,
    WorkflowStep,
)


def workflow_to_dict(workflow: Workflow) -> Dict[str, Any]:
    """Serialize a :class:`Workflow` instance to a plain dict."""
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
        "steps": [step_to_dict(step) for step in workflow.steps],
    }


def step_to_dict(step: WorkflowStep) -> Dict[str, Any]:
    """Serialize a :class:`WorkflowStep` to a plain dict."""
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
        "routes": step.routes,
        "on_success": step.on_success,
        "final_step": step.final_step,
        "iteration": step.iteration,
    }


def dict_to_workflow(data: Dict[str, Any]) -> Workflow:
    """Deserialize a plain dict to a :class:`Workflow` instance."""
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
            started_at=(
                datetime.fromisoformat(step_data["started_at"])
                if step_data.get("started_at") else None
            ),
            completed_at=(
                datetime.fromisoformat(step_data["completed_at"])
                if step_data.get("completed_at") else None
            ),
            error=step_data.get("error"),
            routes=step_data.get("routes", []),
            on_success=step_data.get("on_success"),
            final_step=bool(step_data.get("final_step", False)),
            iteration=step_data.get("iteration", 0),
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
        completed_at=(
            datetime.fromisoformat(data["completed_at"])
            if data.get("completed_at") else None
        ),
        metadata=data.get("metadata", {}),
    )
