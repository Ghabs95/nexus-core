"""Tests for automatic PR-to-issue linking in GitHubPlatform.create_pr_from_changes()."""
import json
from unittest.mock import MagicMock, patch

import pytest

from nexus.adapters.git.github import GitHubPlatform


class TestPRAutoLinking:
    """Verify that create_pr_from_changes injects an issue-closing reference."""

    def _extract_body_from_call(self, mock_run, call_index=-1):
        """Extract the --body value from a subprocess.run call."""
        args = mock_run.call_args_list[call_index]
        cmd = args[0][0] if args[0] else args[1].get("args", [])
        for i, arg in enumerate(cmd):
            if arg == "--body" and i + 1 < len(cmd):
                return cmd[i + 1]
        return None

    @pytest.fixture
    def platform(self):
        with patch.object(GitHubPlatform, "_check_gh_cli"):
            return GitHubPlatform("owner/repo")

    @pytest.mark.asyncio
    async def test_appends_closes_when_missing(self, platform, tmp_path):
        """Body without closing keyword gets 'Closes #42' appended."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()

        body_sent = None

        def fake_run(cmd, **kwargs):
            nonlocal body_sent
            m = MagicMock()
            m.returncode = 0
            m.stdout = ""
            m.stderr = ""

            if cmd[0] == "git":
                subcmd = cmd[1] if len(cmd) > 1 else ""
                if subcmd == "diff":
                    m.stdout = " file.py | 2 +-\n"
                elif subcmd == "rev-parse":
                    m.stdout = "main"
                elif subcmd == "ls-files" or subcmd == "push":
                    m.stdout = ""
            elif cmd[0] == "gh":
                # Capture the body passed to gh pr create
                for i, arg in enumerate(cmd):
                    if arg == "--body" and i + 1 < len(cmd):
                        body_sent = cmd[i + 1]
                m.stdout = "https://github.com/owner/repo/pull/99"
            return m

        with patch("subprocess.run", side_effect=fake_run):
            result = await platform.create_pr_from_changes(
                repo_dir=str(repo),
                issue_number="42",
                title="fix: stuff",
                body="Some changes here",
            )

        assert result is not None
        assert body_sent is not None
        assert "Closes #42" in body_sent

    @pytest.mark.asyncio
    async def test_no_duplicate_when_closes_present(self, platform, tmp_path):
        """Body already containing 'Closes #42' should not get a duplicate."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()

        body_sent = None

        def fake_run(cmd, **kwargs):
            nonlocal body_sent
            m = MagicMock()
            m.returncode = 0
            m.stdout = ""
            m.stderr = ""

            if cmd[0] == "git":
                subcmd = cmd[1] if len(cmd) > 1 else ""
                if subcmd == "diff":
                    m.stdout = " file.py | 2 +-\n"
                elif subcmd == "rev-parse":
                    m.stdout = "main"
                elif subcmd == "ls-files":
                    m.stdout = ""
            elif cmd[0] == "gh":
                for i, arg in enumerate(cmd):
                    if arg == "--body" and i + 1 < len(cmd):
                        body_sent = cmd[i + 1]
                m.stdout = "https://github.com/owner/repo/pull/99"
            return m

        with patch("subprocess.run", side_effect=fake_run):
            result = await platform.create_pr_from_changes(
                repo_dir=str(repo),
                issue_number="42",
                title="fix: stuff",
                body="My PR body.\n\nCloses #42",
            )

        assert result is not None
        assert body_sent is not None
        # Should appear exactly once
        assert body_sent.count("Closes #42") == 1

    @pytest.mark.asyncio
    async def test_no_duplicate_with_fixes_keyword(self, platform, tmp_path):
        """Body with 'Fixes #42' should not get an extra 'Closes #42'."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()

        body_sent = None

        def fake_run(cmd, **kwargs):
            nonlocal body_sent
            m = MagicMock()
            m.returncode = 0
            m.stdout = ""
            m.stderr = ""

            if cmd[0] == "git":
                subcmd = cmd[1] if len(cmd) > 1 else ""
                if subcmd == "diff":
                    m.stdout = " file.py | 2 +-\n"
                elif subcmd == "rev-parse":
                    m.stdout = "main"
                elif subcmd == "ls-files":
                    m.stdout = ""
            elif cmd[0] == "gh":
                for i, arg in enumerate(cmd):
                    if arg == "--body" and i + 1 < len(cmd):
                        body_sent = cmd[i + 1]
                m.stdout = "https://github.com/owner/repo/pull/99"
            return m

        with patch("subprocess.run", side_effect=fake_run):
            result = await platform.create_pr_from_changes(
                repo_dir=str(repo),
                issue_number="42",
                title="fix: stuff",
                body="Bug fix.\n\nFixes #42",
            )

        assert result is not None
        assert body_sent is not None
        assert "Closes #42" not in body_sent
        assert "Fixes #42" in body_sent

    @pytest.mark.asyncio
    async def test_no_duplicate_with_resolves_keyword(self, platform, tmp_path):
        """Body with 'Resolves #42' should not get an extra 'Closes #42'."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()

        body_sent = None

        def fake_run(cmd, **kwargs):
            nonlocal body_sent
            m = MagicMock()
            m.returncode = 0
            m.stdout = ""
            m.stderr = ""

            if cmd[0] == "git":
                subcmd = cmd[1] if len(cmd) > 1 else ""
                if subcmd == "diff":
                    m.stdout = " file.py | 2 +-\n"
                elif subcmd == "rev-parse":
                    m.stdout = "main"
                elif subcmd == "ls-files":
                    m.stdout = ""
            elif cmd[0] == "gh":
                for i, arg in enumerate(cmd):
                    if arg == "--body" and i + 1 < len(cmd):
                        body_sent = cmd[i + 1]
                m.stdout = "https://github.com/owner/repo/pull/99"
            return m

        with patch("subprocess.run", side_effect=fake_run):
            result = await platform.create_pr_from_changes(
                repo_dir=str(repo),
                issue_number="42",
                title="fix: stuff",
                body="Done.\n\nResolves #42",
            )

        assert result is not None
        assert body_sent is not None
        assert "Closes #42" not in body_sent
        assert "Resolves #42" in body_sent

    @pytest.mark.asyncio
    async def test_cross_repo_uses_fully_qualified_reference(self, tmp_path):
        """Cross-repo linkage appends 'Closes owner/repo#N'."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()

        body_sent = None

        with patch.object(GitHubPlatform, "_check_gh_cli"):
            platform = GitHubPlatform("owner/impl-repo")

        def fake_run(cmd, **kwargs):
            nonlocal body_sent
            m = MagicMock()
            m.returncode = 0
            m.stdout = ""
            m.stderr = ""

            if cmd[0] == "git":
                subcmd = cmd[1] if len(cmd) > 1 else ""
                if subcmd == "diff":
                    m.stdout = " file.py | 2 +-\n"
                elif subcmd == "rev-parse":
                    m.stdout = "main"
                elif subcmd == "ls-files":
                    m.stdout = ""
            elif cmd[0] == "gh":
                for i, arg in enumerate(cmd):
                    if arg == "--body" and i + 1 < len(cmd):
                        body_sent = cmd[i + 1]
                m.stdout = "https://github.com/owner/impl-repo/pull/99"
            return m

        with patch("subprocess.run", side_effect=fake_run):
            result = await platform.create_pr_from_changes(
                repo_dir=str(repo),
                issue_number="42",
                title="fix: stuff",
                body="Some changes here",
                issue_repo="owner/workflow-repo",
            )

        assert result is not None
        assert body_sent is not None
        assert "Closes owner/workflow-repo#42" in body_sent

    @pytest.mark.asyncio
    async def test_cross_repo_does_not_duplicate_fully_qualified_reference(self, tmp_path):
        """Cross-repo linkage should not append duplicate fully-qualified reference."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()

        body_sent = None

        with patch.object(GitHubPlatform, "_check_gh_cli"):
            platform = GitHubPlatform("owner/impl-repo")

        def fake_run(cmd, **kwargs):
            nonlocal body_sent
            m = MagicMock()
            m.returncode = 0
            m.stdout = ""
            m.stderr = ""

            if cmd[0] == "git":
                subcmd = cmd[1] if len(cmd) > 1 else ""
                if subcmd == "diff":
                    m.stdout = " file.py | 2 +-\n"
                elif subcmd == "rev-parse":
                    m.stdout = "main"
                elif subcmd == "ls-files":
                    m.stdout = ""
            elif cmd[0] == "gh":
                for i, arg in enumerate(cmd):
                    if arg == "--body" and i + 1 < len(cmd):
                        body_sent = cmd[i + 1]
                m.stdout = "https://github.com/owner/impl-repo/pull/99"
            return m

        with patch("subprocess.run", side_effect=fake_run):
            result = await platform.create_pr_from_changes(
                repo_dir=str(repo),
                issue_number="42",
                title="fix: stuff",
                body="Bug fix.\n\nCloses owner/workflow-repo#42",
                issue_repo="owner/workflow-repo",
            )

        assert result is not None
        assert body_sent is not None
        assert body_sent.count("Closes owner/workflow-repo#42") == 1

    @pytest.mark.asyncio
    async def test_reuses_current_feature_branch_for_pr_head(self, platform, tmp_path):
        """When already on a feature branch, PR head should reuse it."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()

        seen_commands: list[list[str]] = []
        head_branch = None

        def fake_run(cmd, **kwargs):
            nonlocal head_branch
            seen_commands.append(list(cmd))
            m = MagicMock()
            m.returncode = 0
            m.stdout = ""
            m.stderr = ""

            if cmd[0] == "git":
                subcmd = cmd[1] if len(cmd) > 1 else ""
                if subcmd == "diff":
                    m.stdout = " file.py | 2 +-\n"
                elif subcmd == "rev-parse":
                    m.stdout = "feat/universal-nexus-identity-issue-86"
                elif subcmd == "ls-files":
                    m.stdout = ""
            elif cmd[0] == "gh":
                if "--head" in cmd:
                    idx = cmd.index("--head")
                    if idx + 1 < len(cmd):
                        head_branch = cmd[idx + 1]
                m.stdout = "https://github.com/owner/repo/pull/99"
            return m

        with patch("subprocess.run", side_effect=fake_run):
            result = await platform.create_pr_from_changes(
                repo_dir=str(repo),
                issue_number="86",
                title="feat: UNI",
                body="Automated change",
            )

        assert result is not None
        assert head_branch == "feat/universal-nexus-identity-issue-86"
        assert result.head_branch == "feat/universal-nexus-identity-issue-86"
        assert ["git", "checkout", "-b", "nexus/issue-86"] not in seen_commands


@pytest.mark.asyncio
async def test_list_open_issues_github_adapter(tmp_path):
    with patch.object(GitHubPlatform, "_check_gh_cli"):
        platform = GitHubPlatform("owner/repo")

    sample = [
        {
            "number": 42,
            "title": "Workflow issue",
            "body": "Body",
            "state": "OPEN",
            "labels": [{"name": "workflow:full"}],
            "createdAt": "2026-02-20T00:00:00Z",
            "updatedAt": "2026-02-20T00:01:00Z",
            "url": "https://github.com/owner/repo/issues/42",
        }
    ]

    with patch.object(platform, "_run_gh_command", return_value=json.dumps(sample)) as mock_run:
        issues = await platform.list_open_issues(limit=20, labels=["workflow:full"])

    assert len(issues) == 1
    assert issues[0].number == 42
    called_args = mock_run.call_args.args[0]
    assert "issue" in called_args
    assert "list" in called_args
    assert "--label" in called_args
