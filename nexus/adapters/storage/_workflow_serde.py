"""Workflow ↔ dict serialization helpers shared across storage backends.

Both :class:`FileStorage` and :class:`PostgreSQLStorageBackend` (and any
future backends) import from here so the serialization logic lives in one
place.
"""

from datetime import datetime
from typing import Any

from nexus.core.models import (
    Agent,
    StepStatus,
    Workflow,
    WorkflowState,
    WorkflowStep,
)

_TRUTHY_STRINGS = {"1", "true", "yes", "on"}
_FALSY_STRINGS = {"0", "false", "no", "off"}


def _parse_bool(value: Any, default: bool) -> bool:
    """Parse bool-like serialized values with stable string handling."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUTHY_STRINGS:
            return True
        if normalized in _FALSY_STRINGS:
            return False
        return default
    if isinstance(value, (int, float)):
        return value != 0
    return default


def workflow_to_dict(workflow: Workflow) -> dict[str, Any]:
    """Serialize a :class:`Workflow` instance to a plain dict."""
    return {
        "id": workflow.id,
        "name": workflow.name,
        "version": workflow.version,
        "schema_version": workflow.schema_version,
        "description": workflow.description,
        "state": workflow.state.value,
        "current_step": workflow.current_step,
        "created_at": workflow.created_at.isoformat(),
        "updated_at": workflow.updated_at.isoformat(),
        "completed_at": workflow.completed_at.isoformat() if workflow.completed_at else None,
        "metadata": workflow.metadata,
        "orchestration": workflow.orchestration.to_dict(),
        "steps": [step_to_dict(step) for step in workflow.steps],
    }


def step_to_dict(step: WorkflowStep) -> dict[str, Any]:
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


def dict_to_workflow(data: dict[str, Any]) -> Workflow:
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
                if step_data.get("started_at")
                else None
            ),
            completed_at=(
                datetime.fromisoformat(step_data["completed_at"])
                if step_data.get("completed_at")
                else None
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
        schema_version=str(data.get("schema_version", "1.0")),
        description=data.get("description", ""),
        steps=steps,
        state=WorkflowState(data["state"]),
        current_step=data.get("current_step", 0),
        created_at=datetime.fromisoformat(data["created_at"]),
        updated_at=datetime.fromisoformat(data["updated_at"]),
        completed_at=(
            datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None
        ),
        metadata=data.get("metadata", {}),
        orchestration=_deserialize_orchestration(data.get("orchestration")),
    )


def _deserialize_orchestration(data: Any):
    from nexus.core.models import WorkflowOrchestrationConfig

    if not isinstance(data, dict):
        return WorkflowOrchestrationConfig()
    return WorkflowOrchestrationConfig(
        interval_seconds=int(data.get("interval_seconds", 15)),
        completion_glob=str(
            data.get(
                "completion_glob",
                ".nexus/tasks/nexus/completions/completion_summary_*.json",
            )
        ),
        dedupe_cache_size=int(data.get("dedupe_cache_size", 500)),
        default_agent_timeout_seconds=int(data.get("default_agent_timeout_seconds", 3600)),
        liveness_miss_threshold=int(data.get("liveness_miss_threshold", 3)),
        timeout_action=str(data.get("timeout_action", "retry")),
        chaining_enabled=_parse_bool(data.get("chaining_enabled"), True),
        require_completion_comment=_parse_bool(data.get("require_completion_comment"), True),
        block_on_closed_issue=_parse_bool(data.get("block_on_closed_issue"), True),
        max_retries_per_step=int(data.get("max_retries_per_step", 2)),
        backoff=str(data.get("backoff", "exponential")),
        initial_delay_seconds=float(data.get("initial_delay_seconds", 1.0)),
        stale_running_step_action=str(data.get("stale_running_step_action", "reconcile")),
    )
