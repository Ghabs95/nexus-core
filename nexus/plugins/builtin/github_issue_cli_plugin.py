"""Built-in GitHub issue plugin.

Defaults to the direct API adapter and falls back to gh only when
NEXUS_GIT_PLATFORM_TRANSPORT=cli.
"""

import json
import logging
import os
import subprocess
import time
import urllib.error
from typing import Any

from nexus.adapters.git.factory import get_git_platform_transport
from nexus.adapters.git.github import GitHubPlatform
from nexus.adapters.git.utils import build_issue_url

logger = logging.getLogger(__name__)


class GitHubIssueCLIPlugin:
    """Create and manage GitHub issues via API or gh CLI."""

    def __init__(self, config: dict[str, Any]):
        self.repo = config.get("repo", "")
        self.max_attempts = int(config.get("max_attempts", 3))
        self.timeout = int(config.get("timeout", 30))
        self.base_delay = float(config.get("base_delay", 1.0))
        self._transport = get_git_platform_transport()
        token = str(config.get("token_override") or config.get("token") or "").strip()
        self._token_override = token or None
        requester = str(config.get("requester_nexus_id") or os.getenv("NEXUS_REQUESTER_ID") or "").strip()
        self._requester_nexus_id = requester or None

    def _use_api(self) -> bool:
        return self._transport == "api"

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
                            {"git_platform": "github"},
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
                user_env.get("GITHUB_TOKEN")
                or user_env.get("GH_TOKEN")
                or user_env.get("GITLAB_TOKEN")
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

        token = str(os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or "").strip()
        return token or None

    def _platform(self, issue_number: str | None = None) -> GitHubPlatform:
        return GitHubPlatform(repo=self.repo, token=self._token(issue_number))

    def _issue_platform(self, issue_number: str | None = None) -> GitHubPlatform:
        try:
            return self._platform(issue_number)
        except TypeError:
            return self._platform()

    def _command_env(self, issue_number: str | None = None) -> dict[str, str]:
        env = dict(os.environ)
        token = self._token(issue_number)
        if token:
            env["GITHUB_TOKEN"] = token
            env["GH_TOKEN"] = token
            env.setdefault("GITLAB_TOKEN", token)
        elif self._auth_enabled():
            for key in ("GITHUB_TOKEN", "GH_TOKEN", "GITLAB_TOKEN", "GLAB_TOKEN"):
                env.pop(key, None)
        return env

    def _subprocess_run(
        self,
        cmd: list[str],
        *,
        check: bool,
        timeout: int,
        capture_output: bool,
        text: bool,
        issue_number: str | None = None,
    ) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                cmd,
                check=check,
                timeout=timeout,
                capture_output=capture_output,
                text=text,
                env=self._command_env(issue_number),
            )
        except TypeError:
            return subprocess.run(
                cmd,
                check=check,
                timeout=timeout,
                capture_output=capture_output,
                text=text,
            )

    def create_issue(self, title: str, body: str, labels: list[str] | None = None) -> str | None:
        labels = labels or []
        if self._use_api():
            try:
                data = self._platform()._sync_request(
                    "POST",
                    f"repos/{self.repo}/issues",
                    {"title": title, "body": body, "labels": labels},
                )
                return str(data.get("html_url") or data.get("url") or "").strip() or None
            except Exception as exc:
                logger.error("Failed to create issue for %s via API: %s", self.repo, exc)
                return None

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
        if self._use_api():
            try:
                self._issue_platform(issue_number)._sync_request(
                    "POST",
                    f"repos/{self.repo}/issues/{issue_number}/comments",
                    {"body": body},
                )
                return True
            except Exception as exc:
                logger.error("Failed to add issue comment via API: %s", exc)
                return False

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
            self._run_with_retry(cmd, max_attempts=self.max_attempts, issue_number=str(issue_number))
            return True
        except Exception as exc:
            logger.error("Failed to add issue comment: %s", exc)
            return False

    def ensure_label(self, label: str, color: str, description: str) -> bool:
        if self._use_api():
            try:
                self._platform()._sync_request(
                    "POST",
                    f"repos/{self.repo}/labels",
                    {"name": label, "color": color, "description": description},
                )
                return True
            except urllib.error.HTTPError as exc:
                body = getattr(exc, "_nexus_body", "")
                if exc.code == 422 and "already_exists" in str(body):
                    return True
                logger.warning("Failed to ensure label %s via API: %s", label, body or exc)
                return False
            except Exception as exc:
                logger.warning("Failed to ensure label %s via API: %s", label, exc)
                return False

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
            result = self._subprocess_run(
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
        if self._use_api():
            try:
                platform = self._issue_platform(issue_number)
                data = platform._sync_request("GET", f"repos/{self.repo}/issues/{issue_number}")
                labels = []
                for row in data.get("labels", []):
                    name = row.get("name") if isinstance(row, dict) else row
                    if name:
                        labels.append(str(name))
                if label not in labels:
                    labels.append(label)
                platform._sync_request(
                    "PATCH",
                    f"repos/{self.repo}/issues/{issue_number}",
                    {"labels": labels},
                )
                return True
            except Exception as exc:
                logger.error("Failed to add label to issue %s via API: %s", issue_number, exc)
                return False

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
            self._run_with_retry(cmd, max_attempts=self.max_attempts, issue_number=str(issue_number))
            return True
        except Exception as exc:
            logger.error("Failed to add label to issue %s: %s", issue_number, exc)
            return False

    def add_assignee(self, issue_number: str, assignee: str) -> bool:
        assignee = str(assignee or "").strip().lstrip("@")
        if self._use_api():
            try:
                platform = self._issue_platform(issue_number)
                if assignee == "me":
                    viewer = platform._sync_request("GET", "user")
                    assignee = str(viewer.get("login") or "").strip()
                    if not assignee:
                        return False
                data = platform._sync_request("GET", f"repos/{self.repo}/issues/{issue_number}")
                assignees = [
                    str(row.get("login"))
                    for row in data.get("assignees", [])
                    if isinstance(row, dict) and row.get("login")
                ]
                if assignee and assignee not in assignees:
                    assignees.append(assignee)
                platform._sync_request(
                    "PATCH",
                    f"repos/{self.repo}/issues/{issue_number}",
                    {"assignees": assignees},
                )
                return True
            except Exception as exc:
                logger.error("Failed to assign issue %s via API: %s", issue_number, exc)
                return False

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
            self._run_with_retry(cmd, max_attempts=self.max_attempts, issue_number=str(issue_number))
            return True
        except Exception as exc:
            logger.error("Failed to assign issue %s: %s", issue_number, exc)
            return False

    def get_issue(self, issue_number: str, fields: list[str]) -> dict[str, Any] | None:
        if self._use_api():
            try:
                platform = self._issue_platform(issue_number)
                data = platform._sync_request("GET", f"repos/{self.repo}/issues/{issue_number}")
                comments: list[dict[str, Any]] = []
                if "comments" in fields:
                    raw_comments = platform._sync_request(
                        "GET", f"repos/{self.repo}/issues/{issue_number}/comments"
                    )
                    for row in raw_comments or []:
                        if not isinstance(row, dict):
                            continue
                        comments.append(
                            {
                                "id": row.get("id") or row.get("node_id") or "",
                                "body": row.get("body") or "",
                                "createdAt": row.get("created_at") or "",
                                "updatedAt": row.get("updated_at") or "",
                            }
                        )
                field_map = {
                    "title": data.get("title"),
                    "body": data.get("body"),
                    "state": data.get("state"),
                    "number": data.get("number"),
                    "url": data.get("html_url") or data.get("url"),
                    "createdAt": data.get("created_at"),
                    "updatedAt": data.get("updated_at"),
                    "comments": comments,
                    "labels": [
                        row.get("name") if isinstance(row, dict) else row
                        for row in data.get("labels", [])
                    ],
                }
                return {field: field_map.get(field) for field in fields}
            except Exception as exc:
                logger.error("Failed to read issue %s via API: %s", issue_number, exc)
                return None

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
            result = self._run_with_retry(cmd, max_attempts=self.max_attempts, issue_number=str(issue_number))
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
        if self._use_api():
            try:
                self._issue_platform(issue_number)._sync_request(
                    "PATCH",
                    f"repos/{self.repo}/issues/{issue_number}",
                    {"body": body},
                )
                return True
            except Exception as exc:
                logger.error("Failed to update issue %s body via API: %s", issue_number, exc)
                return False

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
            self._run_with_retry(cmd, max_attempts=self.max_attempts, issue_number=str(issue_number))
            return True
        except Exception as exc:
            logger.error("Failed to update issue %s body: %s", issue_number, exc)
            return False

    def close_issue(self, issue_number: str) -> bool:
        if self._use_api():
            try:
                self._issue_platform(issue_number)._sync_request(
                    "PATCH",
                    f"repos/{self.repo}/issues/{issue_number}",
                    {"state": "closed"},
                )
                return True
            except Exception as exc:
                logger.error("Failed to close issue %s via API: %s", issue_number, exc)
                return False

        cmd = [
            "gh",
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
        state: str = "open",
        limit: int = 10,
        fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        fields = fields or ["number", "title", "state"]
        if self._use_api():
            try:
                data = self._platform()._sync_request(
                    "GET",
                    f"repos/{self.repo}/issues?state={state}&per_page={limit}",
                )
                rows = []
                for row in data:
                    if "pull_request" in row:
                        continue
                    labels = [
                        item.get("name") if isinstance(item, dict) else item
                        for item in row.get("labels", [])
                    ]
                    field_map = {
                        "number": row.get("number"),
                        "title": row.get("title"),
                        "state": row.get("state"),
                        "body": row.get("body"),
                        "url": row.get("html_url") or row.get("url"),
                        "createdAt": row.get("created_at"),
                        "updatedAt": row.get("updated_at"),
                        "labels": labels,
                    }
                    rows.append({field: field_map.get(field) for field in fields})
                return rows
            except Exception as exc:
                logger.error("Failed to list issues for %s via API: %s", self.repo, exc)
                return []

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
        issue_number: str | None = None,
    ) -> subprocess.CompletedProcess:
        attempt = 1
        while attempt <= max_attempts:
            try:
                return self._subprocess_run(
                    cmd,
                    check=True,
                    timeout=self.timeout,
                    capture_output=True,
                    text=True,
                    issue_number=issue_number,
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
