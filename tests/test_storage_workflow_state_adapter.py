from __future__ import annotations

import asyncio

import pytest
from nexus.adapters.storage.file import FileStorage
from nexus.adapters.storage.workflow_state_adapter import StorageWorkflowStateStore


def test_file_storage_workflow_state_roundtrip(tmp_path):
    storage = FileStorage(base_path=tmp_path)
    store = StorageWorkflowStateStore(storage)

    store.map_issue("42", "wf-42")
    assert store.get_workflow_id("42") == "wf-42"
    assert store.load_all_mappings() == {"42": "wf-42"}

    store.set_pending_approval("42", 3, "deploy", ["lead"], 3600)
    pending = store.get_pending_approval("42")
    assert pending is not None
    assert pending["step_num"] == 3
    assert pending["step_name"] == "deploy"
    assert pending["approvers"] == ["lead"]
    assert pending["approval_timeout"] == 3600

    store.clear_pending_approval("42")
    store.remove_mapping("42")
    assert store.get_pending_approval("42") is None
    assert store.get_workflow_id("42") is None


def test_store_supports_calls_when_event_loop_is_running(tmp_path):
    store = StorageWorkflowStateStore(FileStorage(base_path=tmp_path))

    async def _call_sync_methods():
        store.map_issue("7", "wf-7")
        return store.get_workflow_id("7")

    result = asyncio.run(_call_sync_methods())
    assert result == "wf-7"


try:
    import sqlalchemy  # noqa: F401

    _SA = True
except ImportError:
    _SA = False


@pytest.mark.skipif(not _SA, reason="sqlalchemy not installed")
def test_postgres_storage_workflow_state_roundtrip_sqlite():
    from nexus.adapters.storage.postgres import PostgreSQLStorageBackend

    storage = PostgreSQLStorageBackend(connection_string="sqlite:///:memory:")
    store = StorageWorkflowStateStore(storage)
    try:
        store.map_issue("100", "wf-100")
        assert store.get_workflow_id("100") == "wf-100"

        store.set_pending_approval("100", 1, "review", ["dev"], 120)
        pending = store.get_pending_approval("100")
        assert pending is not None
        assert pending["approvers"] == ["dev"]
    finally:
        storage.close()
