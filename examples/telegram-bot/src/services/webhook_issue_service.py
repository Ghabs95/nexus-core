"""Webhook issue event handling extracted from webhook_server."""

import os
from pathlib import Path
from typing import Any


def handle_issue_opened_event(
    *,
    event: dict[str, Any],
    logger,
    policy,
    notify_lifecycle,
    emit_alert,
    project_config: dict[str, Any],
    base_dir: str,
    project_repos,
    get_repos,
    get_tasks_active_dir,
    get_inbox_dir,
) -> dict[str, Any]:
    """Handle parsed issue webhook event and create inbox task file when applicable."""
    action = event.get("action")
    issue_number = event.get("number", "")
    issue_title = event.get("title", "")
    issue_body = event.get("body", "")
    issue_author = event.get("author", "")
    issue_url = event.get("url", "")
    issue_labels = event.get("labels", [])
    repo_name = event.get("repo", "unknown")

    logger.info("📋 New issue: #%s - %s by %s", issue_number, issue_title, issue_author)

    if action == "closed":
        message = policy.build_issue_closed_message(event)
        notify_lifecycle(message)
        return {"status": "issue_closed_notified", "issue": issue_number}

    if action != "opened":
        return {"status": "ignored", "reason": f"action is {action}, not opened"}

    workflow_labels = [l for l in issue_labels if str(l).startswith("workflow:")]
    if workflow_labels:
        logger.info(
            "⏭️ Skipping self-created issue #%s (has workflow label: %s)",
            issue_number,
            workflow_labels,
        )
        return {"status": "ignored", "reason": "self-created issue (has workflow label)"}

    try:
        for key, cfg in project_config.items():
            if isinstance(cfg, dict) and repo_name in project_repos(key, cfg, get_repos):
                ws = os.path.join(base_dir, cfg.get("workspace", ""))
                active_dir = get_tasks_active_dir(ws, key)
                task_path = os.path.join(active_dir, f"issue_{issue_number}.md")
                if os.path.exists(task_path):
                    logger.info(
                        "⏭️ Skipping issue #%s — active task file already exists: %s",
                        issue_number,
                        task_path,
                    )
                    return {"status": "ignored", "reason": "task file already exists"}
                break
    except Exception as exc:
        logger.warning("Could not check for existing task file: %s", exc)

    try:
        triage_config = project_config.get("issue_triage", {})
        agent_type = triage_config.get("default_agent_type", "triage")

        label_based = triage_config.get("label_based", {})
        for label in issue_labels:
            if label in label_based:
                agent_type = label_based[label]
                logger.info("  Label '%s' → routing to agent_type: %s", label, agent_type)
                break

        per_repo = triage_config.get("per_repo", {})
        if repo_name in per_repo:
            agent_type = per_repo[repo_name]
            logger.info("  Repository '%s' → routing to agent_type: %s", repo_name, agent_type)
    except Exception as exc:
        logger.warning("⚠️ Could not load triage config, using default: %s", exc)
        triage_config = project_config.get("issue_triage", {})
        agent_type = triage_config.get("default_agent_type", "triage")

    try:
        project_workspace = None
        project_key = None
        for candidate_key, project_cfg in project_config.items():
            if not isinstance(project_cfg, dict):
                continue
            repos = project_repos(candidate_key, project_cfg, get_repos)
            if repo_name in repos:
                project_workspace = project_cfg.get("workspace")
                project_key = candidate_key
                logger.info(
                    "📌 Mapped repository '%s' → project '%s' (workspace: %s)",
                    repo_name,
                    project_key,
                    project_workspace,
                )
                break

        if not project_workspace or not project_key:
            message = (
                f"🚫 No project mapping for repository '{repo_name}'. "
                "Webhook issue task creation blocked to enforce project boundaries."
            )
            logger.error(message)
            emit_alert(message, severity="warning", source="webhook_server")
            return {
                "status": "ignored",
                "reason": "unmapped_repository",
                "repository": repo_name,
                "issue": issue_number,
            }

        workspace_abs = os.path.join(base_dir, project_workspace)
        inbox_dir = get_inbox_dir(workspace_abs, project_key)
        Path(inbox_dir).mkdir(parents=True, exist_ok=True)
        task_file = Path(inbox_dir) / f"issue_{issue_number}.md"

        task_content = f"""# Issue #{issue_number}: {issue_title}

**From:** @{issue_author}  
**URL:** {issue_url}  
**Repository:** {repo_name}  
**Agent Type:** {agent_type}
**Source:** webhook
**Issue Number:** {issue_number}

## Description

{issue_body or "_(No description provided)_"}

## Labels

{', '.join([f"`{l}`" for l in issue_labels]) if issue_labels else "_None_"}

## Status: Ready for {agent_type} agent

This issue will be routed to the {agent_type} agent as defined in the workflow.
The actual agent assignment depends on the current project's workflow configuration.
"""

        task_file.write_text(task_content)
        logger.info("✅ Created task file: %s (agent_type: %s)", task_file, agent_type)

        message = policy.build_issue_created_message(event, agent_type)
        notify_lifecycle(message)

        return {
            "status": "task_created",
            "issue": issue_number,
            "task_file": str(task_file),
            "title": issue_title,
            "agent_type": agent_type,
            "repository": repo_name,
        }
    except Exception as exc:
        logger.error(
            "❌ Error creating task file for issue #%s: %s", issue_number, exc, exc_info=True
        )
        emit_alert(
            f"Issue processing error for #{issue_number}: {str(exc)}",
            severity="error",
            source="webhook_server",
        )
        return {"status": "error", "issue": issue_number, "error": str(exc)}
