"""Webhook issue comment event handling extracted from webhook_server."""

import re
from typing import Any


def handle_issue_comment_event(
    *,
    event: dict[str, Any],
    logger,
    policy,
    processed_events: set[str],
    launch_next_agent,
    check_and_notify_pr,
) -> dict[str, Any]:
    """Handle parsed issue_comment event."""
    action = event.get("action")
    comment_id = event.get("comment_id")
    comment_body = event.get("comment_body", "")
    issue_number = event.get("issue_number", "")
    comment_author = event.get("comment_author", "")
    issue = event.get("issue", {})

    logger.info("📝 Issue comment: #%s by %s (action: %s)", issue_number, comment_author, action)

    if action != "created":
        return {"status": "ignored", "reason": f"action is {action}, not created"}

    if comment_author != "copilot":
        return {"status": "ignored", "reason": "not from copilot"}

    event_key = f"comment_{comment_id}"
    if event_key in processed_events:
        logger.info("⏭️ Already processed comment %s", comment_id)
        return {"status": "duplicate"}

    completion_markers = [
        r"workflow\s+complete",
        r"ready\s+for\s+review",
        r"ready\s+to\s+merge",
        r"implementation\s+complete",
        r"all\s+steps\s+completed",
    ]
    is_completion = any(re.search(pattern, str(comment_body or ""), re.IGNORECASE) for pattern in completion_markers)
    next_agent_match = re.search(r"@(\w+)", str(comment_body or ""))
    next_agent = next_agent_match.group(1) if next_agent_match else None

    if is_completion and not next_agent:
        logger.info("✅ Workflow completion detected for issue #%s", issue_number)
        project = policy.determine_project_from_issue(issue)
        check_and_notify_pr(issue_number, project)
        processed_events.add(event_key)
        return {"status": "workflow_completed", "issue": issue_number}

    if next_agent:
        logger.info("🔗 Chaining to @%s for issue #%s", next_agent, issue_number)
        try:
            pid, _ = launch_next_agent(
                issue_number=issue_number,
                next_agent=next_agent,
                trigger_source="webhook",
            )
            if pid:
                processed_events.add(event_key)
                return {"status": "agent_launched", "issue": issue_number, "next_agent": next_agent}
            return {"status": "launch_failed", "issue": issue_number, "next_agent": next_agent}
        except Exception as exc:
            logger.error("❌ Failed to launch next agent: %s", exc)
            return {"status": "error", "message": str(exc)}

    return {"status": "no_action"}
