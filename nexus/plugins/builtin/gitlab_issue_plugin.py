"""Built-in GitLab issue plugin backed by API adapter calls."""

import logging
import os
import urllib.error
import urllib.parse
from typing import Any

from nexus.adapters.git.utils import build_issue_url
from nexus.adapters.git.gitlab import GitLabPlatform

logger = logging.getLogger(__name__)


class GitLabIssuePlugin:
    """Create and manage GitLab issues via GitLab REST API."""

    def __init__(self, config: dict[str, Any]):
        self.repo = config.get("repo", "")
        self.base_url = str(
            config.get("base_url") or config.get("gitlab_base_url") or "https://gitlab.com"
        )
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
                            {"git_platform": "gitlab", "gitlab_base_url": self.base_url},
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

    def _token(self, issue_number: str | None = None) -> str:
        if self._token_override:
            return self._token_override

        requester_token = self._requester_token(issue_number)
        if requester_token:
            return requester_token

        if self._auth_enabled():
            return ""

        return str(
            os.getenv("GITLAB_TOKEN") or os.getenv("GLAB_TOKEN") or os.getenv("GITHUB_TOKEN") or ""
        ).strip()

    def _project_path(self) -> str:
        return urllib.parse.quote(self.repo, safe="")

    def _platform(self, issue_number: str | None = None) -> GitLabPlatform:
        return GitLabPlatform(token=self._token(issue_number), repo=self.repo, base_url=self.base_url)

    @staticmethod
    def _normalize_state(state: str | None) -> str:
        value = str(state or "opened").strip().lower()
        return "open" if value == "opened" else value

    @staticmethod
    def _labels_from_issue(data: dict[str, Any]) -> list[str]:
        labels: list[str] = []
        for row in data.get("labels", []):
            if isinstance(row, dict):
                name = row.get("name")
            else:
                name = row
            if name:
                labels.append(str(name))
        return labels

    def create_issue(self, title: str, body: str, labels: list[str] | None = None) -> str | None:
        labels = labels or []
        payload: dict[str, Any] = {"title": title, "description": body}
        if labels:
            payload["labels"] = ",".join(labels)

        try:
            data = self._platform()._sync_request(
                "POST",
                f"projects/{self._project_path()}/issues",
                payload,
            )
            return str(data.get("web_url") or "").strip() or None
        except Exception as exc:
            if not labels:
                logger.error("Failed to create issue for %s via API: %s", self.repo, exc)
                return None
            logger.warning(
                "Issue creation with labels failed for %s: %s. Retrying without labels.",
                self.repo,
                exc,
            )
            try:
                data = self._platform()._sync_request(
                    "POST",
                    f"projects/{self._project_path()}/issues",
                    {"title": title, "description": body},
                )
                return str(data.get("web_url") or "").strip() or None
            except Exception as no_label_exc:
                logger.error(
                    "Failed to create issue without labels for %s: %s",
                    self.repo,
                    no_label_exc,
                )
                return None

    def add_comment(self, issue_number: str, body: str) -> bool:
        try:
            self._platform(issue_number)._sync_request(
                "POST",
                f"projects/{self._project_path()}/issues/{issue_number}/notes",
                {"body": body},
            )
            return True
        except Exception as exc:
            logger.error("Failed to add issue comment via API: %s", exc)
            return False

    def ensure_label(self, label: str, color: str, description: str) -> bool:
        try:
            self._platform()._sync_request(
                "POST",
                f"projects/{self._project_path()}/labels",
                {"name": label, "color": color, "description": description},
            )
            return True
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                return True
            logger.warning("Failed to ensure label %s via API: HTTP %s", label, exc.code)
            return False
        except Exception as exc:
            logger.warning("Failed to ensure label %s via API: %s", label, exc)
            return False

    def add_label(self, issue_number: str, label: str) -> bool:
        try:
            platform = self._platform(issue_number)
            data = platform._sync_request(
                "GET", f"projects/{self._project_path()}/issues/{issue_number}"
            )
            labels = self._labels_from_issue(data)
            if label not in labels:
                labels.append(label)
            platform._sync_request(
                "PUT",
                f"projects/{self._project_path()}/issues/{issue_number}",
                {"labels": ",".join(labels)},
            )
            return True
        except Exception as exc:
            logger.error("Failed to add label to issue %s via API: %s", issue_number, exc)
            return False

    def add_assignee(self, issue_number: str, assignee: str) -> bool:
        assignee_name = str(assignee or "").strip().lstrip("@")
        if not assignee_name:
            return False

        try:
            platform = self._platform(issue_number)
            user_id: int | None = None
            if assignee_name == "me":
                me = platform._sync_request("GET", "user")
                raw_id = me.get("id")
                user_id = int(raw_id) if raw_id is not None else None
            else:
                users = platform._sync_request(
                    "GET",
                    f"users?username={urllib.parse.quote(assignee_name, safe='')}",
                )
                if isinstance(users, list) and users:
                    selected = None
                    for row in users:
                        if str(row.get("username") or "").strip().lower() == assignee_name.lower():
                            selected = row
                            break
                    if selected is None:
                        selected = users[0]
                    raw_id = selected.get("id") if isinstance(selected, dict) else None
                    user_id = int(raw_id) if raw_id is not None else None

            if user_id is None:
                logger.warning("Failed to resolve GitLab assignee '%s' for %s", assignee_name, self.repo)
                return False

            issue_data = platform._sync_request(
                "GET", f"projects/{self._project_path()}/issues/{issue_number}"
            )
            assignee_ids: list[int] = []
            for row in issue_data.get("assignees", []):
                if not isinstance(row, dict):
                    continue
                raw_id = row.get("id")
                if raw_id is None:
                    continue
                try:
                    assignee_ids.append(int(raw_id))
                except (TypeError, ValueError):
                    continue

            if user_id not in assignee_ids:
                assignee_ids.append(user_id)

            platform._sync_request(
                "PUT",
                f"projects/{self._project_path()}/issues/{issue_number}",
                {"assignee_ids": assignee_ids},
            )
            return True
        except Exception as exc:
            logger.error("Failed to assign issue %s via API: %s", issue_number, exc)
            return False

    def get_issue(self, issue_number: str, fields: list[str]) -> dict[str, Any] | None:
        try:
            platform = self._platform(issue_number)
            data = platform._sync_request("GET", f"projects/{self._project_path()}/issues/{issue_number}")
            comments: list[dict[str, Any]] = []
            if "comments" in fields:
                raw_comments = platform._sync_request(
                    "GET", f"projects/{self._project_path()}/issues/{issue_number}/notes"
                )
                for row in raw_comments or []:
                    if not isinstance(row, dict):
                        continue
                    comments.append(
                        {
                            "id": row.get("id") or "",
                            "body": row.get("body") or "",
                            "createdAt": row.get("created_at") or "",
                            "updatedAt": row.get("updated_at") or row.get("created_at") or "",
                        }
                    )

            field_map = {
                "title": data.get("title"),
                "body": data.get("description"),
                "state": self._normalize_state(data.get("state")),
                "number": data.get("iid"),
                "url": data.get("web_url"),
                "createdAt": data.get("created_at"),
                "updatedAt": data.get("updated_at"),
                "comments": comments,
                "labels": self._labels_from_issue(data),
            }
            return {field: field_map.get(field) for field in fields}
        except Exception as exc:
            message = str(exc)
            not_found_markers = ["HTTP Error 404", "not found", "returned non-zero exit status 1"]
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
        try:
            self._platform(issue_number)._sync_request(
                "PUT",
                f"projects/{self._project_path()}/issues/{issue_number}",
                {"description": body},
            )
            return True
        except Exception as exc:
            logger.error("Failed to update issue %s body via API: %s", issue_number, exc)
            return False

    def close_issue(self, issue_number: str) -> bool:
        try:
            self._platform(issue_number)._sync_request(
                "PUT",
                f"projects/{self._project_path()}/issues/{issue_number}",
                {"state_event": "close"},
            )
            return True
        except Exception as exc:
            logger.error("Failed to close issue %s via API: %s", issue_number, exc)
            return False

    def list_issues(
        self,
        state: str = "open",
        limit: int = 10,
        fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        fields = fields or ["number", "title", "state"]
        state_map = {
            "open": "opened",
            "opened": "opened",
            "closed": "closed",
            "all": "all",
            "any": "all",
        }
        gitlab_state = state_map.get(str(state or "open").strip().lower(), "opened")

        try:
            data = self._platform()._sync_request(
                "GET",
                f"projects/{self._project_path()}/issues?state={gitlab_state}&per_page={limit}",
            )
            if not isinstance(data, list):
                return []

            rows: list[dict[str, Any]] = []
            for row in data:
                if not isinstance(row, dict):
                    continue
                field_map = {
                    "number": row.get("iid"),
                    "title": row.get("title"),
                    "state": self._normalize_state(row.get("state")),
                    "body": row.get("description"),
                    "url": row.get("web_url"),
                    "createdAt": row.get("created_at"),
                    "updatedAt": row.get("updated_at"),
                    "labels": self._labels_from_issue(row),
                }
                rows.append({field: field_map.get(field) for field in fields})
            return rows
        except Exception as exc:
            logger.error("Failed to list issues for %s via API: %s", self.repo, exc)
            return []


def register_plugins(registry) -> None:
    """Register built-in GitLab API issue plugin in a PluginRegistry."""
    from nexus.plugins import PluginKind  # type: ignore[import-untyped]

    registry.register_factory(
        kind=PluginKind.GIT_PLATFORM,
        name="gitlab-issue-api",
        version="0.1.0",
        factory=lambda config: GitLabIssuePlugin(config),
        description="GitLab issue operations via API adapter",
    )
