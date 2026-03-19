"""Safety checks for local git operations that must run inside worktrees."""

import logging
import os
import subprocess

logger = logging.getLogger(__name__)
_ALLOW_PRIMARY_CHECKOUT_ENV = "NEXUS_ALLOW_PRIMARY_CHECKOUT_BRANCHING"


def _resolve_git_path(repo_dir: str, value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    if os.path.isabs(normalized):
        return os.path.abspath(normalized)
    return os.path.abspath(os.path.join(repo_dir, normalized))


def is_safe_local_checkout(repo_dir: str) -> bool:
    """Return True when repo_dir is a linked worktree or explicitly allowed."""
    if str(os.getenv(_ALLOW_PRIMARY_CHECKOUT_ENV, "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return True

    try:
        git_dir_result = subprocess.run(
            ["git", "rev-parse", "--absolute-git-dir"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        common_dir_result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return False

    if git_dir_result.returncode != 0 or common_dir_result.returncode != 0:
        return False

    git_dir = _resolve_git_path(repo_dir, git_dir_result.stdout)
    common_dir = _resolve_git_path(repo_dir, common_dir_result.stdout)
    if not git_dir or not common_dir:
        return False

    return git_dir != common_dir


def ensure_safe_local_checkout(repo_dir: str, *, issue_number: str) -> bool:
    """Return False when local branch-changing operations would hit the primary checkout."""
    if is_safe_local_checkout(repo_dir):
        return True

    logger.error(
        "Refusing local branch/PR operations for issue #%s in primary checkout %s. "
        "Use an issue worktree or set %s=1 for an explicit override.",
        issue_number,
        repo_dir,
        _ALLOW_PRIMARY_CHECKOUT_ENV,
    )
    return False
