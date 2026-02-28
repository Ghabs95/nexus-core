"""Regression tests for WorkflowWatchService event routing semantics."""

from __future__ import annotations

from state_manager import HostStateManager
from services import workflow_watch_service as watch_module
from services.workflow_watch_service import WorkflowWatchService


def _build_service(monkeypatch) -> WorkflowWatchService:
    monkeypatch.setenv("NEXUS_TELEGRAM_WATCH_ENABLED", "false")
    monkeypatch.setattr(HostStateManager, "load_workflow_watch_subscriptions", lambda: {})
    monkeypatch.setattr(HostStateManager, "save_workflow_watch_subscriptions", lambda _data: None)
    return WorkflowWatchService()


def test_watch_routes_events_by_project_workflow_scope(monkeypatch):
    service = _build_service(monkeypatch)
    sent_messages: list[tuple[int, str]] = []
    service._send_message = lambda chat_id, text: sent_messages.append((chat_id, text))

    service.start_watch(chat_id=101, user_id=1, project_key="nexus", issue_num="106")
    service.start_watch(chat_id=202, user_id=2, project_key="sample_app", issue_num="106")

    service._handle_event(
        "step_status_changed",
        {
            "issue": "106",
            "workflow_id": "nexus-106-full",
            "step_id": "developer",
            "agent_type": "developer",
            "status": "running",
        },
    )

    assert sent_messages == [(101, "▶️ #106 developer · developer → running")]
    assert service._subscriptions["101:1"].workflow_id == "nexus-106-full"
    assert service._subscriptions["202:2"].workflow_id == ""


def test_workflow_completed_only_unsubscribes_matching_project_issue(monkeypatch):
    service = _build_service(monkeypatch)
    sent_messages: list[tuple[int, str]] = []
    service._send_message = lambda chat_id, text: sent_messages.append((chat_id, text))

    service.start_watch(chat_id=101, user_id=1, project_key="nexus", issue_num="106")
    service.start_watch(chat_id=202, user_id=2, project_key="sample_app", issue_num="106")

    service._handle_event(
        "workflow_completed",
        {
            "issue": "106",
            "workflow_id": "nexus-106-full",
            "status": "success",
            "summary": "done",
        },
    )

    assert "101:1" not in service._subscriptions
    assert "202:2" in service._subscriptions
    assert sent_messages == [(101, "✅ Workflow #106 completed: success — done")]


def test_mermaid_dedup_uses_content_hash_not_length(monkeypatch):
    service = _build_service(monkeypatch)
    sent_messages: list[tuple[int, str]] = []
    service._send_message = lambda chat_id, text: sent_messages.append((chat_id, text))
    monkeypatch.setattr(watch_module, "_DEFAULT_THROTTLE_SECONDS", 0.0)

    service.start_watch(
        chat_id=101,
        user_id=1,
        project_key="nexus",
        issue_num="106",
        mermaid_enabled=True,
    )

    service._handle_event(
        "mermaid_diagram",
        {
            "issue": "106",
            "workflow_id": "nexus-106-full",
            "diagram": "abc",
        },
    )
    service._handle_event(
        "mermaid_diagram",
        {
            "issue": "106",
            "workflow_id": "nexus-106-full",
            "diagram": "xyz",
        },
    )

    assert len(sent_messages) == 2
    assert sent_messages[0] == (101, "🧭 Workflow #106 diagram updated.")
    assert sent_messages[1] == (101, "🧭 Workflow #106 diagram updated.")
