"""Tests for automatic PR-to-issue linking in GitHubPlatform.create_pr_from_changes()."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nexus.adapters.git.github import GitHubPlatform


class TestPRAutoLinking:
    @pytest.fixture
    def platform(self):
        with patch.object(GitHubPlatform, "_check_gh_cli"):
            return GitHubPlatform("owner/repo")

    @staticmethod
    def _fake_git_run(feature_branch: str = "main", *, linked_worktree: bool = True):
        def fake_run(cmd, **kwargs):
            m = MagicMock()
            m.returncode = 0
            m.stdout = ""
            m.stderr = ""
            if cmd[0] == "git":
                subcmd = cmd[1] if len(cmd) > 1 else ""
                if subcmd == "rev-parse" and "--is-inside-work-tree" in cmd:
                    m.stdout = "true\n"
                elif subcmd == "rev-parse" and "--absolute-git-dir" in cmd:
                    if linked_worktree:
                        m.stdout = "/tmp/repo/.git/worktrees/issue-42\n"
                    else:
                        m.stdout = "/tmp/repo/.git\n"
                elif subcmd == "rev-parse" and "--git-common-dir" in cmd:
                    m.stdout = "/tmp/repo/.git\n"
                elif subcmd == "diff":
                    m.stdout = " file.py | 2 +-\n"
                elif subcmd == "rev-parse" and "--abbrev-ref" in cmd:
                    m.stdout = f"{feature_branch}\n"
                elif subcmd == "ls-files":
                    m.stdout = ""
            return m

        return fake_run

    @pytest.mark.asyncio
    async def test_appends_closes_when_missing(self, platform, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        mock_post = AsyncMock(
            return_value={
                "id": 99,
                "number": 99,
                "title": "fix: stuff",
                "state": "open",
                "head": {"ref": "nexus/issue-42"},
                "base": {"ref": "main"},
                "html_url": "https://github.com/owner/repo/pull/99",
            }
        )
        with patch("subprocess.run", side_effect=self._fake_git_run()):
            with patch.object(platform, "_post", mock_post):
                result = await platform.create_pr_from_changes(
                    repo_dir=str(repo),
                    issue_number="42",
                    title="fix: stuff",
                    body="Some changes here",
                )
        assert result is not None
        payload = mock_post.await_args.args[1]
        assert "Closes #42" in payload["body"]

    @pytest.mark.asyncio
    async def test_no_duplicate_when_closes_present(self, platform, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        mock_post = AsyncMock(return_value={"id": 99, "number": 99, "title": "fix", "state": "open", "head": {"ref": "nexus/issue-42"}, "base": {"ref": "main"}, "html_url": "https://github.com/owner/repo/pull/99"})
        with patch("subprocess.run", side_effect=self._fake_git_run()):
            with patch.object(platform, "_post", mock_post):
                result = await platform.create_pr_from_changes(
                    repo_dir=str(repo),
                    issue_number="42",
                    title="fix: stuff",
                    body="My PR body.\n\nCloses #42",
                )
        assert result is not None
        assert mock_post.await_args.args[1]["body"].count("Closes #42") == 1

    @pytest.mark.asyncio
    async def test_no_duplicate_with_fixes_keyword(self, platform, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        mock_post = AsyncMock(return_value={"id": 99, "number": 99, "title": "fix", "state": "open", "head": {"ref": "nexus/issue-42"}, "base": {"ref": "main"}, "html_url": "https://github.com/owner/repo/pull/99"})
        with patch("subprocess.run", side_effect=self._fake_git_run()):
            with patch.object(platform, "_post", mock_post):
                result = await platform.create_pr_from_changes(
                    repo_dir=str(repo),
                    issue_number="42",
                    title="fix: stuff",
                    body="Bug fix.\n\nFixes #42",
                )
        assert result is not None
        body = mock_post.await_args.args[1]["body"]
        assert "Closes #42" not in body
        assert "Fixes #42" in body

    @pytest.mark.asyncio
    async def test_no_duplicate_with_resolves_keyword(self, platform, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        mock_post = AsyncMock(return_value={"id": 99, "number": 99, "title": "fix", "state": "open", "head": {"ref": "nexus/issue-42"}, "base": {"ref": "main"}, "html_url": "https://github.com/owner/repo/pull/99"})
        with patch("subprocess.run", side_effect=self._fake_git_run()):
            with patch.object(platform, "_post", mock_post):
                result = await platform.create_pr_from_changes(
                    repo_dir=str(repo),
                    issue_number="42",
                    title="fix: stuff",
                    body="Done.\n\nResolves #42",
                )
        assert result is not None
        body = mock_post.await_args.args[1]["body"]
        assert "Closes #42" not in body
        assert "Resolves #42" in body

    @pytest.mark.asyncio
    async def test_cross_repo_uses_fully_qualified_reference(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        with patch.object(GitHubPlatform, "_check_gh_cli"):
            platform = GitHubPlatform("owner/impl-repo")
        mock_post = AsyncMock(return_value={"id": 99, "number": 99, "title": "fix", "state": "open", "head": {"ref": "nexus/issue-42"}, "base": {"ref": "main"}, "html_url": "https://github.com/owner/impl-repo/pull/99"})
        with patch("subprocess.run", side_effect=self._fake_git_run()):
            with patch.object(platform, "_post", mock_post):
                result = await platform.create_pr_from_changes(
                    repo_dir=str(repo),
                    issue_number="42",
                    title="fix: stuff",
                    body="Some changes here",
                    issue_repo="owner/workflow-repo",
                )
        assert result is not None
        assert "Closes owner/workflow-repo#42" in mock_post.await_args.args[1]["body"]

    @pytest.mark.asyncio
    async def test_cross_repo_does_not_duplicate_fully_qualified_reference(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        with patch.object(GitHubPlatform, "_check_gh_cli"):
            platform = GitHubPlatform("owner/impl-repo")
        mock_post = AsyncMock(return_value={"id": 99, "number": 99, "title": "fix", "state": "open", "head": {"ref": "nexus/issue-42"}, "base": {"ref": "main"}, "html_url": "https://github.com/owner/impl-repo/pull/99"})
        with patch("subprocess.run", side_effect=self._fake_git_run()):
            with patch.object(platform, "_post", mock_post):
                result = await platform.create_pr_from_changes(
                    repo_dir=str(repo),
                    issue_number="42",
                    title="fix: stuff",
                    body="Bug fix.\n\nCloses owner/workflow-repo#42",
                    issue_repo="owner/workflow-repo",
                )
        assert result is not None
        assert mock_post.await_args.args[1]["body"].count("Closes owner/workflow-repo#42") == 1

    @pytest.mark.asyncio
    async def test_reuses_current_feature_branch_for_pr_head(self, platform, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        seen_commands: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            seen_commands.append(list(cmd))
            return self._fake_git_run(feature_branch="feat/universal-nexus-identity-issue-86")(cmd, **kwargs)

        mock_post = AsyncMock(return_value={"id": 99, "number": 99, "title": "feat: UNI", "state": "open", "head": {"ref": "feat/universal-nexus-identity-issue-86"}, "base": {"ref": "main"}, "html_url": "https://github.com/owner/repo/pull/99"})
        with patch("subprocess.run", side_effect=fake_run):
            with patch.object(platform, "_post", mock_post):
                result = await platform.create_pr_from_changes(
                    repo_dir=str(repo),
                    issue_number="86",
                    title="feat: UNI",
                    body="Automated change",
                )
        assert result is not None
        assert mock_post.await_args.args[1]["head"] == "feat/universal-nexus-identity-issue-86"
        assert result.head_branch == "feat/universal-nexus-identity-issue-86"
        assert ["git", "checkout", "-b", "nexus/issue-86"] not in seen_commands

    @pytest.mark.asyncio
    async def test_accepts_git_worktree_without_dotgit_directory(self, platform, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        mock_post = AsyncMock(return_value={"id": 101, "number": 101, "title": "fix: worktree support", "state": "open", "head": {"ref": "nexus/issue-42"}, "base": {"ref": "main"}, "html_url": "https://github.com/owner/repo/pull/101"})
        with patch("subprocess.run", side_effect=self._fake_git_run()):
            with patch.object(platform, "_post", mock_post):
                result = await platform.create_pr_from_changes(
                    repo_dir=str(repo),
                    issue_number="42",
                    title="fix: worktree support",
                    body="Body",
                )
        assert result is not None

    @pytest.mark.asyncio
    async def test_rejects_primary_checkout_for_local_pr_creation(self, platform, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        mock_post = AsyncMock()

        with patch("subprocess.run", side_effect=self._fake_git_run(linked_worktree=False)):
            with patch.object(platform, "_post", mock_post):
                result = await platform.create_pr_from_changes(
                    repo_dir=str(repo),
                    issue_number="42",
                    title="fix: worktree support",
                    body="Body",
                )

        assert result is None
        mock_post.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_open_issues_github_adapter(tmp_path):
    with patch.object(GitHubPlatform, "_check_gh_cli"):
        platform = GitHubPlatform("owner/repo")

    sample = [
        {
            "id": 420,
            "number": 42,
            "title": "Workflow issue",
            "body": "Body",
            "state": "open",
            "labels": [{"name": "workflow:full"}],
            "created_at": "2026-02-20T00:00:00Z",
            "updated_at": "2026-02-20T00:01:00Z",
            "html_url": "https://github.com/owner/repo/issues/42",
        }
    ]

    with patch.object(platform, "_get", new=AsyncMock(return_value=sample)) as mock_get:
        issues = await platform.list_open_issues(limit=20, labels=["workflow:full"])

    assert len(issues) == 1
    assert issues[0].number == 42
    called_path = mock_get.await_args.args[0]
    assert called_path.startswith("repos/owner/repo/issues?")
    assert "labels=workflow%3Afull" in called_path
