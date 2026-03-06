"""Workflow-start git sync helpers (worktree-safe fetch)."""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable
from typing import Any


def _is_network_or_auth_failure(stderr: str, stdout: str = "") -> bool:
    text = f"{stderr or ''}\n{stdout or ''}".lower()
    markers = (
        "could not resolve host",
        "failed to connect",
        "connection timed out",
        "timed out",
        "network is unreachable",
        "connection reset",
        "proxy error",
        "unable to access",
        "authentication failed",
        "permission denied",
        "could not read from remote repository",
        "repository not found",
        "access denied",
        "http 401",
        "http 403",
        "invalid username or password",
        "could not read username",
        "unable to update url base",
        "could not resolve proxy",
    )
    return any(marker in text for marker in markers)


def _wait_for_block_decision(
    *,
    issue_number: str,
    project_name: str,
    timeout_seconds: int,
    should_block_launch: Callable[[str, str], bool] | None,
    sleep_fn: Callable[[float], None],
) -> bool:
    if not callable(should_block_launch):
        return False

    deadline = time.time() + max(1, int(timeout_seconds))
    while time.time() < deadline:
        try:
            if should_block_launch(str(issue_number), str(project_name)):
                return True
        except Exception:
            return False
        sleep_fn(1.0)
    return False


def _project_repo_slugs(
    *,
    project_cfg: dict[str, Any],
    project_name: str,
    get_repos: Callable[[str], list[str]],
) -> list[str]:
    repos: list[str] = []
    try:
        repos = [str(item).strip() for item in get_repos(project_name)]
        repos = [item for item in repos if item]
    except Exception:
        repos = []

    if repos:
        return repos

    single_repo = str(project_cfg.get("git_repo") or "").strip()
    if single_repo:
        repos.append(single_repo)
    repo_list = project_cfg.get("git_repos")
    if isinstance(repo_list, list):
        for repo_name in repo_list:
            value = str(repo_name or "").strip()
            if value and value not in repos:
                repos.append(value)
    return repos


def _build_clone_url(repo_slug: str, project_cfg: dict[str, Any]) -> str:
    repo = str(repo_slug or "").strip().strip("/")
    if repo.startswith(("https://", "http://", "git@")):
        if repo.endswith(".git"):
            return repo
        return f"{repo}.git"

    git_platform = str(project_cfg.get("git_platform", "github") or "github").strip().lower()
    if git_platform == "gitlab":
        base_url = str(project_cfg.get("gitlab_base_url") or "").strip() or "https://gitlab.com"
    else:
        base_url = "https://github.com"
    return f"{base_url.rstrip('/')}/{repo}.git"


def _run_git_command_with_retries(
    *,
    cmd: list[str],
    cwd: str | None,
    retries: int,
    backoff_seconds: int,
    logger: Any | None,
    log_context: str,
    sleep_fn: Callable[[float], None],
) -> tuple[bool, str, bool]:
    max_attempts = max(1, retries + 1)
    for attempt in range(1, max_attempts + 1):
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode == 0:
            return True, "", False

        stderr = str(result.stderr or "").strip()
        stdout = str(result.stdout or "").strip()
        network_or_auth = _is_network_or_auth_failure(stderr, stdout)
        if network_or_auth and attempt < max_attempts:
            if logger:
                logger.warning(
                    "Workflow-start git sync retry %s/%s for %s: %s",
                    attempt,
                    max_attempts,
                    log_context,
                    stderr or stdout or "unknown error",
                )
            sleep_fn(float(max(1, backoff_seconds)))
            continue

        error_msg = stderr or stdout or f"git command failed (code={result.returncode})"
        return False, error_msg, network_or_auth

    return False, "git command failed with unknown error", False


