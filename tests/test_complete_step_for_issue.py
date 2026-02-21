"""Tests for WorkflowStateEnginePlugin.complete_step_for_issue and Workflow.active_agent_type."""

import pytest
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from nexus.core.models import (
    Agent,
    AuditEvent,
    StepStatus,
    Workflow,
    WorkflowState,
    WorkflowStep,
)
from nexus.core.workflow import WorkflowEngine
from nexus.adapters.storage.base import StorageBackend
from nexus.plugins.builtin.workflow_state_engine_plugin import WorkflowStateEnginePlugin


# ---------------------------------------------------------------------------
# Helpers (reuse pattern from test_conditional_steps.py)
# ---------------------------------------------------------------------------


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

    async def save_agent_metadata(self, workflow_id: str, agent_name: str, metadata: Dict[str, Any]) -> None:
        pass

    async def get_agent_metadata(self, workflow_id: str, agent_name: str) -> Optional[Dict[str, Any]]:
        return None

    async def cleanup_old_workflows(self, older_than_days: int = 30) -> int:
        return 0


def _agent(agent_type: str) -> Agent:
    return Agent(name=agent_type, display_name=agent_type.title(), description="test", timeout=60, max_retries=0)


def _step(num: int, step_id: str, agent_type: str, routes=None) -> WorkflowStep:
    return WorkflowStep(
        step_num=num,
        name=step_id,
        agent=_agent(agent_type),
        prompt_template="do work",
        routes=routes or [],
    )


def _make_workflow(workflow_id: str, steps: List[WorkflowStep]) -> Workflow:
    wf = Workflow(
        id=workflow_id,
        name="test",
        version="1.0",
        steps=steps,
        state=WorkflowState.RUNNING,
        current_step=1,
    )
    steps[0].status = StepStatus.RUNNING
    steps[0].started_at = datetime.now(timezone.utc)
    return wf


async def _plugin_with_workflow(workflow: Workflow, issue_number: str) -> tuple:
    storage = InMemoryStorage()
    await storage.save_workflow(workflow)
    engine = WorkflowEngine(storage=storage)
    issue_map = {issue_number: workflow.id}
    plugin = WorkflowStateEnginePlugin({
        "engine_factory": lambda: engine,
        "issue_to_workflow_id": lambda n: issue_map.get(str(n)),
    })
    return plugin, storage


# ---------------------------------------------------------------------------
# Workflow.active_agent_type
# ---------------------------------------------------------------------------


class TestActiveAgentType:
    def test_returns_agent_type_when_step_running(self):
        step = _step(1, "develop", "developer")
        step.status = StepStatus.RUNNING
        wf = Workflow(id="w", name="t", version="1", steps=[step], state=WorkflowState.RUNNING, current_step=1)
        assert wf.active_agent_type == "developer"

    def test_returns_none_when_step_completed(self):
        step = _step(1, "develop", "developer")
        step.status = StepStatus.COMPLETED
        wf = Workflow(id="w", name="t", version="1", steps=[step], state=WorkflowState.RUNNING, current_step=1)
        assert wf.active_agent_type is None

    def test_returns_none_when_no_current_step(self):
        wf = Workflow(id="w", name="t", version="1", steps=[], state=WorkflowState.RUNNING, current_step=0)
        assert wf.active_agent_type is None

    def test_returns_none_workflow_completed(self):
        step = _step(1, "develop", "developer")
        step.status = StepStatus.COMPLETED
        wf = Workflow(id="w", name="t", version="1", steps=[step], state=WorkflowState.COMPLETED, current_step=1)
        assert wf.active_agent_type is None


# ---------------------------------------------------------------------------
# complete_step_for_issue: basic routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_step_for_issue_advances_to_next():
    """Completing the first step should activate the second step."""
    develop = _step(1, "develop", "developer")
    review = _step(2, "review", "reviewer")
    wf = _make_workflow("wf-42", [develop, review])
    plugin, _ = await _plugin_with_workflow(wf, "42")

    updated = await plugin.complete_step_for_issue(
        issue_number="42",
        completed_agent_type="developer",
        outputs={"pr": "https://github.com/org/repo/pull/1"},
    )

    assert updated is not None
    assert updated.active_agent_type == "reviewer"
    assert updated.state == WorkflowState.RUNNING


