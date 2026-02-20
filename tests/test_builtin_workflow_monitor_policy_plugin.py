"""Tests for workflow monitor policy plugin."""

from datetime import datetime, timezone
from types import SimpleNamespace

from nexus.adapters.git.base import Issue
from nexus.plugins.builtin.workflow_monitor_policy_plugin import WorkflowMonitorPolicyPlugin


def test_workflow_monitor_policy_list_and_comments_and_pr():
    def _list_issues(**_kwargs):
        return [
            {"number": 10, "labels": [{"name": "workflow:full"}]},
            {"number": 11, "labels": [{"name": "type:bug"}]},
        ]

    def _get_comments(**_kwargs):
        return [
            SimpleNamespace(id=1, author="Ghabs95", body="one"),
            SimpleNamespace(id=2, author="other", body="two"),
        ]

    def _search_linked_prs(**_kwargs):
        return [
            SimpleNamespace(number=8, state="closed", url="https://example/8"),
            SimpleNamespace(number=9, state="open", url="https://example/9"),
        ]

    plugin = WorkflowMonitorPolicyPlugin(
        {
            "list_issues": _list_issues,
            "get_comments": _get_comments,
            "search_linked_prs": _search_linked_prs,
        }
    )

    issue_numbers = plugin.list_workflow_issue_numbers(
        repo="org/repo",
        workflow_labels={"workflow:full", "workflow:shortened", "workflow:fast-track"},
    )
    comments = plugin.get_bot_comments(repo="org/repo", issue_number="42", bot_author="Ghabs95")
    pr = plugin.find_open_linked_pr(repo="org/repo", issue_number="42")

    assert issue_numbers == ["10"]
    assert [comment.id for comment in comments] == [1]
    assert pr is not None
    assert pr.number == 9


def test_workflow_monitor_policy_resolve_repo_for_issue():
    def _get_issue(**_kwargs):
        return SimpleNamespace(body="**Task File:** `/tmp/ws-a/.nexus/tasks/active/task.md`")

    plugin = WorkflowMonitorPolicyPlugin({"get_issue": _get_issue})

    repo = plugin.resolve_repo_for_issue(
        issue_number="42",
        default_repo="org/default",
        project_workspaces={"proj-a": "/tmp/ws-a", "proj-b": "/tmp/ws-b"},
        project_repos={"proj-a": "org/repo-a", "proj-b": "org/repo-b"},
    )

    assert repo == "org/repo-a"


def test_workflow_policy_list_issue_objects_with_string_labels():
    issues = [
        Issue(
            id="1",
            number=42,
            title="Triage",
            body="",
            state="open",
            labels=["workflow:shortened", "bug"],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            url="https://example/42",
        ),
        Issue(
            id="2",
            number=43,
            title="Other",
            body="",
            state="open",
            labels=["bug"],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            url="https://example/43",
        ),
    ]

    plugin = WorkflowMonitorPolicyPlugin(
        {
            "list_open_issues": lambda **_kwargs: issues,
        }
    )

    issue_numbers = plugin.list_workflow_issue_numbers(
        repo="group/project",
        workflow_labels={"workflow:full", "workflow:shortened", "workflow:fast-track"},
    )

    assert issue_numbers == ["42"]
