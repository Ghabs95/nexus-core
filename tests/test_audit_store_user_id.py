import asyncio

from nexus.core.audit_store import AuditStore


def test_audit_log_forwards_user_id_to_storage(monkeypatch):
    captured: dict[str, object] = {}

    class _Store:
        async def log(self, *, workflow_id, event_type, data, user_id=None):  # noqa: ANN001
            captured["workflow_id"] = workflow_id
            captured["event_type"] = event_type
            captured["data"] = data
            captured["user_id"] = user_id

    class _WorkflowState:
        def get_workflow_id(self, issue_number):  # noqa: ANN001
            return f"wf-{issue_number}"

    monkeypatch.setattr(
        "nexus.core.integrations.workflow_state_factory.get_workflow_state",
        lambda: _WorkflowState(),
    )
    monkeypatch.setattr(
        AuditStore,
        "_get_core_store",
        classmethod(lambda _cls: _Store()),
    )
    monkeypatch.setattr(
        "nexus.core.audit_store._run_coro_sync",
        lambda coro_factory: asyncio.run(coro_factory()),
    )

    AuditStore.audit_log(
        42,
        "AGENT_LAUNCHED",
        "Launched gemini agent",
        user_id="nexus-user-42",
    )

    assert captured["workflow_id"] == "wf-42"
    assert captured["event_type"] == "AGENT_LAUNCHED"
    assert captured["user_id"] == "nexus-user-42"
