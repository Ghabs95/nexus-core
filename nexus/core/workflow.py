"""Basic workflow engine - simplified version for MVP."""
import logging
import os
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import yaml

from nexus.adapters.storage.base import StorageBackend
from nexus.core.models import (
    Agent,
    AuditEvent,
    DryRunReport,
    StepStatus,
    Workflow,
    WorkflowState,
    WorkflowStep,
)

logger = logging.getLogger(__name__)

# Type aliases for transition callbacks.
# on_step_transition(workflow, next_step, completed_step_outputs) -> None
OnStepTransition = Callable[[Workflow, WorkflowStep, dict], Awaitable[None]]
# on_workflow_complete(workflow, last_step_outputs) -> None
OnWorkflowComplete = Callable[[Workflow, dict], Awaitable[None]]

_MAX_LOOP_ITERATIONS = 5  # Maximum times a step can be re-activated by a goto before aborting
_DEFAULT_BACKOFF_BASE = 1.0  # Default base delay (seconds) used when step.initial_delay is unset


class WorkflowEngine:
    """
    Core workflow orchestration engine.

    Handles workflow execution, state management, and step progression.
    """

    def __init__(
        self,
        storage: StorageBackend,
        on_step_transition: OnStepTransition | None = None,
        on_workflow_complete: OnWorkflowComplete | None = None,
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
        workflow.created_at = datetime.now(UTC)
        workflow.updated_at = datetime.now(UTC)

        # Apply workflow-level approval gates to steps
        workflow.apply_approval_gates()

        await self.storage.save_workflow(workflow)
        await self._audit(workflow.id, "WORKFLOW_CREATED", {"name": workflow.name})

        logger.info(f"Created workflow {workflow.id}: {workflow.name}")
        return workflow

    async def get_workflow(self, workflow_id: str) -> Workflow | None:
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
        workflow.updated_at = datetime.now(UTC)
        workflow.current_step = 0

        first_step = workflow.get_step(1)
        if first_step and first_step.status == StepStatus.PENDING:
            first_step.status = StepStatus.RUNNING
            first_step.started_at = datetime.now(UTC)
            workflow.current_step = first_step.step_num

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
        workflow.updated_at = datetime.now(UTC)

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
        workflow.updated_at = datetime.now(UTC)

        await self.storage.save_workflow(workflow)
        await self._audit(workflow_id, "WORKFLOW_RESUMED", {})

        logger.info(f"Resumed workflow {workflow_id}")
        return workflow

    async def cancel_workflow(self, workflow_id: str) -> Workflow:
        """Cancel workflow execution."""
        workflow = await self.storage.load_workflow(workflow_id)
        if not workflow:
            raise ValueError(f"Workflow {workflow_id} not found")

        if workflow.state not in (WorkflowState.RUNNING, WorkflowState.PAUSED):
            raise ValueError(f"Cannot cancel workflow in state {workflow.state.value}")

        workflow.state = WorkflowState.CANCELLED
        workflow.updated_at = datetime.now(UTC)
        workflow.completed_at = datetime.now(UTC)

        await self.storage.save_workflow(workflow)
        await self._audit(workflow_id, "WORKFLOW_CANCELLED", {})

        logger.info(f"Cancelled workflow {workflow_id}")
        return workflow

    async def complete_step(
        self, workflow_id: str, step_num: int, outputs: dict, error: str | None = None
    ) -> Workflow:
        """Mark a step as completed and advance workflow."""
        workflow = await self.storage.load_workflow(workflow_id)
        if not workflow:
            raise ValueError(f"Workflow {workflow_id} not found")

        step = workflow.get_step(step_num)
        if not step:
            raise ValueError(f"Step {step_num} not found in workflow")

        # Update step
        step.completed_at = datetime.now(UTC)
        step.outputs = outputs
        step.error = error

        if error:
            # Determine effective max retries (step-level overrides agent-level)
            max_retries = step.retry if step.retry is not None else step.agent.max_retries
            if step.retry_count < max_retries:
                step.retry_count += 1
                step.status = StepStatus.PENDING
                step.completed_at = None
                step.error = None
                # Compute backoff delay using step's configured strategy and initial_delay
                strategy = step.backoff_strategy or "exponential"
                base = step.initial_delay if step.initial_delay > 0 else _DEFAULT_BACKOFF_BASE
                if strategy == "linear":
                    backoff = min(base * step.retry_count, 60)
                elif strategy == "constant":
                    backoff = base
                else:  # exponential (default)
                    backoff = min(base * (2 ** (step.retry_count - 1)), 60)
                workflow.updated_at = datetime.now(UTC)
                await self.storage.save_workflow(workflow)
                await self._audit(workflow_id, "STEP_RETRY", {
                    "step_num": step_num,
                    "step_name": step.name,
                    "retry_count": step.retry_count,
                    "backoff_seconds": backoff,
                    "error": error,
                })
                logger.info(
                    f"Retrying step {step_num} in workflow {workflow_id} "
                    f"(attempt {step.retry_count}/{max_retries}, backoff {backoff}s)"
                )
                return workflow
            step.status = StepStatus.FAILED
        else:
            step.status = StepStatus.COMPLETED

        activated_step: WorkflowStep | None = None

        if not error:
            if step.final_step:
                workflow.state = WorkflowState.COMPLETED
                workflow.completed_at = datetime.now(UTC)
                workflow.updated_at = datetime.now(UTC)
                await self.storage.save_workflow(workflow)
                await self._audit(workflow_id, "STEP_COMPLETED", {
                    "step_num": step_num,
                    "step_name": step.name,
                    "error": error,
                })
                logger.info(f"Completed step {step_num} in workflow {workflow_id}")
                if self._on_workflow_complete:
                    try:
                        await self._on_workflow_complete(workflow, outputs)
                    except Exception as exc:
                        logger.error(
                            f"on_workflow_complete callback failed for workflow {workflow_id}: {exc}"
                        )
                return workflow

            # Build context once; walk forward handling routers, conditions, and gotos
            context = self._build_step_context(workflow)
            next_step: WorkflowStep | None = None
            if step.on_success:
                next_step = self._find_step_by_name(workflow, step.on_success)
                if next_step is None:
                    logger.warning(
                        "Step %s in workflow %s has unresolved on_success target '%s'; "
                        "falling back to sequential progression",
                        step.name,
                        workflow_id,
                        step.on_success,
                    )
                elif next_step.status != StepStatus.PENDING:
                    try:
                        self._reset_step_for_goto(next_step)
                    except RuntimeError as exc:
                        logger.error(str(exc))
                        workflow.state = WorkflowState.FAILED
                        workflow.completed_at = datetime.now(UTC)
                        await self.storage.save_workflow(workflow)
                        return workflow

            if next_step is None:
                next_step = workflow.get_next_step()

            while next_step:
                # --- Router step: evaluate routes and jump to target ---
                if next_step.routes:
                    next_step.status = StepStatus.SKIPPED
                    next_step.completed_at = datetime.now(UTC)
                    await self._audit(workflow_id, "STEP_SKIPPED", {
                        "step_num": next_step.step_num,
                        "step_name": next_step.name,
                        "reason": "router evaluated",
                    })
                    workflow.current_step = next_step.step_num
                    target = self._resolve_route(workflow, next_step, context)
                    if target is None:
                        # No route matched and no default — workflow is done
                        workflow.state = WorkflowState.COMPLETED
                        workflow.completed_at = datetime.now(UTC)
                        break
                    try:
                        self._reset_step_for_goto(target)
                    except RuntimeError as exc:
                        logger.error(str(exc))
                        workflow.state = WorkflowState.FAILED
                        workflow.completed_at = datetime.now(UTC)
                        await self.storage.save_workflow(workflow)
                        return workflow
                    next_step = target
                    continue

                # --- Normal step: evaluate optional condition ---
                if self._evaluate_condition(next_step.condition, context):
                    # Condition passed (or no condition) – run this step
                    workflow.current_step = next_step.step_num
                    next_step.status = StepStatus.RUNNING
                    next_step.started_at = datetime.now(UTC)
                    activated_step = next_step
                    break
                else:
                    # Condition failed – skip this step
                    next_step.status = StepStatus.SKIPPED
                    next_step.completed_at = datetime.now(UTC)
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
                workflow.completed_at = datetime.now(UTC)
        else:
            # Workflow failed
            workflow.state = WorkflowState.FAILED
            workflow.completed_at = datetime.now(UTC)

        workflow.updated_at = datetime.now(UTC)
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

    @staticmethod
    def render_prompt(template: str, context: dict[str, Any]) -> str:
        """Substitute ``{variable}`` placeholders in *template* with values from *context*.

        Uses :py:meth:`str.format_map` with a safe mapping so that unknown
        placeholders are left intact rather than raising :py:exc:`KeyError`.

        Args:
            template: Prompt template string, e.g. ``"Fix issue: {description}"``.
            context: Mapping of variable names to replacement values.

        Returns:
            The rendered string with known placeholders replaced.
        """

        class _SafeDict(dict):  # type: ignore[type-arg]
            def __missing__(self, key: str) -> str:
                return "{" + key + "}"

        return template.format_map(_SafeDict(context))

    def _resolve_route(
        self, workflow: Workflow, router_step: WorkflowStep, context: dict[str, Any]
    ) -> WorkflowStep | None:
        """Evaluate a router step's routes and return the matching target WorkflowStep.

        Route dict supports either ``goto:`` (YAML convention) or ``then:`` (alias).
        A ``default: true`` entry is used when no ``when:`` clause matches.
        """
        default_target: str | None = None
        for route in router_step.routes:
            when: str | None = route.get("when")
            # Support both "goto" (YAML convention) and "then" (alias)
            target_name: str | None = route.get("goto") or route.get("then")
            is_default: bool = bool(route.get("default")) and not when
            if is_default:
                # "default" can be a step name directly (``default: "develop"``)
                # or a boolean flag alongside ``goto:``/``then:`` (``default: true, goto: "develop"``).
                default_val = route.get("default")
                default_target = target_name or (default_val if isinstance(default_val, str) else None)
                continue
            if when and target_name and self._evaluate_condition(
                when,
                context,
                default_on_error=False,
            ):
                return self._find_step_by_name(workflow, target_name)
        if default_target:
            return self._find_step_by_name(workflow, default_target)
        return None

    def _find_step_by_name(self, workflow: Workflow, name: str) -> WorkflowStep | None:
        """Find a step by its slugified id or agent_type name."""
        slug = WorkflowDefinition._slugify(name)
        for step in workflow.steps:
            if step.name == name or step.name == slug or step.agent.name == name:
                return step
        return None

    def _reset_step_for_goto(self, step: WorkflowStep) -> None:
        """Reset a step so it can be re-executed (loop / goto support).

        Raises RuntimeError if the step has already been re-activated
        _MAX_LOOP_ITERATIONS times to guard against infinite loops.
        """
        if step.iteration >= _MAX_LOOP_ITERATIONS:
            raise RuntimeError(
                f"Step '{step.name}' has been re-activated {step.iteration} times "
                f"(limit {_MAX_LOOP_ITERATIONS}). Aborting to prevent infinite loop."
            )
        step.iteration += 1
        step.status = StepStatus.PENDING
        step.started_at = None
        step.completed_at = None
        step.error = None
        step.outputs = {}
        step.retry_count = 0

    def _build_step_context(self, workflow: Workflow) -> dict[str, Any]:
        """Build evaluation context from all completed/skipped step outputs.

        The context contains:
        - ``<step_id>``: the outputs dict for that step (keyed by step id)
        - ``result``: alias for the most-recently-completed step's outputs
        - top-level keys from the most-recently-completed step's outputs,
          so simple YAML conditions like ``approval_status == 'approved'``
          work without needing to qualify the key with the step id.
        """
        context: dict[str, Any] = {}
        for step in workflow.steps:
            if step.status in (StepStatus.COMPLETED, StepStatus.SKIPPED):
                context[step.name] = step.outputs
                # Expose the most-recently-completed step as `result`
                if step.status == StepStatus.COMPLETED:
                    context["result"] = step.outputs
                    # Also flatten keys at the top level for ergonomic conditions
                    # e.g. `approval_status == 'approved'` instead of
                    # `result['approval_status'] == 'approved'`
                    context.update(step.outputs)
        return context

    def _evaluate_condition(
        self,
        condition: str | None,
        context: dict[str, Any],
        default_on_error: bool = True,
    ) -> bool:
        """
        Evaluate a Python expression against the step context.

        Returns True when:
        - condition is None or empty (no condition → always run)
        - the expression evaluates to a truthy value

        Returns False when the expression evaluates to a falsy value.
        Logs a warning and returns *default_on_error* if the expression raises.
        """
        if not condition:
            return True
        try:
            eval_locals = dict(context)
            eval_locals.setdefault("true", True)
            eval_locals.setdefault("false", False)
            eval_locals.setdefault("null", None)
            result = eval(condition, {"__builtins__": {}}, eval_locals)  # noqa: S307
            return bool(result)
        except Exception as exc:
            logger.warning(
                "Condition evaluation error for '%s': %s. Defaulting to %s.",
                condition,
                exc,
                default_on_error,
            )
            return default_on_error

    async def get_audit_log(self, workflow_id: str) -> list:
        """Get audit log for a workflow."""
        return await self.storage.get_audit_log(workflow_id)

    async def get_runnable_steps(self, workflow_id: str) -> list[WorkflowStep]:
        """Return all steps that are currently ready to run in parallel.

        A step is *runnable* when it is in ``PENDING`` state and its step number
        equals ``workflow.current_step`` **or** its ``parallel_with`` list contains
        the name of the step at position ``workflow.current_step`` (indicating it
        is part of a parallel group).

        This method does not mutate workflow state; callers must explicitly start
        each step via the normal engine flow.

        Args:
            workflow_id: ID of the target workflow.

        Returns:
            List of :class:`~nexus.core.models.WorkflowStep` objects that can be
            started concurrently.  Returns an empty list when the workflow is not
            found or is not running.
        """
        workflow = await self.storage.load_workflow(workflow_id)
        if not workflow or workflow.state != WorkflowState.RUNNING:
            return []

        current = workflow.get_step(workflow.current_step)
        if current is None:
            return []

        runnable: list[WorkflowStep] = []

        # Include the current step if it is still pending (not yet started)
        if current.status == StepStatus.PENDING:
            runnable.append(current)

        # Include any pending steps that declare themselves parallel to the current step
        current_name = current.name
        for step in workflow.steps:
            if step.step_num == workflow.current_step:
                continue  # Already handled above
            if step.status == StepStatus.PENDING and current_name in step.parallel_with:
                runnable.append(step)

        return runnable

    async def _audit(self, workflow_id: str, event_type: str, data: dict) -> None:
        """Add audit event."""
        event = AuditEvent(
            workflow_id=workflow_id,
            timestamp=datetime.now(UTC),
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

    _TERMINAL_NEXT_AGENT_VALUES = {
        "none",
        "n/a",
        "null",
        "no",
        "end",
        "done",
        "finish",
        "complete",
        "",
    }

    @staticmethod
    def normalize_workflow_type(tier_name: str, default: str = "shortened") -> str:
        """Normalize a workflow type string.

        Strips whitespace and lowercases.  Returns *default* when the
        input is empty.  No validation against a fixed list — the
        workflow definition YAML is the source of truth for valid types.
        """
        cleaned = tier_name.strip().lower()
        return cleaned if cleaned else default

    @staticmethod
    def _resolve_steps(data: dict[str, Any], workflow_type: str = "") -> list[dict]:
        """Resolve the steps list from a workflow definition.

        Supports two layouts:
        - **Flat**: a top-level ``steps`` list.
        - **Tiered**: keyed under ``<type>_workflow.steps`` sections
          (e.g. ``full_workflow``, ``shortened_workflow``, etc.).

        When *workflow_type* is provided, the engine first consults the
        optional ``workflow_types`` mapping in the YAML to resolve the
        external label to a section key prefix, then looks for the
        corresponding ``<prefix>_workflow`` section.  Hyphens in the
        resolved prefix are normalised to underscores for key lookup.

        When empty, the flat ``steps`` list is used; if that is absent,
        the first available ``*_workflow`` section is returned as a
        fallback.

        Args:
            data: Parsed workflow YAML dict.
            workflow_type: Optional tier selector.

        Returns:
            The resolved list of step dicts (may be empty).
        """
        if workflow_type:
            # 1. Consult the optional workflow_types mapping in the YAML
            workflow_types_mapping = data.get("workflow_types", {})
            mapped_type = workflow_types_mapping.get(workflow_type, workflow_type)

            # 2. Normalise hyphens → underscores for YAML key lookup
            key_prefix = mapped_type.replace("-", "_")
            keys_to_try = [
                f"{key_prefix}_workflow",
                key_prefix,
                f"{mapped_type}_workflow",
                mapped_type,
            ]
            seen: set = set()
            for key in keys_to_try:
                if key in seen:
                    continue
                seen.add(key)
                tier = data.get(key, {})
                if isinstance(tier, dict) and tier.get("steps"):
                    return tier["steps"]

            return []

        # No workflow_type specified — prefer flat steps
        flat = data.get("steps", [])
        if flat:
            return flat

        # Fallback: pick the first available *_workflow section
        for key, value in data.items():
            if (
                key.endswith("_workflow")
                and isinstance(value, dict)
                and value.get("steps")
            ):
                return value["steps"]

        return []

    @staticmethod
    def from_yaml(
        yaml_path: str,
        workflow_id: str | None = None,
        name_override: str | None = None,
        description_override: str | None = None,
        metadata: dict[str, Any] | None = None,
        workflow_type: str = "",
    ) -> Workflow:
        """Load workflow from a YAML file and return a Workflow object.

        Args:
            yaml_path: Path to the workflow YAML file.
            workflow_id: Optional explicit workflow ID.
            name_override: Override the workflow name from the file.
            description_override: Override the workflow description.
            metadata: Extra metadata to attach.
            workflow_type: Tier selector (``"full"``, ``"shortened"``,
                ``"fast-track"``).  When empty, uses flat ``steps:`` or
                falls back to the first available tier.
        """
        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return WorkflowDefinition.from_dict(
            data,
            workflow_id=workflow_id,
            name_override=name_override,
            description_override=description_override,
            metadata=metadata,
            workflow_type=workflow_type,
        )

    @staticmethod
    def from_dict(
        data: dict[str, Any],
        workflow_id: str | None = None,
        name_override: str | None = None,
        description_override: str | None = None,
        metadata: dict[str, Any] | None = None,
        workflow_type: str = "",
    ) -> Workflow:
        """Load workflow from a dict and return a Workflow object.

        Args:
            data: Parsed workflow dict.
            workflow_id: Optional explicit workflow ID.
            name_override: Override the workflow name.
            description_override: Override the description.
            metadata: Extra metadata to attach.
            workflow_type: Tier selector (see ``_resolve_steps``).
        """
        if not isinstance(data, dict):
            raise ValueError("Workflow definition must be a dict")

        name = name_override or data.get("name", "Unnamed Workflow")
        description = description_override or data.get("description", "")
        version = data.get("version", "1.0")

        resolved_id = workflow_id or WorkflowDefinition._slugify(name)
        if not resolved_id:
            raise ValueError("Workflow ID could not be resolved")

        steps_data = WorkflowDefinition._resolve_steps(data, workflow_type)
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

            # Resolve retry: explicit `retry` integer or `retry_policy.max_retries`
            step_retry: int | None = step_data.get("retry")
            retry_policy = step_data.get("retry_policy")
            step_backoff_strategy: str | None = None
            step_initial_delay: float = 0.0
            if isinstance(retry_policy, dict):
                if step_retry is None:
                    step_retry = retry_policy.get("max_retries")
                step_backoff_strategy = retry_policy.get("backoff")
                raw_delay = retry_policy.get("initial_delay", 0.0)
                try:
                    step_initial_delay = float(raw_delay) if raw_delay else 0.0
                except (TypeError, ValueError):
                    step_initial_delay = 0.0

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

            # `parallel` field: list of step ids that form a parallel group with this step
            parallel_raw = step_data.get("parallel", [])
            if isinstance(parallel_raw, list):
                parallel_with: list[str] = [
                    WorkflowDefinition._slugify(step_id) or step_id
                    for step_id in parallel_raw
                ]
            else:
                parallel_with = []

            step_routes = step_data.get("routes", [])
            steps.append(
                WorkflowStep(
                    step_num=idx,
                    name=WorkflowDefinition._slugify(step_name) or step_name,
                    agent=agent,
                    prompt_template=prompt_template,
                    condition=step_data.get("condition"),
                    retry=step_retry,
                    backoff_strategy=step_backoff_strategy,
                    initial_delay=step_initial_delay,
                    inputs=inputs_data,
                    routes=step_routes,
                    on_success=step_data.get("on_success"),
                    final_step=bool(step_data.get("final_step", False)),
                    parallel_with=parallel_with,
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
    def resolve_next_agents(
        yaml_path: str,
        current_agent_type: str,
        workflow_type: str = "",
    ) -> list[str]:
        """Resolve valid next agent_types for a given agent_type from the workflow.

        Follows ``on_success`` links and router ``routes`` to determine which
        agent_types can legitimately follow *current_agent_type*.

        Args:
            yaml_path: Path to workflow YAML file.
            current_agent_type: The agent_type whose successors we want.
            workflow_type: Tier selector (see ``_resolve_steps``).

        Returns:
            List of valid next agent_type strings (may include ``"none"`` for
            terminal steps).  Empty list if the workflow can't be parsed or
            the agent_type is not found.
        """
        try:
            with open(yaml_path) as f:
                data = yaml.safe_load(f)
        except Exception:
            return []

        steps = WorkflowDefinition._resolve_steps(data, workflow_type)
        if not steps:
            return []

        # Build id → step lookup
        by_id: dict[str, dict] = {s["id"]: s for s in steps if "id" in s}

        # Find the step(s) matching current_agent_type
        current_steps = [s for s in steps if s.get("agent_type") == current_agent_type]
        if not current_steps:
            return []

        result: list[str] = []
        for step in current_steps:
            on_success = step.get("on_success")
            if step.get("final_step"):
                result.append("none")
                continue
            if not on_success:
                result.append("none")
                continue

            target = by_id.get(on_success)
            if not target:
                continue

            # If target is a router, expand its routes
            if target.get("agent_type") == "router":
                for route in target.get("routes", []):
                    route_target_id = route.get("then") or route.get("default")
                    if route_target_id and route_target_id in by_id:
                        result.append(by_id[route_target_id].get("agent_type", "unknown"))
                    elif route_target_id:
                        # route_target_id may itself be an agent_type
                        result.append(route_target_id)
                # Also get default route
                default_route = target.get("default")
                if default_route and default_route in by_id:
                    result.append(by_id[default_route].get("agent_type", "unknown"))
            else:
                result.append(target.get("agent_type", "unknown"))

        # Deduplicate while preserving order
        seen: set = set()
        unique: list[str] = []
        for agent in result:
            if agent not in seen:
                seen.add(agent)
                unique.append(agent)
        return unique

    @staticmethod
    def canonicalize_next_agent(
        yaml_path: str,
        current_agent_type: str,
        proposed_next_agent: str,
        workflow_type: str = "",
    ) -> str:
        """Canonicalize a proposed next_agent into a valid successor agent_type.

        Handles common model output drift:
        - ``@agent`` mention formatting
        - step IDs / names instead of agent_type
        - invalid value when there is only one valid successor

        Returns empty string when ambiguous or invalid.
        """

        def _normalize(value: str) -> str:
            text = (value or "").strip()
            text = text.lstrip("@").strip()
            return text.strip("`").strip()

        candidate = _normalize(proposed_next_agent)
        if candidate.lower() in WorkflowDefinition._TERMINAL_NEXT_AGENT_VALUES:
            return "none"

        valid_next = WorkflowDefinition.resolve_next_agents(
            yaml_path,
            current_agent_type,
            workflow_type=workflow_type,
        )
        if not valid_next:
            return ""

        if candidate in valid_next:
            return candidate

        try:
            with open(yaml_path) as handle:
                data = yaml.safe_load(handle)
        except Exception:
            return valid_next[0] if len(valid_next) == 1 else ""

        steps = WorkflowDefinition._resolve_steps(data, workflow_type)
        candidate_lc = candidate.lower()
        for step in steps:
            step_id = str(step.get("id", "")).strip().lower()
            step_name = str(step.get("name", "")).strip().lower()
            if candidate_lc in (step_id, step_name):
                mapped = str(step.get("agent_type", "")).strip()
                if mapped in valid_next:
                    return mapped

        if len(valid_next) == 1:
            return valid_next[0]

        return ""

    @staticmethod
    def to_prompt_context(
        yaml_path: str,
        current_agent_type: str = "",
        workflow_type: str = "",
    ) -> str:
        """Render workflow steps as a prompt-friendly checklist.

        Reads the YAML file and returns a Markdown formatted list of steps
        with their ``agent_type`` names, suitable for embedding in agent
        prompts so agents know the full workflow and use correct step names.

        When *current_agent_type* is provided, the returned text includes an
        explicit directive stating which agent_type(s) are valid for the
        ``next_agent`` field in the completion summary.

        Args:
            yaml_path: Path to the workflow YAML file.
            current_agent_type: If set, resolves and embeds valid next agents.
            workflow_type: Tier selector (see ``_resolve_steps``).

        Returns:
            Formatted Markdown text, or empty string on error.
        """
        try:
            with open(yaml_path) as f:
                data = yaml.safe_load(f)

            steps = WorkflowDefinition._resolve_steps(data, workflow_type)
            if not steps:
                return ""

            basename = os.path.basename(yaml_path)
            tier_label = f" [{workflow_type}]" if workflow_type else ""
            lines: list[str] = [f"**Workflow Steps{tier_label} (from {basename}):**\n"]
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

            # Build display-name mapping (agent_type → Capitalized)
            # Used by agents for the "Ready for @..." comment line.
            seen: set = set()
            display_pairs: list[str] = []
            for step_data in steps:
                at = step_data.get("agent_type", "")
                if at and at != "router" and at not in seen:
                    seen.add(at)
                    display_pairs.append(f"`{at}` → **{at.title()}**")
            if display_pairs:
                lines.append(
                    "\n**Display Names (for the 'Ready for @...' line in your comment):**\n"
                    + ", ".join(display_pairs)
                )

            # Resolve and embed next-agent constraint
            if current_agent_type:
                valid_next = WorkflowDefinition.resolve_next_agents(
                    yaml_path, current_agent_type, workflow_type=workflow_type
                )
                if valid_next:
                    names = ", ".join(f"`{a}`" for a in valid_next)
                    if len(valid_next) == 1:
                        lines.append(
                            f"\n**YOUR next_agent MUST be:** {names}\n"
                            f"Do NOT skip ahead or pick a different agent."
                        )
                    else:
                        lines.append(
                            f"\n**YOUR next_agent MUST be one of:** {names}\n"
                            f"Choose based on your classification. "
                            f"Do NOT skip ahead or pick a different agent."
                        )

            return "\n".join(lines)
        except Exception as exc:
            logger.warning(f"Could not render workflow prompt context from {yaml_path}: {exc}")
            return ""

    @staticmethod
    def dry_run(
        data: dict[str, Any],
        workflow_type: str = "",
    ) -> "DryRunReport":
        """Validate a workflow definition dict and simulate step execution.

        No agents or tools are invoked.  Returns a :class:`DryRunReport`
        containing detected configuration errors and the predicted step
        execution order.

        Validation checks:
        - Top-level ``name`` or ``id`` field is present.
        - The resolved steps list is non-empty.
        - Each step has a non-empty ``agent_type``.
        - ``on_success`` references point to steps that exist in the definition.
        - ``condition`` expressions are syntactically valid Python.

        Simulation:
        - Walks the steps in declaration order (skipping ``router`` steps).
        - Evaluates each condition with an empty context; a condition that
          raises a :class:`NameError` (because referenced outputs don't exist
          yet) is treated as **RUN** (unknown → conservative).
        - Any other evaluation error marks the step as SKIP.

        Args:
            data: Parsed workflow definition dict.
            workflow_type: Tier selector (see ``_resolve_steps``).

        Returns:
            A :class:`DryRunReport` with ``errors`` and ``predicted_flow``.
        """
        errors: list[str] = []
        predicted_flow: list[str] = []

        if not isinstance(data, dict):
            return DryRunReport(errors=["Workflow definition must be a dict"])

        # --- Top-level field validation ---
        if not data.get("name") and not data.get("id"):
            errors.append("Missing required top-level field: 'name' or 'id'")

        # --- Steps validation ---
        steps = WorkflowDefinition._resolve_steps(data, workflow_type)
        if not steps:
            errors.append(
                f"No steps found for workflow_type={workflow_type!r}. "
                "Check that the workflow definition contains a non-empty steps list."
            )
        else:
            step_ids = {s["id"] for s in steps if isinstance(s, dict) and "id" in s}

            for idx, step in enumerate(steps, start=1):
                if not isinstance(step, dict):
                    errors.append(f"Step {idx}: must be a dict, got {type(step).__name__}")
                    continue

                step_label = step.get("id") or step.get("name") or f"step_{idx}"

                # agent_type presence
                agent_type = step.get("agent_type", "")
                if not agent_type:
                    errors.append(f"Step '{step_label}': missing 'agent_type'")

                # on_success reference validity (only when step IDs are used)
                on_success = step.get("on_success")
                if on_success and step_ids and on_success not in step_ids:
                    errors.append(
                        f"Step '{step_label}': 'on_success' references unknown step id '{on_success}'"
                    )

                # condition syntax check
                condition = step.get("condition")
                if condition:
                    try:
                        compile(condition, "<condition>", "eval")
                    except SyntaxError as exc:
                        errors.append(
                            f"Step '{step_label}': malformed condition expression "
                            f"'{condition}' — {exc}"
                        )

        # --- Simulation ---
        for idx, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                continue
            agent_type = step.get("agent_type", "")
            if agent_type == "router":
                continue

            step_label = step.get("name") or step.get("id") or f"step_{idx}"
            condition = step.get("condition")

            if not condition:
                predicted_flow.append(f"RUN  {step_label} ({agent_type})")
                continue

            # Simulate with empty context; NameError → RUN (outputs not available yet)
            try:
                result = eval(condition, {"__builtins__": {}}, {})  # noqa: S307
                status = "RUN " if result else "SKIP"
            except NameError:
                # References to step outputs that don't exist yet → treat as RUN
                status = "RUN "
            except Exception:
                status = "SKIP"

            predicted_flow.append(f"{status} {step_label} ({agent_type}) [condition: {condition}]")

        return DryRunReport(errors=errors, predicted_flow=predicted_flow)

    @staticmethod
    def _slugify(text: str) -> str:
        """Convert text into a safe workflow ID."""
        value = re.sub(r"[^a-zA-Z0-9_-]+", "-", text.strip().lower())
        return value.strip("-")
