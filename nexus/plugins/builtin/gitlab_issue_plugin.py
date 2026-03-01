"""Built-in plugin: GitLab issue creation via glab CLI."""

import json
import logging
import subprocess
import time
from typing import Any

logger = logging.getLogger(__name__)


class GitLabIssueCLIPlugin:
    """Create GitLab issues via glab CLI with label fallback behavior."""

    def __init__(self, config: dict[str, Any]):
        self.repo = config.get("repo", "")
        self.max_attempts = int(config.get("max_attempts", 3))
        self.timeout = int(config.get("timeout", 30))
        self.base_delay = float(config.get("base_delay", 1.0))

    def create_issue(self, title: str, body: str, labels: list[str] | None = None) -> str | None:
        """Create issue and return URL, or None when all attempts fail."""
        labels = labels or []
        cmd = [
            "glab",
            "issue",
            "create",
            "--repo",
            self.repo,
            "--title",
            title,
            "--description",
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
            "glab",
            "issue",
            "create",
            "--repo",
            self.repo,
            "--title",
            title,
            "--description",
            body,
        ]
        try:
            fallback_result = self._run_with_retry(fallback_cmd, max_attempts=1)
            return fallback_result.stdout.strip()
        except Exception as exc:
            logger.error("Failed to create issue without labels: %s", exc)
            return None

    def add_comment(self, issue_number: str, body: str) -> bool:
        """Add a comment (note) to an issue and return success status."""
        cmd = [
            "glab",
            "issue",
            "note",
            str(issue_number),
            "--repo",
            self.repo,
            "-m",
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
            "glab",
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
            if "already exists" in stderr or "taken" in stderr:
                return True
            return False
        except Exception as exc:
            logger.warning("Failed to ensure label %s: %s", label, exc)
            return False

    def add_label(self, issue_number: str, label: str) -> bool:
        """Add a label to an issue."""
        cmd = [
            "glab",
            "issue",
            "update",
            str(issue_number),
            "--repo",
            self.repo,
            "--label",
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
            "glab",
            "issue",
            "update",
            str(issue_number),
            "--repo",
            self.repo,
            "--assignee",
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
        # Note: glab doesn't natively support arbitrary field selection via --json like gh does,
        # but glab api does. We'll use glab api to fetch the issue directly.
        # Alternatively, 'glab api projects/:path/issues/:id'
        cmd = [
            "glab",
            "api",
            f"projects/{self.repo.replace('/', '%2F')}/issues/{issue_number}",
        ]
        try:
            result = self._run_with_retry(cmd, max_attempts=self.max_attempts)
            data = json.loads(result.stdout or "{}")
            # Filter fields if requested
            if fields:
                filtered_list: dict[str, Any] = {
                    str(k): v for k, v in data.items() if str(k) in fields
                }
                filtered = filtered_list
                # Fallbacks for common fields mapped between gh and gitlab
                if "number" in fields and "iid" in data:
                    filtered["number"] = data["iid"]
                if "body" in fields and "description" in data:
                    filtered["body"] = data["description"]
                return filtered
            return data
        except Exception as exc:
            message = str(exc)
            if (
                "404" in message
                or "not found" in message.lower()
                or "returned non-zero exit status 1" in message
            ):
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
            "glab",
            "issue",
            "update",
            str(issue_number),
            "--repo",
            self.repo,
            "--description",
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
            "glab",
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
        state: str = "opened",
        limit: int = 10,
        fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """List issues from the configured repository using glab api."""
        state_map = {"open": "opened", "closed": "closed", "all": "all"}
        glab_state = state_map.get(state, "opened")

        cmd = [
            "glab",
            "api",
            f"projects/{self.repo.replace('/', '%2F')}/issues?state={glab_state}&per_page={limit}",
        ]
        try:
            result = self._run_with_retry(cmd, max_attempts=self.max_attempts)
            data = json.loads(result.stdout or "[]")
            if not isinstance(data, list):
                return []

            # Map standard fields back
            mapped_data = []
            for item in data:
                mapped_item = item.copy()
                mapped_item["number"] = item.get("iid")
                mapped_item["body"] = item.get("description")

                if fields:
                    filtered_item: dict[str, Any] = {
                        str(k): v for k, v in mapped_item.items() if str(k) in fields
                    }
                    # Ensure minimal keys exist
                    if "number" in fields and "number" not in filtered_item:
                        filtered_item["number"] = mapped_item.get("number")
                    mapped_data.append(filtered_item)
                else:
                    mapped_data.append(mapped_item)
            return mapped_data

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
    """Register built-in GitLab issue plugin in a PluginRegistry."""
    from nexus.plugins import PluginKind  # type: ignore[import-untyped]

    registry.register_factory(
        kind=PluginKind.GIT_PLATFORM,
        name="gitlab-issue-cli",
        version="0.1.0",
        factory=lambda config: GitLabIssueCLIPlugin(config),
        description="GitLab issue creation via glab CLI with no-label fallback",
    )
