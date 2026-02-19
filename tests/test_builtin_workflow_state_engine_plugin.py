"""Tests for built-in workflow state engine adapter plugin."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nexus.plugins.builtin.workflow_state_engine_plugin import WorkflowStateEnginePlugin


class _FakeEngine:
    def __init__(self):
        self.pause_workflow = AsyncMock()
        self.resume_workflow = AsyncMock()
        self.approve_step = AsyncMock()
        self.deny_step = AsyncMock()
        self.get_workflow = AsyncMock()
        self.create_workflow = AsyncMock()
        self.start_workflow = AsyncMock()


@pytest.mark.asyncio
async def test_pause_workflow_resolves_issue_mapping():
    engine = _FakeEngine()
    plugin = WorkflowStateEnginePlugin(
        {
            "engine_factory": lambda: engine,
            "issue_to_workflow_id": lambda issue: "wf-123" if issue == "123" else None,
        }
    )

    ok = await plugin.pause_workflow("123", reason="test")

    assert ok is True
    engine.pause_workflow.assert_awaited_once_with("wf-123")


@pytest.mark.asyncio
async def test_resume_workflow_returns_false_without_mapping():
    engine = _FakeEngine()
    plugin = WorkflowStateEnginePlugin(
        {
            "engine_factory": lambda: engine,
            "issue_to_workflow_id": lambda _issue: None,
        }
    )

    ok = await plugin.resume_workflow("999")

    assert ok is False
    engine.resume_workflow.assert_not_called()


@pytest.mark.asyncio
async def test_get_workflow_status_formats_payload():
    engine = _FakeEngine()
    workflow = SimpleNamespace(
        id="wf-77",
        name="Demo Workflow",
        state=SimpleNamespace(value="running"),
        current_step=1,
        steps=[
            SimpleNamespace(name="triage", agent=SimpleNamespace(display_name="Triage")),
            SimpleNamespace(name="build", agent=SimpleNamespace(display_name="Builder")),
        ],
        created_at=None,
        updated_at=None,
        metadata={"k": "v"},
    )
    engine.get_workflow.return_value = workflow

    plugin = WorkflowStateEnginePlugin(
        {
            "engine_factory": lambda: engine,
            "issue_workflow_map": {"77": "wf-77"},
        }
    )

    status = await plugin.get_workflow_status("77")

    assert status is not None
    assert status["workflow_id"] == "wf-77"
    assert status["current_step"] == 2
    assert status["total_steps"] == 2
    assert status["current_step_name"] == "build"
    assert status["current_agent"] == "Builder"


@pytest.mark.asyncio
async def test_approve_and_deny_step_delegate_to_engine():
    engine = _FakeEngine()
    plugin = WorkflowStateEnginePlugin(
        {
            "engine_factory": lambda: engine,
            "issue_workflow_map": {"55": "wf-55"},
        }
    )

    approve_ok = await plugin.approve_step("55", approved_by="alice")
    deny_ok = await plugin.deny_step("55", denied_by="bob", reason="no")

    assert approve_ok is True
    assert deny_ok is True
    engine.approve_step.assert_awaited_once_with("wf-55", approved_by="alice")
    engine.deny_step.assert_awaited_once_with("wf-55", denied_by="bob", reason="no")


@pytest.mark.asyncio
async def test_create_workflow_for_issue_builds_workflow_and_maps_issue(tmp_path):
    engine = _FakeEngine()
    mapped = {"issue": None, "workflow_id": None}

    workflow_yaml = tmp_path / "workflow.yaml"
    workflow_yaml.write_text(
        "\n".join(
            [
                "name: Demo Flow",
                "full_workflow:",
                "  steps:",
                "    - name: triage",
                "      agent_type: triage",
            ]
        ),
        encoding="utf-8",
    )

    plugin = WorkflowStateEnginePlugin(
        {
            "engine_factory": lambda: engine,
            "workflow_definition_path_resolver": lambda _project: str(workflow_yaml),
            "issue_to_workflow_map_setter": lambda issue, workflow_id: mapped.update(
                {"issue": issue, "workflow_id": workflow_id}
            ),
            "github_repo": "org/repo",
        }
    )

    workflow_id = await plugin.create_workflow_for_issue(
        issue_number="12",
        issue_title="demo-task",
        project_name="nexus",
        tier_name="full",
        task_type="feature",
        description="desc",
    )

    assert workflow_id == "nexus-12-full"
    engine.create_workflow.assert_awaited_once()
    created_workflow = engine.create_workflow.await_args.args[0]
    assert created_workflow.id == "nexus-12-full"
    assert created_workflow.metadata["github_issue_url"].endswith("/issues/12")
    assert mapped == {"issue": "12", "workflow_id": "nexus-12-full"}


@pytest.mark.asyncio
async def test_create_workflow_for_issue_returns_none_when_definition_missing(tmp_path):
    engine = _FakeEngine()
    missing = str(Path(tmp_path) / "missing.yaml")
    plugin = WorkflowStateEnginePlugin(
        {
            "engine_factory": lambda: engine,
            "workflow_definition_path_resolver": lambda _project: missing,
        }
    )

    workflow_id = await plugin.create_workflow_for_issue(
        issue_number="12",
        issue_title="demo-task",
        project_name="nexus",
        tier_name="full",
        task_type="feature",
    )

    assert workflow_id is None
    engine.create_workflow.assert_not_called()


@pytest.mark.asyncio
async def test_start_workflow_delegates_to_engine():
    engine = _FakeEngine()
    plugin = WorkflowStateEnginePlugin({"engine_factory": lambda: engine})

    ok = await plugin.start_workflow("wf-99")

    assert ok is True
    engine.start_workflow.assert_awaited_once_with("wf-99")


@pytest.mark.asyncio
async def test_request_approval_gate_invokes_callbacks():
    captured = {
        "set": None,
        "audit": None,
        "notify": None,
    }

    def _set_pending_approval(**kwargs):
        captured["set"] = kwargs

    def _audit_log(issue_num, event, details):
        captured["audit"] = (issue_num, event, details)

    def _notify_approval_required(**kwargs):
        captured["notify"] = kwargs

    plugin = WorkflowStateEnginePlugin(
        {
            "set_pending_approval": _set_pending_approval,
            "audit_log": _audit_log,
            "notify_approval_required": _notify_approval_required,
        }
    )

    ok = await plugin.request_approval_gate(
        workflow_id="wf-12",
        issue_number="12",
        step_num=4,
        step_name="review",
        agent_name="Reviewer",
        approvers=["lead"],
        approval_timeout=3600,
        project="nexus",
    )

    assert ok is True
    assert captured["set"]["issue_num"] == "12"
    assert captured["set"]["step_num"] == 4
    assert captured["audit"][0] == 12
    assert captured["audit"][1] == "APPROVAL_REQUESTED"
    assert captured["notify"]["issue_number"] == "12"


@pytest.mark.asyncio
async def test_approve_and_deny_invoke_clear_and_audit_callbacks():
    engine = _FakeEngine()
    callback_log = {"clear": [], "audit": []}

    def _clear_pending_approval(issue_num):
        callback_log["clear"].append(issue_num)

    def _audit_log(issue_num, event, details):
        callback_log["audit"].append((issue_num, event, details))

    plugin = WorkflowStateEnginePlugin(
        {
            "engine_factory": lambda: engine,
            "issue_workflow_map": {"9": "wf-9"},
            "clear_pending_approval": _clear_pending_approval,
            "audit_log": _audit_log,
        }
    )

    approve_ok = await plugin.approve_step("9", approved_by="alice")
    deny_ok = await plugin.deny_step("9", denied_by="bob", reason="no")

    assert approve_ok is True
    assert deny_ok is True
    assert callback_log["clear"] == ["9", "9"]
    assert callback_log["audit"][0][1] == "APPROVAL_GRANTED"
    assert callback_log["audit"][1][1] == "APPROVAL_DENIED"
