"""Built-in plugin: GitHub issue creation via gh CLI."""

import json
import logging
import subprocess
import time
from typing import Any

logger = logging.getLogger(__name__)


class GitHubIssueCLIPlugin:
    """Create GitHub issues via gh CLI with label fallback behavior."""

    def __init__(self, config: dict[str, Any]):
        self.repo = config.get("repo", "")
        self.max_attempts = int(config.get("max_attempts", 3))
        self.timeout = int(config.get("timeout", 30))
        self.base_delay = float(config.get("base_delay", 1.0))

    def create_issue(self, title: str, body: str, labels: list[str] | None = None) -> str | None:
        """Create issue and return URL, or None when all attempts fail."""
        labels = labels or []
        cmd = [
            "gh",
            "issue",
            "create",
            "--repo",
            self.repo,
            "--title",
            title,
            "--body",
            body,
        ]
        for label in labels:
            cmd.extend(["--label", label])

        try:
            result = self._run_with_retry(cmd, max_attempts=self.max_attempts)
            return result.stdout.strip()
        except Exception as exc:
            logger.warning(
                "Issue creation with labels failed after retries: %s. Retrying once without labels.",
                exc,
            )

        fallback_cmd = [
            "gh",
            "issue",
            "create",
            "--repo",
            self.repo,
            "--title",
            title,
            "--body",
            body,
        ]
        try:
            fallback_result = self._run_with_retry(fallback_cmd, max_attempts=1)
            return fallback_result.stdout.strip()
        except Exception as exc:
            logger.error("Failed to create issue without labels: %s", exc)
            return None

    def add_comment(self, issue_number: str, body: str) -> bool:
        """Add a comment to an issue and return success status."""
        cmd = [
            "gh",
            "issue",
            "comment",
            str(issue_number),
            "--repo",
            self.repo,
            "--body",
            body,
        ]
        try:
            self._run_with_retry(cmd, max_attempts=self.max_attempts)
            return True
        except Exception as exc:
            logger.error("Failed to add issue comment: %s", exc)
            return False

    def ensure_label(self, label: str, color: str, description: str) -> bool:
        """Create label if needed; returns True when creation succeeds."""
        cmd = [
            "gh",
            "label",
            "create",
            label,
            "--repo",
            self.repo,
            "--color",
            color,
            "--description",
            description,
        ]
        try:
            result = subprocess.run(
                cmd,
                check=False,
                timeout=self.timeout,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return True
            stderr = (result.stderr or "").lower()
            if "already exists" in stderr:
                return True
            return False
        except Exception as exc:
            logger.warning("Failed to ensure label %s: %s", label, exc)
            return False

    def add_label(self, issue_number: str, label: str) -> bool:
        """Add a label to an issue."""
        cmd = [
            "gh",
            "issue",
            "edit",
            str(issue_number),
            "--repo",
            self.repo,
            "--add-label",
            label,
        ]
        try:
            self._run_with_retry(cmd, max_attempts=self.max_attempts)
            return True
        except Exception as exc:
            logger.error("Failed to add label to issue %s: %s", issue_number, exc)
            return False

    def add_assignee(self, issue_number: str, assignee: str) -> bool:
        """Assign an issue to a user/login."""
        cmd = [
            "gh",
            "issue",
            "edit",
            str(issue_number),
            "--repo",
            self.repo,
            "--add-assignee",
            assignee,
        ]
        try:
            self._run_with_retry(cmd, max_attempts=self.max_attempts)
            return True
        except Exception as exc:
            logger.error("Failed to assign issue %s: %s", issue_number, exc)
            return False

    def get_issue(self, issue_number: str, fields: list[str]) -> dict[str, Any] | None:
        """Fetch issue JSON for selected fields."""
        cmd = [
            "gh",
            "issue",
            "view",
            str(issue_number),
            "--repo",
            self.repo,
            "--json",
            ",".join(fields),
        ]
        try:
            result = self._run_with_retry(cmd, max_attempts=self.max_attempts)
            return json.loads(result.stdout or "{}")
        except Exception as exc:
            message = str(exc)
            not_found_markers = [
                "returned non-zero exit status 1",
                "Could not resolve to an issue",
                "not found",
            ]
            if any(marker in message for marker in not_found_markers):
                logger.debug(
                    "Issue %s not found in repo %s while probing: %s",
                    issue_number,
                    self.repo,
                    message,
                )
            else:
                logger.error("Failed to read issue %s: %s", issue_number, exc)
            return None

    def update_issue_body(self, issue_number: str, body: str) -> bool:
        """Update issue body text."""
        cmd = [
            "gh",
            "issue",
            "edit",
            str(issue_number),
            "--repo",
            self.repo,
            "--body",
            body,
        ]
        try:
            self._run_with_retry(cmd, max_attempts=self.max_attempts)
            return True
        except Exception as exc:
            logger.error("Failed to update issue %s body: %s", issue_number, exc)
            return False

    def close_issue(self, issue_number: str) -> bool:
        """Close an issue."""
        cmd = [
            "gh",
            "issue",
            "close",
            str(issue_number),
            "--repo",
            self.repo,
        ]
        try:
            self._run_with_retry(cmd, max_attempts=self.max_attempts)
            return True
        except Exception as exc:
            logger.error("Failed to close issue %s: %s", issue_number, exc)
            return False

    def list_issues(
        self,
        state: str = "open",
        limit: int = 10,
        fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """List issues from the configured repository."""
        fields = fields or ["number", "title", "state"]
        cmd = [
            "gh",
            "issue",
            "list",
            "--repo",
            self.repo,
            "--state",
            state,
            "--limit",
            str(limit),
            "--json",
            ",".join(fields),
        ]
        try:
            result = self._run_with_retry(cmd, max_attempts=self.max_attempts)
            data = json.loads(result.stdout or "[]")
            return data if isinstance(data, list) else []
        except Exception as exc:
            logger.error("Failed to list issues for %s: %s", self.repo, exc)
            return []

    def _run_with_retry(
        self,
        cmd: list[str],
        max_attempts: int,
    ) -> subprocess.CompletedProcess:
        """Run command with simple exponential backoff."""
        attempt = 1
        while attempt <= max_attempts:
            try:
                return subprocess.run(
                    cmd,
                    check=True,
                    timeout=self.timeout,
                    capture_output=True,
                    text=True,
                )
            except (subprocess.CalledProcessError, FileNotFoundError) as exc:
                if attempt == max_attempts:
                    raise RuntimeError(str(exc)) from exc
                delay = self.base_delay * (2 ** (attempt - 1))
                time.sleep(delay)
                attempt += 1

        raise RuntimeError("Unexpected retry exhaustion")


def register_plugins(registry) -> None:
    """Register built-in GitHub issue plugin in a PluginRegistry."""
    from nexus.plugins import PluginKind

    registry.register_factory(
        kind=PluginKind.GIT_PLATFORM,
        name="github-issue-cli",
        version="0.1.0",
        factory=lambda config: GitHubIssueCLIPlugin(config),
        description="GitHub issue creation via gh CLI with no-label fallback",
    )
