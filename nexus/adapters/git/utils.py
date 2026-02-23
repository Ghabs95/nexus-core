"""Helpers for provider-aware repository and issue URL handling."""

from __future__ import annotations

from typing import Any, Dict, Optional


def resolve_repo(config: Optional[Dict[str, Any]], default_repo: str) -> str:
    """Resolve repository slug from project config with legacy fallback."""
    if not isinstance(config, dict):
        return default_repo

    repo = config.get("git_repo") or config.get("github_repo")
    if isinstance(repo, str) and repo.strip():
        return repo.strip()
    return default_repo


def build_issue_url(repo: str, issue_num: str, config: Optional[Dict[str, Any]]) -> str:
    """Build issue URL for configured git platform (GitHub/GitLab)."""
    if not isinstance(config, dict):
        return f"https://github.com/{repo}/issues/{issue_num}"

    platform = str(config.get("git_platform", "github")).lower().strip()
    if platform == "gitlab":
        base_url = str(config.get("gitlab_base_url", "https://gitlab.com")).rstrip("/")
        return f"{base_url}/{repo}/-/issues/{issue_num}"

    return f"https://github.com/{repo}/issues/{issue_num}"
