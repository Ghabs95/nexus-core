"""Built-in plugin: GitHub webhook policy.

Normalizes GitHub webhook payloads into stable event dictionaries and provides
small policy helpers for webhook-side decisions.
"""

import hashlib
import hmac
from typing import Any, Optional


class GithubWebhookPolicyPlugin:
    """Policy helper for GitHub webhook event normalization."""

    def __init__(self, config: Optional[dict[str, Any]] = None):
        self.config = config or {}

    def parse_issue_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return normalized issue event data from a GitHub issues payload."""
        issue = payload.get("issue", {}) or {}
        repository = payload.get("repository", {}) or {}

        labels = issue.get("labels", []) or []
        label_names = [label.get("name") for label in labels if isinstance(label, dict) and label.get("name")]

        return {
            "action": payload.get("action"),
            "number": str(issue.get("number", "")),
            "title": issue.get("title", ""),
            "body": issue.get("body", ""),
            "url": issue.get("html_url", ""),
            "author": (issue.get("user", {}) or {}).get("login", ""),
            "closed_by": (payload.get("sender", {}) or {}).get("login", "unknown"),
            "labels": label_names,
            "repo": repository.get("full_name", "unknown"),
        }

    def parse_pull_request_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return normalized pull request event data from a GitHub PR payload."""
        pr = payload.get("pull_request", {}) or {}
        repository = payload.get("repository", {}) or {}

        return {
            "action": payload.get("action"),
            "number": pr.get("number"),
            "title": pr.get("title", ""),
            "url": pr.get("html_url", ""),
            "author": (pr.get("user", {}) or {}).get("login", ""),
            "merged": bool(pr.get("merged")),
            "merged_by": (pr.get("merged_by", {}) or {}).get("login", "unknown"),
            "repo": repository.get("full_name", "unknown"),
        }

    def parse_issue_comment_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return normalized issue_comment event data."""
        comment = payload.get("comment", {}) or {}
        issue = payload.get("issue", {}) or {}

        return {
            "action": payload.get("action"),
            "comment_id": comment.get("id"),
            "comment_body": comment.get("body", ""),
            "comment_author": (comment.get("user", {}) or {}).get("login", ""),
            "issue_number": str(issue.get("number", "")),
            "issue": issue,
        }

    def parse_pull_request_review_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return normalized pull_request_review event data."""
        review = payload.get("review", {}) or {}
        pr = payload.get("pull_request", {}) or {}

        return {
            "action": payload.get("action"),
            "pr_number": pr.get("number"),
            "review_state": review.get("state"),
            "reviewer": (review.get("user", {}) or {}).get("login", ""),
        }

    def dispatch_event(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Dispatch raw GitHub event type to normalized route + event payload."""
        if event_type == "issues":
            return {"route": "issues", "event": self.parse_issue_event(payload)}
        if event_type == "issue_comment":
            return {"route": "issue_comment", "event": self.parse_issue_comment_event(payload)}
        if event_type == "pull_request":
            return {"route": "pull_request", "event": self.parse_pull_request_event(payload)}
        if event_type == "pull_request_review":
            return {
                "route": "pull_request_review",
                "event": self.parse_pull_request_review_event(payload),
            }
        if event_type == "ping":
            return {"route": "ping", "event": {}}
        return {"route": "unhandled", "event": {"event_type": event_type}}

    def should_notify_pr_merged(self, merge_policy: str) -> bool:
        """Return True when PR merge notifications should be emitted."""
        return str(merge_policy or "always") != "always"

    def verify_signature(
        self,
        payload_body: bytes,
        signature_header: Optional[str],
        secret: Optional[str],
    ) -> bool:
        """Verify GitHub webhook signature header."""
        if not secret:
            return True

        if not signature_header or "=" not in signature_header:
            return False

        hash_algorithm, github_signature = signature_header.split("=", 1)
        if hash_algorithm != "sha256":
            return False

        mac = hmac.new(
            str(secret).encode("utf-8"),
            msg=payload_body,
            digestmod=hashlib.sha256,
        )
        expected_signature = mac.hexdigest()
        return hmac.compare_digest(expected_signature, github_signature)

    def resolve_project_key(
        self,
        repo_name: str,
        project_config: dict[str, Any],
        default_project: str = "nexus",
    ) -> str:
        """Resolve project key from repository full name."""
        for project_key, project_cfg in (project_config or {}).items():
            if isinstance(project_cfg, dict) and project_cfg.get("github_repo") == repo_name:
                return project_key
        return default_project

    def resolve_merge_policy(
        self,
        repo_name: str,
        project_config: dict[str, Any],
        default_policy: str = "always",
    ) -> str:
        """Resolve effective merge policy for a repository."""
        project_key = self.resolve_project_key(repo_name, project_config)
        project_cfg = (project_config or {}).get(project_key, {})
        if isinstance(project_cfg, dict) and project_cfg.get("require_human_merge_approval"):
            return str(project_cfg.get("require_human_merge_approval"))
        return str((project_config or {}).get("require_human_merge_approval", default_policy))

    def build_issue_closed_message(self, event: dict[str, Any]) -> str:
        """Build issue-closed lifecycle notification message."""
        return (
            "🔒 **Issue Closed**\n\n"
            f"Issue: #{event.get('number', '')}\n"
            f"Title: {event.get('title', '')}\n"
            f"Repository: {event.get('repo', 'unknown')}\n"
            f"Closed by: @{event.get('closed_by', 'unknown')}\n\n"
            f"🔗 {event.get('url', '')}"
        )

    def build_issue_created_message(self, event: dict[str, Any], agent_type: str) -> str:
        """Build issue-created lifecycle notification message."""
        return (
            "📥 **Issue Created**\n\n"
            f"Issue: #{event.get('number', '')}\n"
            f"Title: {event.get('title', '')}\n"
            f"Repository: {event.get('repo', 'unknown')}\n"
            f"Author: @{event.get('author', '')}\n"
            f"Routed to: `{agent_type}`\n\n"
            f"🔗 {event.get('url', '')}"
        )

    def build_pr_created_message(self, event: dict[str, Any]) -> str:
        """Build PR-created lifecycle notification message."""
        return (
            "🔀 **PR Created**\n\n"
            f"PR: #{event.get('number', '')}\n"
            f"Title: {event.get('title', '')}\n"
            f"Repository: {event.get('repo', 'unknown')}\n"
            f"Author: @{event.get('author', '')}\n\n"
            f"🔗 {event.get('url', '')}"
        )

    def build_pr_merged_message(self, event: dict[str, Any], merge_policy: str) -> str:
        """Build PR-merged lifecycle notification message."""
        return (
            "✅ **PR Merged**\n\n"
            f"PR: #{event.get('number', '')}\n"
            f"Title: {event.get('title', '')}\n"
            f"Repository: {event.get('repo', 'unknown')}\n"
            f"Merged by: @{event.get('merged_by', 'unknown')}\n"
            f"Policy: `{merge_policy}`\n\n"
            f"🔗 {event.get('url', '')}"
        )

    def determine_project_from_issue(self, issue: dict[str, Any]) -> str:
        """Best-effort project key detection from issue labels/body."""
        labels = issue.get("labels", []) or []
        for label in labels:
            label_name = ""
            if isinstance(label, dict):
                label_name = str(label.get("name", "")).lower()
            elif isinstance(label, str):
                label_name = label.lower()

            if "casit" in label_name or "caseitalia" in label_name:
                return "casit"
            if "wlbl" in label_name or "wallible" in label_name:
                return "wlbl"
            if "bm" in label_name or "biome" in label_name:
                return "bm"

        body = str(issue.get("body", "")).lower()
        if "caseitalia" in body or "case-italia" in body:
            return "casit"
        if "wallible" in body or "wlbl" in body:
            return "wlbl"
        if "biome" in body or "biomejs" in body:
            return "bm"

        return "casit"


def register_plugins(registry) -> None:
    """Register built-in GitHub webhook policy plugin."""
    from nexus.plugins import PluginKind

    registry.register_factory(
        kind=PluginKind.INPUT_ADAPTER,
        name="github-webhook-policy",
        version="0.1.0",
        factory=lambda config: GithubWebhookPolicyPlugin(config),
        description="GitHub webhook payload normalization and policy helpers",
    )