@pytest.mark.asyncio
async def test_complete_step_for_issue_completes_workflow_on_last_step():
    """Completing the last step with no successor should mark workflow COMPLETED."""
    summarizer = _step(1, "close_loop", "summarizer")
    wf = _make_workflow("wf-99", [summarizer])
    plugin, _ = await _plugin_with_workflow(wf, "99")

    updated = await plugin.complete_step_for_issue(
        issue_number="99",
        completed_agent_type="summarizer",
        outputs={"summary": "done"},
    )

    assert updated is not None
    assert updated.state == WorkflowState.COMPLETED
    assert updated.active_agent_type is None


@pytest.mark.asyncio
async def test_complete_step_for_issue_returns_none_when_no_mapping():
    """Returns None when no workflow is mapped to the issue."""
    plugin = WorkflowStateEnginePlugin({
        "issue_to_workflow_id": lambda _: None,
    })
    result = await plugin.complete_step_for_issue("999", "developer", {})
    assert result is None


@pytest.mark.asyncio
async def test_complete_step_for_issue_noop_when_agent_type_mismatch():
    """When agent_type doesn't match RUNNING step, plugin raises mismatch error."""
    develop = _step(1, "develop", "developer")
    review = _step(2, "review", "reviewer")
    wf = _make_workflow("wf-fallback", [develop, review])
    plugin, _ = await _plugin_with_workflow(wf, "fallback")

    # Pass wrong agent_type that doesn't match RUNNING "developer"
    with pytest.raises(ValueError, match="Completion agent mismatch"):
        await plugin.complete_step_for_issue(
            issue_number="fallback",
            completed_agent_type="unknown-agent",
            outputs={"done": True},
        )


@pytest.mark.asyncio
async def test_complete_step_for_issue_autostarts_pending_workflow():
    """Pending workflows auto-start so first completion can advance chain."""
    triage = _step(1, "triage", "triage")
    debug = _step(2, "debug", "debug")
    wf = Workflow(
        id="wf-pending",
        name="test",
        version="1.0",
        steps=[triage, debug],
        state=WorkflowState.PENDING,
        current_step=0,
    )
    plugin, _ = await _plugin_with_workflow(wf, "pending")

    updated = await plugin.complete_step_for_issue(
        issue_number="pending",
        completed_agent_type="triage",
        outputs={"priority": "p2"},
    )

    assert updated is not None
    assert updated.state == WorkflowState.RUNNING
    assert updated.active_agent_type == "debug"


# ---------------------------------------------------------------------------
# complete_step_for_issue: router step (review/develop loop)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_step_for_issue_routes_through_router_to_close():
    """Router evaluates route condition and routes approved review to close_loop."""
    develop = _step(1, "develop", "developer")
    review = _step(2, "review", "reviewer")
    router = _step(3, "route_review", "router", routes=[
        {"when": "approval_status == 'approved'", "then": "close_loop"},
        {"default": "develop"},
    ])
    close_loop = _step(4, "close_loop", "summarizer")

    wf = _make_workflow("wf-router", [develop, review, router, close_loop])
    plugin, _ = await _plugin_with_workflow(wf, "router")

    # develop completes
    await plugin.complete_step_for_issue("router", "developer", {"pr": "1"})
    assert wf.active_agent_type == "reviewer"

    # review completes — approved
    updated = await plugin.complete_step_for_issue(
        "router", "reviewer", {"approval_status": "approved", "review_comments": []}
    )

    assert updated is not None
    assert updated.active_agent_type == "summarizer", (
        f"expected summarizer, got {updated.active_agent_type} (state={updated.state})"
    )


