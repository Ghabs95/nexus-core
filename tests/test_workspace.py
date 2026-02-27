"""Tests for nexus.core.workspace — WorkspaceManager."""

import os
from unittest import mock

from nexus.core.workspace import WorkspaceManager

# ---------------------------------------------------------------------------
# provision_worktree – branch_name parameter
# ---------------------------------------------------------------------------


def _mock_subprocess_run_ok(*args, **kwargs):
    """subprocess.run stub that always succeeds."""
    result = mock.MagicMock()
    result.returncode = 1  # branch does NOT exist → triggers creation
    result.stdout = ""
    result.stderr = ""
    return result


class TestProvisionWorktreeDefaultBranch:
    """Without explicit branch_name, falls back to ``nexus/issue-{N}``."""

    @mock.patch("nexus.core.workspace.subprocess.run", side_effect=_mock_subprocess_run_ok)
    @mock.patch("os.makedirs")
    @mock.patch("os.path.isdir", return_value=False)
    def test_default_branch_name(self, mock_isdir, mock_makedirs, mock_run, tmp_path):
        base = str(tmp_path)
        result = WorkspaceManager.provision_worktree(base, "42")

        expected_dir = os.path.join(base, ".nexus", "worktrees", "issue-42")
        assert result == expected_dir

        # Second subprocess call should create worktree with default branch
        worktree_call = mock_run.call_args_list[-1]
        cmd = worktree_call[0][0]
        assert cmd == ["git", "worktree", "add", "-b", "nexus/issue-42", expected_dir]


class TestProvisionWorktreeCustomBranch:
    """When branch_name is provided, it should be used."""

    @mock.patch("nexus.core.workspace.subprocess.run", side_effect=_mock_subprocess_run_ok)
    @mock.patch("os.makedirs")
    @mock.patch("os.path.isdir", return_value=False)
    def test_custom_branch_name(self, mock_isdir, mock_makedirs, mock_run, tmp_path):
        base = str(tmp_path)
        result = WorkspaceManager.provision_worktree(base, "42", branch_name="feat/my-feature")

        expected_dir = os.path.join(base, ".nexus", "worktrees", "issue-42")
        assert result == expected_dir

        # Should use the custom branch name
        worktree_call = mock_run.call_args_list[-1]
        cmd = worktree_call[0][0]
        assert cmd == ["git", "worktree", "add", "-b", "feat/my-feature", expected_dir]

    @mock.patch("nexus.core.workspace.subprocess.run", side_effect=_mock_subprocess_run_ok)
    @mock.patch("os.makedirs")
    @mock.patch("os.path.isdir", return_value=False)
    def test_empty_string_branch_falls_back_to_default(
        self, mock_isdir, mock_makedirs, mock_run, tmp_path
    ):
        base = str(tmp_path)
        WorkspaceManager.provision_worktree(base, "42", branch_name="")

        worktree_call = mock_run.call_args_list[-1]
        cmd = worktree_call[0][0]
        # Empty string is falsy, should fallback to default
        assert cmd == [
            "git",
            "worktree",
            "add",
            "-b",
            "nexus/issue-42",
            os.path.join(base, ".nexus", "worktrees", "issue-42"),
        ]

    @mock.patch("nexus.core.workspace.subprocess.run", side_effect=_mock_subprocess_run_ok)
    @mock.patch("os.makedirs")
    @mock.patch("os.path.isdir", return_value=False)
    def test_none_branch_falls_back_to_default(self, mock_isdir, mock_makedirs, mock_run, tmp_path):
        base = str(tmp_path)
        WorkspaceManager.provision_worktree(base, "42", branch_name=None)

        worktree_call = mock_run.call_args_list[-1]
        cmd = worktree_call[0][0]
        assert cmd == [
            "git",
            "worktree",
            "add",
            "-b",
            "nexus/issue-42",
            os.path.join(base, ".nexus", "worktrees", "issue-42"),
        ]
