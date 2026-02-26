"""Webhook pull request event handling extracted from webhook_server."""

import re
from typing import Any


def handle_pull_request_event(
    *,
    event: dict[str, Any],
    logger,
    policy,
    notify_lifecycle,
    effective_review_mode,
    launch_next_agent,
) -> dict[str, Any]:
    """Handle parsed pull_request event."""
    action = event.get("action")
    pr_number = event.get("number")
    pr_title = event.get("title", "")
    pr_author = event.get("author", "")
    repo_name = event.get("repo", "unknown")
    merged = bool(event.get("merged"))

    logger.info("🔀 Pull request #%s: %s by %s", pr_number, action, pr_author)

    if action == "opened":
        message = policy.build_pr_created_message(event)
        notify_lifecycle(message)

        issue_match = re.search(r"#(\d+)", str(pr_title or ""))
        if issue_match:
            referenced_issue = issue_match.group(1)
            logger.info(
                "PR #%s references issue #%s — auto-queuing reviewer",
                pr_number,
                referenced_issue,
            )
            try:
                launch_next_agent(referenced_issue, "reviewer", trigger_source="pr_opened")
            except Exception as exc:
                logger.warning(
                    "Failed to auto-queue reviewer for issue #%s: %s",
                    referenced_issue,
                    exc,
                )

        return {"status": "pr_opened_notified", "pr": pr_number, "action": action}

    if action == "closed" and merged:
        review_mode = effective_review_mode(repo_name)
        should_notify = policy.should_notify_pr_merged(review_mode)
        if should_notify:
            message = policy.build_pr_merged_message(event, review_mode)
            notify_lifecycle(message)
            return {
                "status": "pr_merged_notified",
                "pr": pr_number,
                "action": action,
                "review_mode": review_mode,
            }

        logger.info(
            "Skipping PR merged notification for #%s due to review mode '%s'",
            pr_number,
            review_mode,
        )
        return {
            "status": "pr_merged_skipped_manual_review",
            "pr": pr_number,
            "action": action,
            "review_mode": review_mode,
        }

    return {"status": "logged", "pr": pr_number, "action": action}
