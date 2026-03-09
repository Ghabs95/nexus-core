from unittest.mock import MagicMock

from nexus.plugins.builtin.gitlab_issue_plugin import GitLabIssueCLIPlugin


class _Result:
    def __init__(self, stdout: str):
        self.stdout = stdout
        self.returncode = 0


def test_example_usage_gitlab_get_issue_requests_comments(monkeypatch):
    plugin = GitLabIssueCLIPlugin({"repo": "group/project", "max_attempts": 2, "timeout": 30})

    notes_json = (
        '[{"id":901,"body":"## Verify Change Complete - reviewer\\n\\nReady for **@Deployer**",'
        '"created_at":"2026-03-08T15:45:23Z","updated_at":"2026-03-08T15:45:23Z"}]'
    )
    issue_json = '{"iid":113,"title":"Issue 113"}'

    platform = MagicMock()
    platform.side_effect = [_Result(issue_json), _Result(notes_json)]
    monkeypatch.setattr(plugin, "_run_with_retry", platform)

    issue = plugin.get_issue("113", ["title", "comments"])

    assert issue is not None
    assert issue["title"] == "Issue 113"
    assert issue["comments"][0]["body"].startswith("## Verify Change Complete")
