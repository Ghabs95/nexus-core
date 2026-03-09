"""Tests for built-in GitHub API issue plugin."""

from unittest.mock import MagicMock

from nexus.plugins.builtin.github_issue_plugin import GitHubIssuePlugin


def test_builtin_api_plugin_retries_without_labels(monkeypatch):
    plugin = GitHubIssuePlugin({"repo": "owner/repo"})
    calls: list[tuple[str, str, dict | None]] = []

    def _sync_request(method, endpoint, payload=None):  # noqa: ANN001
        calls.append((method, endpoint, payload))
        if payload and payload.get("labels"):
            raise RuntimeError("label create failed")
        return {"html_url": "https://github.com/owner/repo/issues/42"}

    platform = MagicMock()
    platform._sync_request.side_effect = _sync_request
    monkeypatch.setattr(plugin, "_platform", lambda: platform)

    url = plugin.create_issue("Test", "Body", labels=["project:nexus"])

    assert url == "https://github.com/owner/repo/issues/42"
    assert calls == [
        (
            "POST",
            "repos/owner/repo/issues",
            {"title": "Test", "body": "Body", "labels": ["project:nexus"]},
        ),
        ("POST", "repos/owner/repo/issues", {"title": "Test", "body": "Body"}),
    ]


def test_builtin_api_plugin_get_issue_includes_comments(monkeypatch):
    plugin = GitHubIssuePlugin({"repo": "owner/repo"})

    issue_payload = {
        "title": "T",
        "body": "B",
        "state": "open",
        "number": 113,
        "html_url": "https://github.com/owner/repo/issues/113",
        "created_at": "2026-03-08T15:00:00Z",
        "updated_at": "2026-03-08T15:01:00Z",
        "labels": [{"name": "workflow:shortened"}],
    }
    comments_payload = [
        {
            "id": 4019285436,
            "body": "## Implement Change Complete - developer\\n\\nReady for **@Reviewer**",
            "created_at": "2026-03-08T15:40:54Z",
            "updated_at": "2026-03-08T15:40:54Z",
        }
    ]

    calls: list[tuple[str, str]] = []

    def _sync_request(method, endpoint, payload=None):  # noqa: ANN001
        assert payload is None
        calls.append((method, endpoint))
        if endpoint.endswith("/comments"):
            return comments_payload
        return issue_payload

    platform = MagicMock()
    platform._sync_request.side_effect = _sync_request
    monkeypatch.setattr(plugin, "_platform", lambda: platform)

    issue = plugin.get_issue("113", ["title", "comments", "updatedAt"])

    assert issue == {
        "title": "T",
        "comments": [
            {
                "id": 4019285436,
                "body": "## Implement Change Complete - developer\\n\\nReady for **@Reviewer**",
                "createdAt": "2026-03-08T15:40:54Z",
                "updatedAt": "2026-03-08T15:40:54Z",
            }
        ],
        "updatedAt": "2026-03-08T15:01:00Z",
    }
    assert calls == [
        ("GET", "repos/owner/repo/issues/113"),
        ("GET", "repos/owner/repo/issues/113/comments"),
    ]


def test_builtin_api_plugin_add_assignee_me(monkeypatch):
    plugin = GitHubIssuePlugin({"repo": "owner/repo"})
    calls: list[tuple[str, str, dict | None]] = []

    def _sync_request(method, endpoint, payload=None):  # noqa: ANN001
        calls.append((method, endpoint, payload))
        if endpoint == "user":
            return {"login": "octocat"}
        if method == "GET":
            return {"assignees": []}
        return {}

    platform = MagicMock()
    platform._sync_request.side_effect = _sync_request
    monkeypatch.setattr(plugin, "_platform", lambda: platform)

    success = plugin.add_assignee("42", "@me")

    assert success is True
    assert calls == [
        ("GET", "user", None),
        ("GET", "repos/owner/repo/issues/42", None),
        ("PATCH", "repos/owner/repo/issues/42", {"assignees": ["octocat"]}),
    ]


def test_builtin_api_plugin_lists_issues_filters_prs(monkeypatch):
    plugin = GitHubIssuePlugin({"repo": "owner/repo"})
    data = [
        {"number": 1, "title": "A", "state": "open", "labels": [], "html_url": "u1"},
        {"number": 2, "title": "PR", "state": "open", "pull_request": {}, "labels": []},
    ]

    platform = MagicMock()
    platform._sync_request.return_value = data
    monkeypatch.setattr(plugin, "_platform", lambda: platform)

    issues = plugin.list_issues(state="open", limit=5)

    assert issues == [{"number": 1, "title": "A", "state": "open"}]
    platform._sync_request.assert_called_once_with(
        "GET",
        "repos/owner/repo/issues?state=open&per_page=5",
    )
