"""Unit tests for :class:`FileWorkflowStateStore`.

Tests every method from the :class:`WorkflowStateStore` protocol,
plus edge-cases: missing files, corrupt JSON, concurrent re-reads.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nexus.adapters.storage.file_workflow_state import FileWorkflowStateStore
from nexus.core.workflow_state import WorkflowStateStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> FileWorkflowStateStore:
    return FileWorkflowStateStore(base_path=tmp_path)


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_is_instance_of_protocol(self, store: FileWorkflowStateStore) -> None:
        assert isinstance(store, WorkflowStateStore)


# ---------------------------------------------------------------------------
# Workflow mapping
# ---------------------------------------------------------------------------


class TestMapIssue:
    def test_map_and_get(self, store: FileWorkflowStateStore) -> None:
        store.map_issue("10", "nexus-10-full")
        assert store.get_workflow_id("10") == "nexus-10-full"

    def test_overwrite_existing_mapping(self, store: FileWorkflowStateStore) -> None:
        store.map_issue("10", "nexus-10-full")
        store.map_issue("10", "nexus-10-fast")
        assert store.get_workflow_id("10") == "nexus-10-fast"

    def test_multiple_issues(self, store: FileWorkflowStateStore) -> None:
        store.map_issue("1", "wf-1")
        store.map_issue("2", "wf-2")
        assert store.get_workflow_id("1") == "wf-1"
        assert store.get_workflow_id("2") == "wf-2"


class TestGetWorkflowId:
    def test_returns_none_when_missing(self, store: FileWorkflowStateStore) -> None:
        assert store.get_workflow_id("999") is None

    def test_returns_none_when_no_file(self, store: FileWorkflowStateStore) -> None:
        # No mapping file exists at all
        assert store.get_workflow_id("1") is None


class TestRemoveMapping:
    def test_removes_existing(self, store: FileWorkflowStateStore) -> None:
        store.map_issue("10", "wf-10")
        store.remove_mapping("10")
        assert store.get_workflow_id("10") is None

    def test_noop_when_not_present(self, store: FileWorkflowStateStore) -> None:
        # Should not raise
        store.remove_mapping("non-existent")

    def test_other_mappings_preserved(self, store: FileWorkflowStateStore) -> None:
        store.map_issue("1", "wf-1")
        store.map_issue("2", "wf-2")
        store.remove_mapping("1")
        assert store.get_workflow_id("1") is None
        assert store.get_workflow_id("2") == "wf-2"


class TestLoadAllMappings:
    def test_empty_when_no_file(self, store: FileWorkflowStateStore) -> None:
        assert store.load_all_mappings() == {}

    def test_returns_all(self, store: FileWorkflowStateStore) -> None:
        store.map_issue("1", "wf-1")
        store.map_issue("2", "wf-2")
        result = store.load_all_mappings()
        assert result == {"1": "wf-1", "2": "wf-2"}


# ---------------------------------------------------------------------------
# Approval gate
# ---------------------------------------------------------------------------


class TestSetPendingApproval:
    def test_set_and_get(self, store: FileWorkflowStateStore) -> None:
        store.set_pending_approval(
            issue_num="42",
            step_num=3,
            step_name="deploy",
            approvers=["tech-lead"],
            approval_timeout=3600,
        )
        pending = store.get_pending_approval("42")
        assert pending is not None
        assert pending["step_num"] == 3
        assert pending["step_name"] == "deploy"
        assert pending["approvers"] == ["tech-lead"]
        assert pending["approval_timeout"] == 3600
        assert "requested_at" in pending
        assert isinstance(pending["requested_at"], float)

    def test_overwrite(self, store: FileWorkflowStateStore) -> None:
        store.set_pending_approval("42", 1, "review", ["dev"], 600)
        store.set_pending_approval("42", 2, "deploy", ["ops"], 1200)
        pending = store.get_pending_approval("42")
        assert pending is not None
        assert pending["step_num"] == 2
        assert pending["step_name"] == "deploy"

    def test_multiple_issues(self, store: FileWorkflowStateStore) -> None:
        store.set_pending_approval("1", 1, "s1", ["a"], 60)
        store.set_pending_approval("2", 2, "s2", ["b"], 120)
        assert store.get_pending_approval("1") is not None
        assert store.get_pending_approval("2") is not None


class TestGetPendingApproval:
    def test_returns_none_when_absent(self, store: FileWorkflowStateStore) -> None:
        assert store.get_pending_approval("999") is None

    def test_returns_none_when_no_file(self, store: FileWorkflowStateStore) -> None:
        assert store.get_pending_approval("1") is None


class TestClearPendingApproval:
    def test_clears(self, store: FileWorkflowStateStore) -> None:
        store.set_pending_approval("55", 1, "review", [], 86400)
        store.clear_pending_approval("55")
        assert store.get_pending_approval("55") is None

    def test_noop_when_not_present(self, store: FileWorkflowStateStore) -> None:
        store.clear_pending_approval("non-existent")

    def test_other_approvals_preserved(self, store: FileWorkflowStateStore) -> None:
        store.set_pending_approval("1", 1, "s1", [], 60)
        store.set_pending_approval("2", 2, "s2", [], 120)
        store.clear_pending_approval("1")
        assert store.get_pending_approval("1") is None
        assert store.get_pending_approval("2") is not None


class TestLoadAllApprovals:
    def test_empty(self, store: FileWorkflowStateStore) -> None:
        assert store.load_all_approvals() == {}

    def test_returns_all(self, store: FileWorkflowStateStore) -> None:
        store.set_pending_approval("1", 1, "s1", ["a"], 60)
        store.set_pending_approval("2", 2, "s2", ["b"], 120)
        result = store.load_all_approvals()
        assert "1" in result
        assert "2" in result
        assert result["1"]["step_name"] == "s1"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_corrupt_mapping_file(self, store: FileWorkflowStateStore, tmp_path: Path) -> None:
        mapping_file = tmp_path / "workflow_mapping.json"
        mapping_file.write_text("{invalid json!!")
        # Should fall back to default
        assert store.get_workflow_id("1") is None
        assert store.load_all_mappings() == {}

    def test_corrupt_approval_file(self, store: FileWorkflowStateStore, tmp_path: Path) -> None:
        approval_file = tmp_path / "approval_state.json"
        approval_file.write_text("not json")
        assert store.get_pending_approval("1") is None
        assert store.load_all_approvals() == {}

    def test_empty_json_file(self, store: FileWorkflowStateStore, tmp_path: Path) -> None:
        mapping_file = tmp_path / "workflow_mapping.json"
        mapping_file.write_text("")
        # Empty string → json.loads fails → defaults
        assert store.load_all_mappings() == {}

    def test_null_json_file(self, store: FileWorkflowStateStore, tmp_path: Path) -> None:
        mapping_file = tmp_path / "workflow_mapping.json"
        mapping_file.write_text("null")
        # null parses as None → falsy → default
        assert store.load_all_mappings() == {}

    def test_subdirectory_auto_created(self, tmp_path: Path) -> None:
        nested = tmp_path / "deep" / "nested" / "path"
        s = FileWorkflowStateStore(base_path=nested)
        s.map_issue("1", "wf-1")
        assert s.get_workflow_id("1") == "wf-1"
        assert nested.exists()

    def test_atomic_write_leaves_no_tmp(
        self, store: FileWorkflowStateStore, tmp_path: Path
    ) -> None:
        store.map_issue("1", "wf-1")
        # No .tmp file should be left behind
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []

    def test_file_persists_across_instances(self, tmp_path: Path) -> None:
        s1 = FileWorkflowStateStore(base_path=tmp_path)
        s1.map_issue("10", "wf-10")
        s1.set_pending_approval("10", 1, "review", ["dev"], 300)

        s2 = FileWorkflowStateStore(base_path=tmp_path)
        assert s2.get_workflow_id("10") == "wf-10"
        assert s2.get_pending_approval("10") is not None
