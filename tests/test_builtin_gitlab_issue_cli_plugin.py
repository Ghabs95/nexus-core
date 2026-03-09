"""Tests for built-in GitLab issue plugin."""

import json

from nexus.plugins.builtin.gitlab_issue_cli_plugin import GitLabIssueCLIPlugin


class _Result:
    def __init__(self, stdout: str):
        self.stdout = stdout
        self.returncode = 0


def test_gitlab_get_issue_includes_comments_when_requested(monkeypatch):
    plugin = GitLabIssueCLIPlugin({"repo": "group/project", "max_attempts": 2, "timeout": 30})

    calls: list[list[str]] = []

    def _fake_run_with_retry(cmd, max_attempts):  # noqa: ANN001
        calls.append(cmd)
        if cmd[-1].endswith("/notes"):
            return _Result(
                json.dumps(
                    [
                        {
                            "id": 901,
                            "body": "## Verify Change Complete - reviewer\\n\\nReady for **@Deployer**",
                            "created_at": "2026-03-08T15:45:23Z",
                            "updated_at": "2026-03-08T15:45:23Z",
                        }
                    ]
                )
            )
        return _Result(
            json.dumps(
                {
                    "iid": 113,
                    "title": "Issue 113",
                    "description": "Body",
                    "state": "opened",
                }
            )
        )

    monkeypatch.setattr(plugin, "_run_with_retry", _fake_run_with_retry)

    issue = plugin.get_issue("113", ["title", "body", "number", "comments"])

    assert issue == {
        "title": "Issue 113",
        "body": "Body",
        "number": 113,
        "comments": [
            {
                "id": 901,
                "body": "## Verify Change Complete - reviewer\\n\\nReady for **@Deployer**",
                "createdAt": "2026-03-08T15:45:23Z",
                "updatedAt": "2026-03-08T15:45:23Z",
            }
        ],
    }
    assert len(calls) == 2
    assert calls[0][:2] == ["glab", "api"]
    assert calls[1][-1].endswith("/issues/113/notes")


def test_gitlab_get_issue_skips_comments_call_when_not_requested(monkeypatch):
    plugin = GitLabIssueCLIPlugin({"repo": "group/project", "max_attempts": 2, "timeout": 30})

    calls: list[list[str]] = []

    def _fake_run_with_retry(cmd, max_attempts):  # noqa: ANN001
        calls.append(cmd)
        return _Result(json.dumps({"iid": 113, "title": "Issue 113"}))

    monkeypatch.setattr(plugin, "_run_with_retry", _fake_run_with_retry)

    issue = plugin.get_issue("113", ["title", "number"])

    assert issue == {"title": "Issue 113", "number": 113}
    assert len(calls) == 1
