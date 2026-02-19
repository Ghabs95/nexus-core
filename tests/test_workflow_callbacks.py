"""Tests for WorkflowEngine transition callbacks and WorkflowDefinition.to_prompt_context()."""
import os
import pytest
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

from nexus.core.models import (
    Agent,
    AuditEvent,
    StepStatus,
    Workflow,
    WorkflowState,
    WorkflowStep,
)
from nexus.core.workflow import WorkflowEngine, WorkflowDefinition
from nexus.adapters.storage.base import StorageBackend


# ---------------------------------------------------------------------------
# Helpers (reused from test_conditional_steps)
# ---------------------------------------------------------------------------


def make_agent(name: str = "test_agent") -> Agent:
    return Agent(name=name, display_name=name, description="test", timeout=60, max_retries=1)


def make_step(step_num: int, name: str, condition: Optional[str] = None) -> WorkflowStep:
    return WorkflowStep(
        step_num=step_num,
        name=name,
        agent=make_agent(name),
        prompt_template="do something",
        condition=condition,
    )


def make_workflow(steps: List[WorkflowStep]) -> Workflow:
    wf = Workflow(
        id="wf-test",
        name="Test Workflow",
        version="1.0",
        steps=steps,
        state=WorkflowState.RUNNING,
        current_step=1,
    )
    if steps:
        steps[0].status = StepStatus.RUNNING
        steps[0].started_at = datetime.now(timezone.utc)
    return wf


class InMemoryStorage(StorageBackend):
    def __init__(self) -> None:
        self._workflows: Dict[str, Workflow] = {}
        self._audit: List[AuditEvent] = []

    async def save_workflow(self, workflow: Workflow) -> None:
        self._workflows[workflow.id] = workflow

    async def load_workflow(self, workflow_id: str) -> Optional[Workflow]:
        return self._workflows.get(workflow_id)

    async def list_workflows(self, state=None, limit: int = 100):
        return list(self._workflows.values())

    async def delete_workflow(self, workflow_id: str) -> bool:
        return bool(self._workflows.pop(workflow_id, None))

    async def append_audit_event(self, event: AuditEvent) -> None:
        self._audit.append(event)

    async def get_audit_log(self, workflow_id: str, since=None) -> List[AuditEvent]:
        return [e for e in self._audit if e.workflow_id == workflow_id]

    async def save_agent_metadata(self, wid: str, name: str, meta: Dict[str, Any]) -> None:
        pass

    async def get_agent_metadata(self, wid: str, name: str) -> Optional[Dict[str, Any]]:
        return None

    async def cleanup_old_workflows(self, older_than_days: int = 30) -> int:
        return 0


# ---------------------------------------------------------------------------
# Transition callback tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_step_transition_called():
    """on_step_transition fires when the next step is activated."""
    callback = AsyncMock()

    storage = InMemoryStorage()
    engine = WorkflowEngine(storage=storage, on_step_transition=callback)

    step1 = make_step(1, "analyze")
    step2 = make_step(2, "implement")
    wf = make_workflow([step1, step2])
    await storage.save_workflow(wf)

    await engine.complete_step("wf-test", step_num=1, outputs={"result": "ok"})

    callback.assert_awaited_once()
    args = callback.call_args[0]
    assert args[0].id == "wf-test"  # workflow
    assert args[1].step_num == 2  # next_step
    assert args[2] == {"result": "ok"}  # outputs


@pytest.mark.asyncio
async def test_on_workflow_complete_called():
    """on_workflow_complete fires when the last step finishes."""
    callback = AsyncMock()

    storage = InMemoryStorage()
    engine = WorkflowEngine(storage=storage, on_workflow_complete=callback)

    step1 = make_step(1, "only_step")
    wf = make_workflow([step1])
    await storage.save_workflow(wf)

    await engine.complete_step("wf-test", step_num=1, outputs={"done": True})

    callback.assert_awaited_once()
    args = callback.call_args[0]
    assert args[0].state == WorkflowState.COMPLETED
    assert args[1] == {"done": True}


