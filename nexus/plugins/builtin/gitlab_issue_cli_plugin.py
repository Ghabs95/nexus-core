"""Built-in plugin: GitLab issue creation via glab CLI."""

import json
import logging
import os
import subprocess
import time
from typing import Any

from nexus.adapters.git.utils import build_issue_url

logger = logging.getLogger(__name__)


class GitLabIssueCLIPlugin:
    """Create GitLab issues via glab CLI with label fallback behavior."""

    def __init__(self, config: dict[str, Any]):
        self.repo = config.get("repo", "")
        self.max_attempts = int(config.get("max_attempts", 3))
        self.timeout = int(config.get("timeout", 30))
        self.base_delay = float(config.get("base_delay", 1.0))
        token = str(config.get("token_override") or config.get("token") or "").strip()
        self._token_override = token or None
        requester = str(config.get("requester_nexus_id") or os.getenv("NEXUS_REQUESTER_ID") or "").strip()
        self._requester_nexus_id = requester or None

    @staticmethod
    def _auth_enabled() -> bool:
        try:
            from nexus.core.auth.access_domain import auth_enabled
        except Exception:
            return False
        return bool(auth_enabled())

    def _requester_token(self, issue_number: str | None = None) -> str | None:
        if not self._auth_enabled():
            return None

        requester_nexus_id = self._requester_nexus_id
        if not requester_nexus_id and issue_number:
            try:
                from nexus.core.auth.credential_store import (
                    get_issue_requester,
                    get_issue_requester_by_url,
                )

                requester_nexus_id = get_issue_requester(str(self.repo), str(issue_number))
                if not requester_nexus_id:
                    requester_nexus_id = get_issue_requester_by_url(
                        build_issue_url(
                            str(self.repo),
                            str(issue_number),
                            {"git_platform": "gitlab"},
                        )
                    )
            except Exception:
                requester_nexus_id = None

        if not requester_nexus_id:
            return None

        try:
            from nexus.core.auth.access_domain import build_execution_env

            user_env, env_error = build_execution_env(str(requester_nexus_id))
            if env_error:
                logger.warning(
                    "Requester token unavailable for repo=%s issue=%s requester=%s: %s",
                    self.repo,
                    issue_number,
                    requester_nexus_id,
                    env_error,
                )
                return None
            token = str(
                user_env.get("GITLAB_TOKEN")
                or user_env.get("GLAB_TOKEN")
                or user_env.get("GITHUB_TOKEN")
                or ""
            ).strip()
            return token or None
        except Exception:
            return None

    def _token(self, issue_number: str | None = None) -> str | None:
        if self._token_override:
            return self._token_override

        requester_token = self._requester_token(issue_number)
        if requester_token:
            return requester_token

        if self._auth_enabled():
            return None

        token = str(
            os.getenv("GITLAB_TOKEN") or os.getenv("GLAB_TOKEN") or os.getenv("GITHUB_TOKEN") or ""
        ).strip()
        return token or None

    def _command_env(self, issue_number: str | None = None) -> dict[str, str]:
        env = dict(os.environ)
        token = self._token(issue_number)
        if token:
            env["GITLAB_TOKEN"] = token
            env["GLAB_TOKEN"] = token
            env.setdefault("GITHUB_TOKEN", token)
            env.setdefault("GH_TOKEN", token)
        elif self._auth_enabled():
            for key in ("GITLAB_TOKEN", "GLAB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
                env.pop(key, None)
        return env

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
            self._run_with_retry(cmd, max_attempts=self.max_attempts, issue_number=str(issue_number))
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
                env=self._command_env(),
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
            self._run_with_retry(cmd, max_attempts=self.max_attempts, issue_number=str(issue_number))
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
            self._run_with_retry(cmd, max_attempts=self.max_attempts, issue_number=str(issue_number))
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
            result = self._run_with_retry(cmd, max_attempts=self.max_attempts, issue_number=str(issue_number))
            data = json.loads(result.stdout or "{}")
            comments: list[dict[str, Any]] = []
            if "comments" in (fields or []):
                notes_cmd = [
                    "glab",
                    "api",
                    (
                        f"projects/{self.repo.replace('/', '%2F')}/issues/"
                        f"{issue_number}/notes"
                    ),
                ]
                notes_result = self._run_with_retry(
                    notes_cmd,
                    max_attempts=self.max_attempts,
                    issue_number=str(issue_number),
                )
                notes_data = json.loads(notes_result.stdout or "[]")
                for row in notes_data or []:
                    if not isinstance(row, dict):
                        continue
                    comments.append(
                        {
                            "id": row.get("id") or "",
                            "body": row.get("body") or "",
                            "createdAt": row.get("created_at") or "",
                            "updatedAt": row.get("updated_at") or "",
                        }
                    )
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
                if "comments" in fields:
                    filtered["comments"] = comments
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
            self._run_with_retry(cmd, max_attempts=self.max_attempts, issue_number=str(issue_number))
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
            self._run_with_retry(cmd, max_attempts=self.max_attempts, issue_number=str(issue_number))
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
        issue_number: str | None = None,
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
                    env=self._command_env(issue_number),
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
