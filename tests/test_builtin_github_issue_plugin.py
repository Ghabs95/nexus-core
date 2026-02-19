"""Tests for built-in GitHub issue plugin."""

import subprocess

from nexus.plugins.builtin.github_issue_plugin import GitHubIssueCLIPlugin


class _Result:
    def __init__(self, stdout: str):
        self.stdout = stdout
        self.returncode = 0


def test_builtin_plugin_retries_without_labels(monkeypatch):
    plugin = GitHubIssueCLIPlugin({"repo": "owner/repo", "max_attempts": 3, "timeout": 30})
    calls = {"count": 0}

    def _fake_run(cmd, check, timeout, capture_output, text):
        calls["count"] += 1
        if "--label" in cmd:
            raise subprocess.CalledProcessError(1, cmd, stderr="label failed")
        return _Result("https://github.com/owner/repo/issues/42\n")

    monkeypatch.setattr("nexus.plugins.builtin.github_issue_plugin.subprocess.run", _fake_run)

    url = plugin.create_issue(
        title="Test",
        body="Body",
        labels=["project:nexus", "type:feature", "workflow:fast-track"],
    )

    assert calls["count"] == 4
    assert url == "https://github.com/owner/repo/issues/42"


def test_builtin_plugin_add_comment(monkeypatch):
    plugin = GitHubIssueCLIPlugin({"repo": "owner/repo", "max_attempts": 2, "timeout": 30})
    captured = {"cmd": None}

    def _fake_run(cmd, check, timeout, capture_output, text):
        captured["cmd"] = cmd
        return _Result("ok")

    monkeypatch.setattr("nexus.plugins.builtin.github_issue_plugin.subprocess.run", _fake_run)

    success = plugin.add_comment("42", "hello")

    assert success is True
    assert captured["cmd"] == [
        "gh", "issue", "comment", "42", "--repo", "owner/repo", "--body", "hello"
    ]


def test_builtin_plugin_issue_ops(monkeypatch):
    plugin = GitHubIssueCLIPlugin({"repo": "owner/repo", "max_attempts": 2, "timeout": 30})
    commands = []

    def _fake_run(cmd, check, timeout, capture_output, text):
        commands.append((cmd, check))
        if cmd[:3] == ["gh", "issue", "view"]:
            return _Result('{"title":"T","body":"B"}')
        if cmd[:3] == ["gh", "issue", "list"]:
            return _Result('[{"number":1,"title":"A","state":"open"}]')
        return _Result("ok")

    monkeypatch.setattr("nexus.plugins.builtin.github_issue_plugin.subprocess.run", _fake_run)

    assert plugin.ensure_label("agent:requested", "E6E6FA", "Requested") is True
    assert plugin.add_label("42", "agent:requested") is True
    issue = plugin.get_issue("42", ["title", "body"])
    assert issue == {"title": "T", "body": "B"}
    assert plugin.add_assignee("42", "@me") is True
    assert plugin.update_issue_body("42", "new body") is True
    assert plugin.close_issue("42") is True
    issues = plugin.list_issues(state="open", limit=5)
    assert issues == [{"number": 1, "title": "A", "state": "open"}]

    assert commands[0][0][:3] == ["gh", "label", "create"]
    assert commands[0][1] is False
    assert commands[1][0][:3] == ["gh", "issue", "edit"]
    assert commands[1][1] is True
    assert commands[2][0][:3] == ["gh", "issue", "view"]
    assert commands[2][1] is True
    assert commands[3][0][:3] == ["gh", "issue", "edit"]
    assert commands[3][1] is True
    assert commands[4][0][:3] == ["gh", "issue", "edit"]
    assert commands[4][1] is True
    assert commands[5][0][:3] == ["gh", "issue", "close"]
    assert commands[5][1] is True
    assert commands[6][0][:3] == ["gh", "issue", "list"]
    assert commands[6][1] is True
