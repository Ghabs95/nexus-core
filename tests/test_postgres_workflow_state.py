"""Unit tests for :class:`PostgresWorkflowStateStore`.

Uses an in-memory SQLite database (via ``sqlite:///:memory:``) to exercise
the SQLAlchemy ORM layer without requiring a real PostgreSQL server.
"""

from __future__ import annotations

from typing import Generator, Any

import pytest

try:
    import sqlalchemy  # noqa: F401

    _SA = True
except ImportError:
    _SA = False

pytestmark = pytest.mark.skipif(not _SA, reason="sqlalchemy not installed")


from nexus.adapters.storage.postgres_workflow_state import PostgresWorkflowStateStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store() -> Generator[PostgresWorkflowStateStore, Any, None]:
    """Create a store backed by in-memory SQLite (same SQLAlchemy ORM)."""
    instance = PostgresWorkflowStateStore(
        connection_string="sqlite:///:memory:",
        echo=False,
    )
    try:
        yield instance
    finally:
        instance.close()


# ---------------------------------------------------------------------------
# Workflow mapping
# ---------------------------------------------------------------------------


class TestMapIssue:
    def test_map_and_get(self, store: PostgresWorkflowStateStore) -> None:
        store.map_issue("10", "nexus-10-full")
        assert store.get_workflow_id("10") == "nexus-10-full"

    def test_overwrite_existing(self, store: PostgresWorkflowStateStore) -> None:
        store.map_issue("10", "nexus-10-full")
        store.map_issue("10", "nexus-10-fast")
        assert store.get_workflow_id("10") == "nexus-10-fast"

    def test_multiple_issues(self, store: PostgresWorkflowStateStore) -> None:
        store.map_issue("1", "wf-1")
        store.map_issue("2", "wf-2")
        assert store.get_workflow_id("1") == "wf-1"
        assert store.get_workflow_id("2") == "wf-2"


class TestGetWorkflowId:
    def test_returns_none_when_missing(self, store: PostgresWorkflowStateStore) -> None:
        assert store.get_workflow_id("999") is None


class TestRemoveMapping:
    def test_removes_existing(self, store: PostgresWorkflowStateStore) -> None:
        store.map_issue("10", "wf-10")
        store.remove_mapping("10")
        assert store.get_workflow_id("10") is None

    def test_noop_when_not_present(self, store: PostgresWorkflowStateStore) -> None:
        store.remove_mapping("non-existent")  # Should not raise

    def test_other_mappings_preserved(self, store: PostgresWorkflowStateStore) -> None:
        store.map_issue("1", "wf-1")
        store.map_issue("2", "wf-2")
        store.remove_mapping("1")
        assert store.get_workflow_id("1") is None
        assert store.get_workflow_id("2") == "wf-2"


class TestLoadAllMappings:
    def test_empty(self, store: PostgresWorkflowStateStore) -> None:
        assert store.load_all_mappings() == {}

    def test_returns_all(self, store: PostgresWorkflowStateStore) -> None:
        store.map_issue("1", "wf-1")
        store.map_issue("2", "wf-2")
        result = store.load_all_mappings()
        assert result == {"1": "wf-1", "2": "wf-2"}


# ---------------------------------------------------------------------------
# Approval gate
# ---------------------------------------------------------------------------


class TestSetPendingApproval:
    def test_set_and_get(self, store: PostgresWorkflowStateStore) -> None:
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
        assert isinstance(pending["requested_at"], float)

    def test_overwrite(self, store: PostgresWorkflowStateStore) -> None:
        store.set_pending_approval("42", 1, "review", ["dev"], 600)
        store.set_pending_approval("42", 2, "deploy", ["ops"], 1200)
        pending = store.get_pending_approval("42")
        assert pending is not None
        assert pending["step_num"] == 2
        assert pending["step_name"] == "deploy"

    def test_approvers_roundtrip_as_list(self, store: PostgresWorkflowStateStore) -> None:
        """Approvers are stored as JSON but returned as a list."""
        store.set_pending_approval("1", 1, "s1", ["a", "b", "c"], 60)
        pending = store.get_pending_approval("1")
        assert pending is not None
        assert pending["approvers"] == ["a", "b", "c"]
        assert isinstance(pending["approvers"], list)


class TestGetPendingApproval:
    def test_returns_none_when_absent(self, store: PostgresWorkflowStateStore) -> None:
        assert store.get_pending_approval("999") is None


class TestClearPendingApproval:
    def test_clears(self, store: PostgresWorkflowStateStore) -> None:
        store.set_pending_approval("55", 1, "review", [], 86400)
        store.clear_pending_approval("55")
        assert store.get_pending_approval("55") is None

    def test_noop_when_not_present(self, store: PostgresWorkflowStateStore) -> None:
        store.clear_pending_approval("non-existent")

    def test_other_approvals_preserved(self, store: PostgresWorkflowStateStore) -> None:
        store.set_pending_approval("1", 1, "s1", [], 60)
        store.set_pending_approval("2", 2, "s2", [], 120)
        store.clear_pending_approval("1")
        assert store.get_pending_approval("1") is None
        assert store.get_pending_approval("2") is not None


class TestLoadAllApprovals:
    def test_empty(self, store: PostgresWorkflowStateStore) -> None:
        assert store.load_all_approvals() == {}

    def test_returns_all(self, store: PostgresWorkflowStateStore) -> None:
        store.set_pending_approval("1", 1, "s1", ["a"], 60)
        store.set_pending_approval("2", 2, "s2", ["b"], 120)
        result = store.load_all_approvals()
        assert "1" in result
        assert "2" in result
        assert result["1"]["step_name"] == "s1"
        assert result["2"]["approvers"] == ["b"]
