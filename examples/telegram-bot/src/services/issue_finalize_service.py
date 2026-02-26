"""Workflow finalization helpers extracted from inbox_processor."""

import asyncio
import logging

from integrations.notifications import emit_alert
from orchestration.nexus_core_helpers import get_git_platform

logger = logging.getLogger(__name__)


def verify_workflow_terminal_before_finalize(
    *,
    workflow_plugin,
    issue_num: str,
    project_name: str,
    alert_source: str = "inbox_processor",
) -> bool:
    """Return True when finalization may proceed; emit alert on non-terminal state."""
    try:
        if workflow_plugin and hasattr(workflow_plugin, "get_workflow_status"):
            status = asyncio.run(workflow_plugin.get_workflow_status(str(issue_num)))
            state = str((status or {}).get("state", "")).strip().lower()
            if state and state not in {"completed", "failed", "cancelled"}:
                logger.warning(
                    "Skipping finalize for issue #%s: workflow state is non-terminal (%s)",
                    issue_num,
                    state,
                )
                emit_alert(
                    "⚠️ Finalization blocked for "
                    f"issue #{issue_num}: workflow state is `{state}` (expected terminal).",
                    severity="warning",
                    source=alert_source,
                    issue_number=str(issue_num),
                    project_key=project_name,
                )
                return False
    except Exception as exc:
        logger.warning(
            "Could not verify workflow state before finalize for issue #%s: %s",
            issue_num,
            exc,
        )
    return True


def create_pr_from_changes(
    *,
    project_name: str,
    repo: str,
    repo_dir: str,
    issue_number: str,
    title: str,
    body: str,
    issue_repo: str | None = None,
) -> str | None:
    platform = get_git_platform(repo, project_name=project_name)
    pr_result = asyncio.run(
        platform.create_pr_from_changes(
            repo_dir=repo_dir,
            issue_number=issue_number,
            title=title,
            body=body,
            issue_repo=issue_repo,
        )
    )
    return pr_result.url if pr_result else None


def close_issue(
    *,
    project_name: str,
    repo: str,
    issue_number: str,
    comment: str | None = None,
) -> bool:
    platform = get_git_platform(repo, project_name=project_name)
    return bool(asyncio.run(platform.close_issue(issue_number, comment=comment)))


def find_existing_pr(
    *,
    project_name: str,
    repo: str,
    issue_number: str,
) -> str | None:
    platform = get_git_platform(repo, project_name=project_name)
    linked = asyncio.run(platform.search_linked_prs(str(issue_number)))
    if not linked:
        return None
    open_pr = next((pr for pr in linked if str(pr.state).lower() == "open"), None)
    selected = open_pr or linked[0]
    return selected.url


def cleanup_worktree(
    *,
    repo_dir: str,
    issue_number: str,
) -> bool:
    from nexus.core.workspace import WorkspaceManager

    return bool(
        WorkspaceManager.cleanup_worktree(
            base_repo_path=repo_dir,
            issue_number=str(issue_number),
        )
    )
