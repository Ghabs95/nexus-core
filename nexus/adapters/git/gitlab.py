"""GitLab platform adapter using the GitLab REST API.

Uses only the Python standard library (``urllib``) so no extra dependencies
are required.  Pass a personal access token via ``token``.

Example::

    from nexus.adapters.git.gitlab import GitLabPlatform

    gl = GitLabPlatform(
        base_url="https://gitlab.com",
        token="glpat-…",
        repo="mygroup/myproject",
    )
"""
import asyncio
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from nexus.adapters.git.base import Comment, GitPlatform, Issue, PullRequest

logger = logging.getLogger(__name__)


class GitLabPlatform(GitPlatform):
    """GitLab platform adapter (REST API v4).

    Args:
        base_url: GitLab instance base URL (default ``https://gitlab.com``).
        token: Personal access token (``glpat-…``) or deploy token.
        repo: Namespace/path of the project (e.g.  ``"mygroup/myproject"``).
    """

    def __init__(
        self,
        token: str,
        repo: str,
        base_url: str = "https://gitlab.com",
    ):
        self._token = token
        self._repo = repo
        self._encoded_repo = urllib.parse.quote(repo, safe="")
        self._api_base = f"{base_url.rstrip('/')}/api/v4"

    # ------------------------------------------------------------------
    # GitPlatform interface
    # ------------------------------------------------------------------

    async def create_issue(
        self, title: str, body: str, labels: Optional[List[str]] = None
    ) -> Issue:
        payload: Dict[str, Any] = {"title": title, "description": body}
        if labels:
            payload["labels"] = ",".join(labels)
        data = await self._post(f"projects/{self._encoded_repo}/issues", payload)
        return self._to_issue(data)

    async def get_issue(self, issue_id: str) -> Optional[Issue]:
        try:
            data = await self._get(f"projects/{self._encoded_repo}/issues/{issue_id}")
            return self._to_issue(data)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise

    async def update_issue(
        self,
        issue_id: str,
        title: Optional[str] = None,
        body: Optional[str] = None,
        state: Optional[str] = None,
        labels: Optional[List[str]] = None,
    ) -> Issue:
        payload: Dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if body is not None:
            payload["description"] = body
        if state is not None:
            # GitLab uses "close" / "reopen" as state_event
            payload["state_event"] = "close" if state == "closed" else "reopen"
        if labels is not None:
            payload["labels"] = ",".join(labels)
        data = await self._put(f"projects/{self._encoded_repo}/issues/{issue_id}", payload)
        return self._to_issue(data)

    async def add_comment(self, issue_id: str, body: str) -> Comment:
        data = await self._post(
            f"projects/{self._encoded_repo}/issues/{issue_id}/notes",
            {"body": body},
        )
        return self._to_comment(data, issue_id)

    async def get_comments(
        self, issue_id: str, since: Optional[datetime] = None
    ) -> List[Comment]:
        url = f"projects/{self._encoded_repo}/issues/{issue_id}/notes?per_page=100&sort=asc"
        items = await self._get(url)
        comments = [self._to_comment(c, issue_id) for c in items]
        if since:
            comments = [c for c in comments if c.created_at >= since]
        return comments

    async def close_issue(self, issue_id: str, comment: Optional[str] = None) -> None:
        if comment:
            await self.add_comment(issue_id, comment)
        await self.update_issue(issue_id, state="closed")

    async def search_linked_prs(self, issue_id: str) -> List[PullRequest]:
        """GitLab links MRs to issues via the project's MR API; search by title keyword."""
        mrs = await self._get(
            f"projects/{self._encoded_repo}/merge_requests"
            f"?state=all&search=%23{issue_id}&per_page=20"
        )
        return [self._to_pr(mr) for mr in mrs]

    async def create_branch(self, branch_name: str, base_branch: str = "main") -> str:
        data = await self._post(
            f"projects/{self._encoded_repo}/repository/branches",
            {"branch": branch_name, "ref": base_branch},
        )
        return data.get("web_url", branch_name)

    async def create_pr_from_changes(
        self,
        repo_dir: str,
        issue_number: str,
        title: str,
        body: str,
        base_branch: str = "main",
        branch_prefix: str = "nexus",
    ) -> Optional[PullRequest]:
        """Push local changes and open a GitLab merge request."""
        import subprocess

        branch_name = f"{branch_prefix}/issue-{issue_number}"

        # Stage + commit + push
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True,
            cwd=repo_dir,
        )
        if not result.stdout.strip():
            subprocess.run(["git", "add", "-A"], cwd=repo_dir, check=True)

        status = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            cwd=repo_dir,
        )
        if not status.stdout.strip():
            logger.info("No local changes detected — skipping MR creation")
            return None

        subprocess.run(
            ["git", "checkout", "-B", branch_name], cwd=repo_dir, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", f"nexus: {title} (closes #{issue_number})"],
            cwd=repo_dir,
            check=True,
        )
        subprocess.run(
            ["git", "push", "--set-upstream", "origin", branch_name, "--force"],
            cwd=repo_dir,
            check=True,
        )

        data = await self._post(
            f"projects/{self._encoded_repo}/merge_requests",
            {
                "source_branch": branch_name,
                "target_branch": base_branch,
                "title": title,
                "description": body,
                "remove_source_branch": True,
            },
        )
        return self._to_pr(data)

    # ------------------------------------------------------------------
    # HTTP helpers (sync + asyncio.to_thread wrapper)
    # ------------------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        return {
            "PRIVATE-TOKEN": self._token,
            "Content-Type": "application/json",
        }

    def _sync_request(self, method: str, path: str, payload: Optional[Dict] = None) -> Any:
        url = f"{self._api_base}/{path.lstrip('/')}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            logger.error("GitLab API %s %s → HTTP %d: %s", method, path, exc.code, body)
            raise

    async def _get(self, path: str) -> Any:
        return await asyncio.to_thread(self._sync_request, "GET", path)

    async def _post(self, path: str, payload: Dict) -> Any:
        return await asyncio.to_thread(self._sync_request, "POST", path, payload)

    async def _put(self, path: str, payload: Dict) -> Any:
        return await asyncio.to_thread(self._sync_request, "PUT", path, payload)

    # ------------------------------------------------------------------
    # Model converters
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_dt(val: Optional[str]) -> datetime:
        if not val:
            return datetime.now(tz=timezone.utc)
        # GitLab returns ISO 8601 with 'Z' or '+HH:MM'
        return datetime.fromisoformat(val.replace("Z", "+00:00"))

    def _to_issue(self, data: Dict) -> Issue:
        return Issue(
            id=str(data["id"]),
            number=data["iid"],
            title=data.get("title", ""),
            body=data.get("description") or "",
            state=data.get("state", "opened").replace("opened", "open"),
            labels=[lbl if isinstance(lbl, str) else lbl["name"] for lbl in data.get("labels", [])],
            created_at=self._parse_dt(data.get("created_at")),
            updated_at=self._parse_dt(data.get("updated_at")),
            url=data.get("web_url", ""),
        )

    @staticmethod
    def _to_comment(data: Dict, issue_id: str) -> Comment:
        return Comment(
            id=str(data["id"]),
            issue_id=str(issue_id),
            author=data.get("author", {}).get("username", "unknown"),
            body=data.get("body", ""),
            created_at=GitLabPlatform._parse_dt(data.get("created_at")),
            url=data.get("noteable_url", ""),
        )

    @staticmethod
    def _to_pr(data: Dict) -> PullRequest:
        return PullRequest(
            id=str(data["id"]),
            number=data.get("iid", 0),
            title=data.get("title", ""),
            state=data.get("state", "opened").replace("opened", "open"),
            head_branch=data.get("source_branch", ""),
            base_branch=data.get("target_branch", "main"),
            url=data.get("web_url", ""),
        )
