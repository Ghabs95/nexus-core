"""Built-in plugin: GitHub webhook policy.

Normalizes GitHub webhook payloads into stable event dictionaries and provides
small policy helpers for webhook-side decisions.
"""

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

    def should_notify_pr_merged(self, merge_policy: str) -> bool:
        """Return True when PR merge notifications should be emitted."""
        return str(merge_policy or "always") != "always"


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
