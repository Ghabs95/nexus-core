from __future__ import annotations

import os
import shutil
import time
from typing import Any, Callable
from urllib.parse import urlparse


def resolve_project_from_path(
    *,
    summary_path: str,
    project_config: dict[str, Any],
    iter_project_configs: Callable[..., Any],
    get_repos: Callable[[str], list[str]],
    base_dir: str,
) -> str:
    """Resolve project key from a completion summary file path."""
    for key, cfg in iter_project_configs(project_config, get_repos):
        workspace = cfg.get("workspace")
        if not workspace:
            continue
        workspace_abs = os.path.join(base_dir, str(workspace))
        if summary_path.startswith(workspace_abs):
            return key
    return ""


def extract_repo_from_issue_url(issue_url: str) -> str:
    """Extract ``namespace/repo`` from GitHub or GitLab issue URL."""
    if not issue_url:
        return ""
    try:
        parsed = urlparse(issue_url.strip())
        parts = [segment for segment in parsed.path.strip("/").split("/") if segment]
        if len(parts) >= 4 and parts[2].lower() == "issues":
            return f"{parts[0]}/{parts[1]}"
        if "-" in parts:
            dash_idx = parts.index("-")
            if dash_idx >= 1 and len(parts) > dash_idx + 2 and parts[dash_idx + 1] == "issues":
                return "/".join(parts[:dash_idx])
    except Exception:
        return ""
    return ""


def resolve_project_for_repo(
    *,
    repo_name: str,
    project_config: dict[str, Any],
    iter_project_configs: Callable[..., Any],
    project_repos_from_config: Callable[
        [str, dict[str, Any], Callable[[str], list[str]]], list[str]
    ],
    get_repos: Callable[[str], list[str]],
) -> str | None:
    """Resolve configured project key for a repository full name."""
    for key, cfg in iter_project_configs(project_config, get_repos):
        if repo_name in project_repos_from_config(key, cfg, get_repos):
            return key
    return None


def reroute_webhook_task_to_project(
    *,
    filepath: str,
    target_project: str,
    project_config: dict[str, Any],
    base_dir: str,
    get_inbox_dir: Callable[[str, str], str],
) -> str | None:
    """Move a webhook task file to the target project's inbox directory."""
    project_cfg = project_config.get(target_project)
    if not isinstance(project_cfg, dict):
        return None
    workspace_rel = project_cfg.get("workspace")
    if not workspace_rel:
        return None

    workspace_abs = os.path.join(base_dir, str(workspace_rel))
    inbox_dir = get_inbox_dir(workspace_abs, target_project)
    os.makedirs(inbox_dir, exist_ok=True)

    target_path = os.path.join(inbox_dir, os.path.basename(filepath))
    if os.path.abspath(target_path) == os.path.abspath(filepath):
        return target_path

    if os.path.exists(target_path):
        stem, ext = os.path.splitext(os.path.basename(filepath))
        target_path = os.path.join(inbox_dir, f"{stem}_{int(time.time())}{ext}")

    shutil.move(filepath, target_path)
    return target_path


def resolve_repo_strict(
    *,
    project_name: str,
    issue_num: str,
    project_config: dict[str, Any],
    get_default_project: Callable[[], str],
    get_repo: Callable[[str], str],
    get_repos: Callable[[str], list[str]],
    project_repos_from_config: Callable[
        [str, dict[str, Any], Callable[[str], list[str]]], list[str]
    ],
    resolve_repo_for_issue: Callable[[str, str | None], str],
    logger: Any,
    emit_alert: Callable[..., Any],
) -> str:
    """Resolve repo with boundary checks between project and issue context."""
    project_repos: list[str] = []
    if project_name and project_name in project_config:
        project_repos = project_repos_from_config(
            project_name, project_config[project_name], get_repos
        )

    issue_repo = resolve_repo_for_issue(issue_num, project_name or get_default_project())
    if project_repos and issue_repo and issue_repo not in project_repos:
        message = (
            f"🚫 Project boundary mismatch for issue #{issue_num}: "
            f"project '{project_name}' repos {project_repos}, issue context -> {issue_repo}. "
            "Workflow finalization blocked."
        )
        logger.error(message)
        emit_alert(message, severity="error", source="inbox_processor")
        raise ValueError(message)

    return issue_repo or (project_repos[0] if project_repos else get_repo(get_default_project()))


def resolve_git_dir(
    *,
    project_name: str,
    project_config: dict[str, Any],
    base_dir: str,
) -> str | None:
    """Resolve the actual git repo directory for a project."""
    proj_cfg = project_config.get(project_name, {})
    workspace = str(proj_cfg.get("workspace", "") or "")
    configured_repo = str(proj_cfg.get("git_repo", "") or "")
    if not workspace:
        return None

    workspace_abs = os.path.join(base_dir, workspace)
    if os.path.isdir(os.path.join(workspace_abs, ".git")):
        return workspace_abs

    if configured_repo and "/" in configured_repo:
        repo_name = configured_repo.split("/")[-1]
        candidate = os.path.join(workspace_abs, repo_name)
        if os.path.isdir(os.path.join(candidate, ".git")):
            return candidate
    return None


def resolve_git_dir_for_repo(
    *,
    project_name: str,
    repo_name: str,
    project_config: dict[str, Any],
    base_dir: str,
) -> str | None:
    """Resolve git directory for a specific configured repo."""
    proj_cfg = project_config.get(project_name, {})
    workspace = proj_cfg.get("workspace", "") if isinstance(proj_cfg, dict) else ""
    if not workspace:
        return None

    workspace_abs = os.path.join(base_dir, workspace)
    target_repo = str(repo_name or "").strip()
    target_basename = target_repo.split("/")[-1] if "/" in target_repo else target_repo

    if os.path.isdir(os.path.join(workspace_abs, ".git")):
        if os.path.basename(workspace_abs.rstrip(os.sep)) == target_basename:
            return workspace_abs

    candidate = os.path.join(workspace_abs, target_basename)
    if os.path.isdir(os.path.join(candidate, ".git")):
        return candidate

    return None


def resolve_git_dirs(
    *,
    project_name: str,
    get_repos: Callable[[str], list[str]],
    resolve_git_dir_for_repo: Callable[[str, str], str | None],
) -> dict[str, str]:
    """Return repo -> git_dir map for repos that currently have a checkout on disk."""
    resolved: dict[str, str] = {}
    try:
        repo_names = get_repos(project_name)
    except Exception:
        repo_names = []

    for repo_name in repo_names:
        repo_key = str(repo_name or "").strip()
        if not repo_key:
            continue
        git_dir = resolve_git_dir_for_repo(project_name, repo_key)
        if git_dir:
            resolved[repo_key] = git_dir
    return resolved
