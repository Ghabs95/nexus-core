"""Tests for built-in GitLab API issue plugin."""

from unittest.mock import MagicMock

from nexus.plugins.builtin.gitlab_issue_plugin import GitLabIssuePlugin


def test_builtin_gitlab_api_plugin_retries_without_labels(monkeypatch):
    plugin = GitLabIssuePlugin({"repo": "group/project"})
    calls: list[tuple[str, str, dict | None]] = []

    def _sync_request(method, endpoint, payload=None):  # noqa: ANN001
        calls.append((method, endpoint, payload))
        if payload and payload.get("labels"):
            raise RuntimeError("label create failed")
        return {"web_url": "https://gitlab.com/group/project/-/issues/42"}

    platform = MagicMock()
    platform._sync_request.side_effect = _sync_request
    monkeypatch.setattr(plugin, "_platform", lambda: platform)

    url = plugin.create_issue("Test", "Body", labels=["project:nexus", "type:feature"])

    assert url == "https://gitlab.com/group/project/-/issues/42"
    assert calls == [
        (
            "POST",
            "projects/group%2Fproject/issues",
            {"title": "Test", "description": "Body", "labels": "project:nexus,type:feature"},
        ),
        (
            "POST",
            "projects/group%2Fproject/issues",
            {"title": "Test", "description": "Body"},
        ),
    ]


def test_builtin_gitlab_api_plugin_get_issue_maps_fields(monkeypatch):
    plugin = GitLabIssuePlugin({"repo": "group/project"})
    calls: list[tuple[str, str]] = []

    issue_payload = {
        "iid": 113,
        "title": "Issue 113",
        "description": "Body",
        "state": "opened",
        "web_url": "https://gitlab.com/group/project/-/issues/113",
        "created_at": "2026-03-08T15:00:00Z",
        "updated_at": "2026-03-08T15:01:00Z",
        "labels": ["workflow:shortened"],
    }
    comments_payload = [
        {
            "id": 77,
            "body": "Ready for review",
            "created_at": "2026-03-08T15:40:54Z",
            "updated_at": "2026-03-08T15:41:00Z",
        }
    ]

    def _sync_request(method, endpoint, payload=None):  # noqa: ANN001
        assert payload is None
        calls.append((method, endpoint))
        if endpoint.endswith("/notes"):
            return comments_payload
        return issue_payload

    platform = MagicMock()
    platform._sync_request.side_effect = _sync_request
    monkeypatch.setattr(plugin, "_platform", lambda: platform)

    issue = plugin.get_issue("113", ["number", "title", "state", "comments", "labels"])

    assert issue == {
        "number": 113,
        "title": "Issue 113",
        "state": "open",
        "comments": [
            {
                "id": 77,
                "body": "Ready for review",
                "createdAt": "2026-03-08T15:40:54Z",
                "updatedAt": "2026-03-08T15:41:00Z",
            }
        ],
        "labels": ["workflow:shortened"],
    }
    assert calls == [
        ("GET", "projects/group%2Fproject/issues/113"),
        ("GET", "projects/group%2Fproject/issues/113/notes"),
    ]


def test_builtin_gitlab_api_plugin_add_assignee_resolves_username(monkeypatch):
    plugin = GitLabIssuePlugin({"repo": "group/project"})
    calls: list[tuple[str, str, dict | None]] = []

    def _sync_request(method, endpoint, payload=None):  # noqa: ANN001
        calls.append((method, endpoint, payload))
        if endpoint.startswith("users?username="):
            return [{"id": 999, "username": "reviewer"}]
        if method == "GET":
            return {"assignees": [{"id": 100}]}
        return {}

    platform = MagicMock()
    platform._sync_request.side_effect = _sync_request
    monkeypatch.setattr(plugin, "_platform", lambda: platform)

    success = plugin.add_assignee("77", "reviewer")

    assert success is True
    assert calls == [
        ("GET", "users?username=reviewer", None),
        ("GET", "projects/group%2Fproject/issues/77", None),
        ("PUT", "projects/group%2Fproject/issues/77", {"assignee_ids": [100, 999]}),
    ]


def test_builtin_gitlab_api_plugin_lists_issues_state_mapping(monkeypatch):
    plugin = GitLabIssuePlugin({"repo": "group/project"})
    payload = [
        {
            "iid": 5,
            "title": "Closed issue",
            "state": "closed",
            "description": "Done",
            "web_url": "https://gitlab.com/group/project/-/issues/5",
            "labels": [],
        }
    ]

    platform = MagicMock()
    platform._sync_request.return_value = payload
    monkeypatch.setattr(plugin, "_platform", lambda: platform)

    issues = plugin.list_issues(state="closed", limit=3)

    assert issues == [{"number": 5, "title": "Closed issue", "state": "closed"}]
    platform._sync_request.assert_called_once_with(
        "GET",
        "projects/group%2Fproject/issues?state=closed&per_page=3",
    )
