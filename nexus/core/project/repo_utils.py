"""Shared helpers for resolving project repo lists from project configuration."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any


def project_repos_from_config(
    project_name: str,
    project_cfg: dict[str, Any],
    get_project_repos: Callable[[str], list[str]],
) -> list[str]:
    """Return configured repo list for a project config payload."""
    repos: list[str] = []

    single_repo = None
    if isinstance(project_cfg, dict):
        single_repo = project_cfg.get("git_repo")
    if isinstance(single_repo, str) and single_repo.strip():
        repos.append(single_repo.strip())

    repo_list = None
    if isinstance(project_cfg, dict):
        repo_list = project_cfg.get("git_repos")
    if isinstance(repo_list, list):
        for repo_name in repo_list:
            if not isinstance(repo_name, str):
                continue
            value = repo_name.strip()
            if value and value not in repos:
                repos.append(value)

    if repos:
        return repos

    try:
        fallback = get_project_repos(project_name)
    except Exception:
        return []

    return [str(item).strip() for item in fallback if isinstance(item, str) and str(item).strip()]


def iter_project_configs(
    project_config: dict[str, Any],
    get_project_repos: Callable[[str], list[str]],
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield (project_key, project_cfg) pairs for configured projects with repos."""
    for project_key, project_cfg in project_config.items():
        if not isinstance(project_cfg, dict):
            continue
        if project_repos_from_config(project_key, project_cfg, get_project_repos):
            yield project_key, project_cfg