@pytest.mark.asyncio
async def test_complete_step_for_issue_routes_loop_back_to_develop():
    """Router evaluates default and loops reviewer → developer on changes_requested."""
    develop = _step(1, "develop", "developer")
    review = _step(2, "review", "reviewer")
    router = _step(3, "route_review", "router", routes=[
        {"when": "approval_status == 'approved'", "then": "close_loop"},
        {"default": "develop"},
    ])
    close_loop = _step(4, "close_loop", "summarizer")

    wf = _make_workflow("wf-loop", [develop, review, router, close_loop])
    plugin, _ = await _plugin_with_workflow(wf, "loop")

    # develop completes
    await plugin.complete_step_for_issue("loop", "developer", {"pr": "1"})

    # review completes — changes requested → loops back
    updated = await plugin.complete_step_for_issue(
        "loop", "reviewer", {"approval_status": "changes_requested"}
    )

    assert updated is not None
    assert updated.active_agent_type == "developer"
    assert develop.iteration == 1


# ---------------------------------------------------------------------------
# Idempotency ledger tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_complete_step_for_issue_idempotency_duplicate_suppressed(tmp_path):
    """Calling complete_step_for_issue with a duplicate event_id must be a no-op.

    Simulates a re-delivered signal: after the first call advances the workflow,
    we reset step 1 back to RUNNING and replay the same event_id.  The ledger
    must suppress the second advancement.
    """
    step1 = _step(1, "triage", "triage")
    step2 = _step(2, "dev", "developer")
    wf = _make_workflow("wf-idem", [step1, step2])
    plugin, _ = await _plugin_with_workflow(wf, "idem")
    ledger_path = str(tmp_path / "ledger.json")
    plugin.config["idempotency_ledger_path"] = ledger_path

    # First call advances the workflow (step1 → COMPLETED, step2 → RUNNING).
    updated = await plugin.complete_step_for_issue("idem", "triage", {}, event_id="ev-001")
    assert updated is not None
    assert updated.active_agent_type == "developer"

    # Simulate re-delivery: reset step1 back to RUNNING.
    step1.status = StepStatus.RUNNING
    step2.status = StepStatus.PENDING

    # Second call with the same composite key must be suppressed by the ledger.
    updated2 = await plugin.complete_step_for_issue("idem", "triage", {}, event_id="ev-001")
    # step1 must still be RUNNING (the ledger blocked the advancement).
    assert step1.status == StepStatus.RUNNING


@pytest.mark.asyncio
async def test_complete_step_for_issue_different_event_ids_advance_independently(tmp_path):
    """Two distinct event_ids for different steps should both advance normally."""
    step1 = _step(1, "triage", "triage")
    step2 = _step(2, "dev", "developer")
    wf = _make_workflow("wf-idem2", [step1, step2])
    plugin, _ = await _plugin_with_workflow(wf, "idem2")
    ledger_path = str(tmp_path / "ledger2.json")
    plugin.config["idempotency_ledger_path"] = ledger_path

    await plugin.complete_step_for_issue("idem2", "triage", {}, event_id="ev-aaa")
    updated = await plugin.complete_step_for_issue("idem2", "developer", {}, event_id="ev-bbb")
    assert updated is not None
    from nexus.core.models import WorkflowState as WS
    assert updated.state == WS.COMPLETED


@pytest.mark.asyncio
async def test_complete_step_for_issue_no_event_id_always_advances(tmp_path):
    """Without an event_id the ledger is skipped and calls advance as normal."""
    step1 = _step(1, "triage", "triage")
    step2 = _step(2, "dev", "developer")
    wf = _make_workflow("wf-idem3", [step1, step2])
    plugin, _ = await _plugin_with_workflow(wf, "idem3")
    ledger_path = str(tmp_path / "ledger3.json")
    plugin.config["idempotency_ledger_path"] = ledger_path

    await plugin.complete_step_for_issue("idem3", "triage", {})
    updated = await plugin.complete_step_for_issue("idem3", "developer", {})
    assert updated is not None
    from nexus.core.models import WorkflowState as WS
    assert updated.state == WS.COMPLETED