def _alert_and_wait_for_decision(
    *,
    issue_number: str,
    project_name: str,
    repo_slug: str,
    branch: str,
    error_msg: str,
    decision_timeout_seconds: int,
    should_block_launch: Callable[[str, str], bool] | None,
    sleep_fn: Callable[[float], None],
    emit_alert: Callable[..., Any] | None,
    operation: str,
) -> bool:
    if callable(emit_alert):
        emit_alert(
            (
                f"⚠️ Workflow-start git {operation} failed after retries.\n"
                f"Issue: #{issue_number}\n"
                f"Project: {project_name}\n"
                f"Repo: {repo_slug}\n"
                f"Branch: {branch}\n"
                f"Error: {error_msg}\n\n"
                "Choose whether to block launch now. "
                "If no action is taken, launch continues automatically."
            ),
            severity="warning",
            source="workflow_start_git_sync",
            issue_number=str(issue_number),
            project_key=str(project_name),
            actions=[
                {
                    "label": "🛑 Block Launch",
                    "callback_data": f"stop_{issue_number}|{project_name}",
                }
            ],
        )
    return _wait_for_block_decision(
        issue_number=str(issue_number),
        project_name=str(project_name),
        timeout_seconds=max(1, decision_timeout_seconds),
        should_block_launch=should_block_launch,
        sleep_fn=sleep_fn,
    )


