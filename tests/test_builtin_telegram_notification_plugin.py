"""Tests for built-in Telegram notification plugin."""

import json
from datetime import datetime, timezone

import pytest

from nexus.core.models import Agent, StepStatus, Workflow, WorkflowState, WorkflowStep
from nexus.core.visualizer import workflow_to_mermaid
from nexus.plugins.builtin.telegram_notification_plugin import TelegramNotificationPlugin


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_workflow(step_statuses=None) -> Workflow:
    """Return a minimal Workflow with two steps for testing."""
    agent = Agent(name="triage", display_name="Triage", description="")
    steps = []
    statuses = step_statuses or [StepStatus.COMPLETED, StepStatus.RUNNING]
    for i, status in enumerate(statuses, start=1):
        step = WorkflowStep(
            step_num=i,
            name=f"Step {i}",
            agent=agent,
            prompt_template="",
            status=status,
        )
        steps.append(step)
    return Workflow(
        id="wf-test-1",
        name="Test Workflow",
        version="1.0.0",
        steps=steps,
        state=WorkflowState.RUNNING,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Existing tests
# ---------------------------------------------------------------------------

def test_send_alert_sync_posts_to_telegram(monkeypatch):
    captured = {"url": "", "body": {}}

    def _fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _Response({"ok": True, "result": {"message_id": 123}})

    monkeypatch.setattr(
        "nexus.plugins.builtin.telegram_notification_plugin.request.urlopen",
        _fake_urlopen,
    )

    plugin = TelegramNotificationPlugin(
        {"bot_token": "token123", "chat_id": "999", "parse_mode": "Markdown"}
    )
    sent = plugin.send_alert_sync("System ready", severity="info")

    assert sent is True
    assert "bottoken123/sendMessage" in captured["url"]
    assert captured["body"]["chat_id"] == "999"
    assert "System ready" in captured["body"]["text"]


# ---------------------------------------------------------------------------
# workflow_to_mermaid unit tests
# ---------------------------------------------------------------------------

def test_workflow_to_mermaid_contains_step_names():
    wf = _make_workflow([StepStatus.COMPLETED, StepStatus.PENDING])
    diagram = workflow_to_mermaid(wf)
    assert "Step 1" in diagram
    assert "Step 2" in diagram


def test_workflow_to_mermaid_contains_status_labels():
    wf = _make_workflow([StepStatus.COMPLETED, StepStatus.RUNNING, StepStatus.FAILED])
    diagram = workflow_to_mermaid(wf)
    assert "COMPLETED" in diagram
    assert "RUNNING" in diagram
    assert "FAILED" in diagram


def test_workflow_to_mermaid_contains_classdefs():
    wf = _make_workflow()
    diagram = workflow_to_mermaid(wf)
    assert "classDef completed" in diagram
    assert "classDef running" in diagram
    assert "classDef pending" in diagram
    assert "classDef failed" in diagram
    assert "classDef skipped" in diagram


def test_workflow_to_mermaid_has_edges_between_steps():
    wf = _make_workflow([StepStatus.COMPLETED, StepStatus.RUNNING, StepStatus.PENDING])
    diagram = workflow_to_mermaid(wf)
    assert "step1 --> step2" in diagram
    assert "step2 --> step3" in diagram


def test_workflow_to_mermaid_uses_workflow_name_as_title():
    wf = _make_workflow()
    diagram = workflow_to_mermaid(wf)
    assert wf.name in diagram


def test_workflow_to_mermaid_custom_title():
    wf = _make_workflow()
    diagram = workflow_to_mermaid(wf, title="Custom Title")
    assert "Custom Title" in diagram


def test_workflow_to_mermaid_single_step_no_edges():
    wf = _make_workflow([StepStatus.RUNNING])
    diagram = workflow_to_mermaid(wf)
    assert "-->" not in diagram


# ---------------------------------------------------------------------------
# send_workflow_visualization integration test
# ---------------------------------------------------------------------------

def test_send_workflow_visualization_posts_mermaid(monkeypatch):
    captured = {"body": {}}

    def _fake_urlopen(req, timeout):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _Response({"ok": True, "result": {"message_id": 42}})

    monkeypatch.setattr(
        "nexus.plugins.builtin.telegram_notification_plugin.request.urlopen",
        _fake_urlopen,
    )

    plugin = TelegramNotificationPlugin(
        {"bot_token": "tkn", "chat_id": "7", "parse_mode": "Markdown"}
    )
    wf = _make_workflow([StepStatus.COMPLETED, StepStatus.RUNNING])
    result = plugin.send_workflow_visualization(wf)

    assert result is True
    text = captured["body"]["text"]
    assert "mermaid" in text
    assert "Test Workflow" in text
    assert "COMPLETED" in text
    assert "RUNNING" in text


def test_send_workflow_visualization_no_credentials():
    plugin = TelegramNotificationPlugin({})
    wf = _make_workflow()
    # Should return False gracefully when no credentials are configured.
    assert plugin.send_workflow_visualization(wf) is False