@pytest.mark.asyncio
async def test_on_step_transition_not_called_on_error():
    """Callbacks should NOT fire when a step fails."""
    transition_cb = AsyncMock()
    complete_cb = AsyncMock()

    storage = InMemoryStorage()
    engine = WorkflowEngine(
        storage=storage,
        on_step_transition=transition_cb,
        on_workflow_complete=complete_cb,
    )

    step1 = make_step(1, "analyze")
    step2 = make_step(2, "implement")
    wf = make_workflow([step1, step2])
    await storage.save_workflow(wf)

    await engine.complete_step("wf-test", step_num=1, outputs={}, error="something broke")

    transition_cb.assert_not_awaited()
    complete_cb.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_workflow_complete_called_after_skip():
    """on_workflow_complete fires when the only remaining step is skipped."""
    callback = AsyncMock()

    storage = InMemoryStorage()
    engine = WorkflowEngine(storage=storage, on_workflow_complete=callback)

    step1 = make_step(1, "analyze")
    step2 = make_step(2, "optional", condition="result['tier'] == 'high'")
    wf = make_workflow([step1, step2])
    await storage.save_workflow(wf)

    result = await engine.complete_step("wf-test", step_num=1, outputs={"tier": "low"})

    assert result.state == WorkflowState.COMPLETED
    callback.assert_awaited_once()


@pytest.mark.asyncio
async def test_callback_exception_does_not_crash_engine():
    """If a callback raises, the engine should log but not propagate the error."""
    callback = AsyncMock(side_effect=RuntimeError("callback boom"))

    storage = InMemoryStorage()
    engine = WorkflowEngine(storage=storage, on_step_transition=callback)

    step1 = make_step(1, "analyze")
    step2 = make_step(2, "implement")
    wf = make_workflow([step1, step2])
    await storage.save_workflow(wf)

    # Should not raise
    result = await engine.complete_step("wf-test", step_num=1, outputs={})
    assert result.steps[1].status == StepStatus.RUNNING


# ---------------------------------------------------------------------------
# to_prompt_context tests
# ---------------------------------------------------------------------------


class TestToPromptContext:
    def test_renders_steps(self, tmp_path):
        workflow_yaml = tmp_path / "workflow.yaml"
        workflow_yaml.write_text(
            "name: Test Workflow\n"
            "steps:\n"
            "  - id: triage\n"
            "    name: Triage Issue\n"
            "    agent_type: triage\n"
            "    description: Classify severity\n"
            "  - id: implement\n"
            "    name: Implement Fix\n"
            "    agent_type: debug\n"
            "    description: Fix the bug\n"
        )
        result = WorkflowDefinition.to_prompt_context(str(workflow_yaml))
        assert "Triage Issue" in result
        assert "`triage`" in result
        assert "`debug`" in result
        assert "CRITICAL" in result

    def test_skips_router_steps(self, tmp_path):
        workflow_yaml = tmp_path / "workflow.yaml"
        workflow_yaml.write_text(
            "name: Test\n"
            "steps:\n"
            "  - id: route\n"
            "    name: Route\n"
            "    agent_type: router\n"
            "    description: Pick path\n"
            "  - id: impl\n"
            "    name: Implement\n"
            "    agent_type: debug\n"
            "    description: Do work\n"
        )
        result = WorkflowDefinition.to_prompt_context(str(workflow_yaml))
        assert "router" not in result.lower().split("agent_type")[0]  # router step omitted
        assert "`debug`" in result

    def test_returns_empty_on_missing_file(self):
        result = WorkflowDefinition.to_prompt_context("/nonexistent/workflow.yaml")
        assert result == ""

    def test_returns_empty_on_no_steps(self, tmp_path):
        workflow_yaml = tmp_path / "workflow.yaml"
        workflow_yaml.write_text("name: Empty\nsteps: []\n")
        result = WorkflowDefinition.to_prompt_context(str(workflow_yaml))
        assert result == ""