def sync_project_repos_on_workflow_start(
    *,
    issue_number: str,
    project_name: str,
    project_cfg: dict[str, Any],
    resolve_git_dirs: Callable[[str], dict[str, str]],
    resolve_git_dir: Callable[[str], str | None],
    resolve_git_dir_for_repo: Callable[[str, str], str | None] | None = None,
    ensure_workspace_dir: Callable[[str], str | None] | None = None,
    get_repos: Callable[[str], list[str]],
    get_repo_branch: Callable[[str, str], str],
    emit_alert: Callable[..., Any] | None = None,
    logger: Any | None = None,
    should_block_launch: Callable[[str, str], bool] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Sync configured project repos with worktree-safe fetch before initial launch."""
    cfg = project_cfg if isinstance(project_cfg, dict) else {}
    git_sync = cfg.get("git_sync") if isinstance(cfg.get("git_sync"), dict) else {}

    enabled = bool(git_sync.get("on_workflow_start", False))
    if not enabled:
        return {"enabled": False, "skipped": True, "reason": "disabled"}

    retries = int(git_sync.get("network_auth_retries", 3) or 3)
    backoff_seconds = int(git_sync.get("retry_backoff_seconds", 5) or 5)
    decision_timeout_seconds = int(git_sync.get("decision_timeout_seconds", 120) or 120)
    bootstrap_missing_workspace = bool(git_sync.get("bootstrap_missing_workspace", False))
    bootstrap_missing_repos = bool(git_sync.get("bootstrap_missing_repos", False))

    if bootstrap_missing_workspace and callable(ensure_workspace_dir):
        try:
            ensure_workspace_dir(project_name)
        except Exception as exc:
            if logger:
                logger.warning(
                    "Workflow-start git sync bootstrap could not ensure workspace for %s: %s",
                    project_name,
                    exc,
                )

    configured_repos = _project_repo_slugs(
        project_cfg=cfg,
        project_name=project_name,
        get_repos=get_repos,
    )
    resolved_dirs: dict[str, str] = {}
    try:
        resolved = resolve_git_dirs(project_name)
        if isinstance(resolved, dict):
            resolved_dirs.update(
                {
                    str(repo_slug): str(path)
                    for repo_slug, path in resolved.items()
                    if str(repo_slug).strip() and str(path).strip()
                }
            )
    except Exception:
        resolved_dirs = {}

    if not resolved_dirs:
        fallback_dir = resolve_git_dir(project_name)
        if fallback_dir:
            if configured_repos:
                for repo_name in configured_repos:
                    key = str(repo_name).strip()
                    if key:
                        resolved_dirs[key] = str(fallback_dir)
            else:
                primary_repo = str(cfg.get("git_repo") or "").strip()
                if primary_repo:
                    resolved_dirs[primary_repo] = str(fallback_dir)

    synced: list[dict[str, str]] = []
    bootstrapped: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []
    blocked = False

    if bootstrap_missing_repos and callable(resolve_git_dir_for_repo):
        for repo_slug in configured_repos:
            if blocked or repo_slug in resolved_dirs:
                continue
            try:
                repo_dir = resolve_git_dir_for_repo(project_name, repo_slug)
            except Exception as exc:
                if logger:
                    logger.warning(
                        "Workflow-start git bootstrap could not resolve path for %s: %s",
                        repo_slug,
                        exc,
                    )
                continue
            if not repo_dir:
                continue

            repo_dir = str(repo_dir).strip()
            if not repo_dir:
                continue
            parent = os.path.dirname(repo_dir.rstrip(os.sep))
            if parent:
                try:
                    os.makedirs(parent, exist_ok=True)
                except Exception as exc:
                    failures.append(
                        {
                            "repo": repo_slug,
                            "branch": str(get_repo_branch(project_name, repo_slug) or "main").strip()
                            or "main",
                            "dir": repo_dir,
                            "error": f"could not prepare parent directory: {exc}",
                            "kind": "other",
                        }
                    )
                    continue

            branch = str(get_repo_branch(project_name, repo_slug) or "main").strip() or "main"
            clone_url = _build_clone_url(repo_slug, cfg)
            success, error_msg, network_or_auth = _run_git_command_with_retries(
                cmd=["git", "clone", "--branch", branch, "--single-branch", clone_url, repo_dir],
                cwd=None,
                retries=retries,
                backoff_seconds=backoff_seconds,
                logger=logger,
                log_context=f"{repo_slug} bootstrap clone on {branch}",
                sleep_fn=sleep_fn,
            )
            if success:
                resolved_dirs[repo_slug] = repo_dir
                bootstrapped.append(
                    {"repo": repo_slug, "branch": branch, "dir": repo_dir, "clone_url": clone_url}
                )
                continue

            failures.append(
                {
                    "repo": repo_slug,
                    "branch": branch,
                    "dir": repo_dir,
                    "error": error_msg,
                    "kind": "network_auth" if network_or_auth else "other",
                }
            )
            if not network_or_auth:
                if logger:
                    logger.warning(
                        "Workflow-start git bootstrap warning for %s on %s: %s",
                        repo_slug,
                        branch,
                        error_msg,
                    )
                continue

            if logger:
                logger.warning(
                    "Workflow-start git bootstrap exhausted retries for %s on %s: %s",
                    repo_slug,
                    branch,
                    error_msg,
                )
            blocked = _alert_and_wait_for_decision(
                issue_number=str(issue_number),
                project_name=str(project_name),
                repo_slug=repo_slug,
                branch=branch,
                error_msg=error_msg,
                decision_timeout_seconds=decision_timeout_seconds,
                should_block_launch=should_block_launch,
                sleep_fn=sleep_fn,
                emit_alert=emit_alert,
                operation="bootstrap",
            )

    if not resolved_dirs:
        return {
            "enabled": True,
            "skipped": True,
            "reason": "no_git_dirs",
            "blocked": blocked,
            "synced": synced,
            "bootstrapped": bootstrapped,
            "failures": failures,
        }

    for repo_slug, repo_dir in resolved_dirs.items():
        if blocked:
            break
        branch = str(get_repo_branch(project_name, repo_slug) or "main").strip() or "main"
        success, error_msg, network_or_auth = _run_git_command_with_retries(
            cmd=[
                "git",
                "fetch",
                "--prune",
                "origin",
                f"{branch}:refs/remotes/origin/{branch}",
            ],
            cwd=repo_dir,
            retries=retries,
            backoff_seconds=backoff_seconds,
            logger=logger,
            log_context=f"{repo_slug} fetch on {branch}",
            sleep_fn=sleep_fn,
        )
        if success:
            synced.append({"repo": repo_slug, "branch": branch, "dir": repo_dir})
            continue

        failures.append(
            {
                "repo": repo_slug,
                "branch": branch,
                "dir": repo_dir,
                "error": error_msg,
                "kind": "network_auth" if network_or_auth else "other",
            }
        )
        if not network_or_auth:
            if logger:
                logger.warning(
                    "Workflow-start git sync warning for %s on %s: %s",
                    repo_slug,
                    branch,
                    error_msg,
                )
            continue

        if logger:
            logger.warning(
                "Workflow-start git sync exhausted retries for %s on %s: %s",
                repo_slug,
                branch,
                error_msg,
            )
        blocked = _alert_and_wait_for_decision(
            issue_number=str(issue_number),
            project_name=str(project_name),
            repo_slug=repo_slug,
            branch=branch,
            error_msg=error_msg,
            decision_timeout_seconds=decision_timeout_seconds,
            should_block_launch=should_block_launch,
            sleep_fn=sleep_fn,
            emit_alert=emit_alert,
            operation="sync",
        )

    return {
        "enabled": True,
        "skipped": False,
        "blocked": blocked,
        "synced": synced,
        "bootstrapped": bootstrapped,
        "failures": failures,
    }
