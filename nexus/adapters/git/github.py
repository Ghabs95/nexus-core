"""GitHub platform adapter using gh CLI."""
import json
import logging
import subprocess
from datetime import datetime
from typing import List, Optional

from nexus.adapters.git.base import Comment, GitPlatform, Issue, PullRequest

logger = logging.getLogger(__name__)


class GitHubPlatform(GitPlatform):
    """GitHub platform adapter using gh CLI."""

    def __init__(self, repo: str, token: Optional[str] = None):
        """
        Initialize GitHub adapter.
        
        Args:
            repo: Repository in format "owner/name"
            token: Optional GitHub token (uses gh CLI auth if not provided)
        """
        self.repo = repo
        self.token = token
        self._check_gh_cli()

    def _check_gh_cli(self) -> None:
        """Check if gh CLI is installed and authenticated."""
        try:
            subprocess.run(
                ["gh", "--version"],
                capture_output=True,
                check=True,
                timeout=5
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            raise RuntimeError(
                "gh CLI not found. Install from https://cli.github.com/"
            )

    def _run_gh_command(self, args: List[str], timeout: int = 30) -> str:
        """Run gh CLI command and return stdout."""
        cmd = ["gh"] + args + ["--repo", self.repo]
        
        env = None
        if self.token:
            import os
            env = os.environ.copy()
            env["GITHUB_TOKEN"] = self.token
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=True,
                env=env
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            logger.error(f"gh command failed: {e.stderr}")
            raise RuntimeError(f"GitHub CLI error: {e.stderr}")
        except subprocess.TimeoutExpired:
            logger.error(f"gh command timed out after {timeout}s")
            raise RuntimeError(f"GitHub CLI command timed out")

    async def create_issue(
        self, title: str, body: str, labels: Optional[List[str]] = None
    ) -> Issue:
        """Create a new issue."""
        args = ["issue", "create", "--title", title, "--body", body]
        
        if labels:
            args.extend(["--label", ",".join(labels)])
        
        args.append("--json")
        args.append("number,title,body,state,labels,createdAt,updatedAt,url")
        
        output = self._run_gh_command(args)
        data = json.loads(output)
        
        return self._parse_issue(data)

    async def get_issue(self, issue_id: str) -> Optional[Issue]:
        """Get issue by ID or number."""
        try:
            args = [
                "issue", "view", str(issue_id),
                "--json", "number,title,body,state,labels,createdAt,updatedAt,url"
            ]
            output = self._run_gh_command(args)
            data = json.loads(output)
            return self._parse_issue(data)
        except RuntimeError:
            return None

    async def update_issue(
        self,
        issue_id: str,
        title: Optional[str] = None,
        body: Optional[str] = None,
        state: Optional[str] = None,
        labels: Optional[List[str]] = None,
    ) -> Issue:
        """Update issue properties."""
        args = ["issue", "edit", str(issue_id)]
        
        if title:
            args.extend(["--title", title])
        if body:
            args.extend(["--body", body])
        if labels:
            args.extend(["--add-label", ",".join(labels)])
        
        self._run_gh_command(args)
        
        # Close/reopen if state changed
        if state == "closed":
            self._run_gh_command(["issue", "close", str(issue_id)])
        elif state == "open":
            self._run_gh_command(["issue", "reopen", str(issue_id)])
        
        # Fetch updated issue
        updated = await self.get_issue(issue_id)
        if not updated:
            raise RuntimeError(f"Failed to fetch updated issue {issue_id}")
        return updated

    async def add_comment(self, issue_id: str, body: str) -> Comment:
        """Add a comment to an issue."""
        args = ["issue", "comment", str(issue_id), "--body", body]
        self._run_gh_command(args)
        
        # Fetch the comment (last comment on the issue)
        comments = await self.get_comments(issue_id)
        if comments:
            return comments[-1]
        
        # Fallback: create a comment object
        return Comment(
            id="unknown",
            issue_id=str(issue_id),
            author="bot",
            body=body,
            created_at=datetime.utcnow(),
            url=f"https://github.com/{self.repo}/issues/{issue_id}"
        )

    async def get_comments(
        self, issue_id: str, since: Optional[datetime] = None
    ) -> List[Comment]:
        """Get comments for an issue."""
        args = [
            "api",
            f"/repos/{self.repo}/issues/{issue_id}/comments",
            "--jq", ".[]|{id,user:.user.login,body,created_at,html_url}"
        ]
        
        try:
            output = self._run_gh_command(args)
            if not output:
                return []
            
            comments = []
            for line in output.strip().split("\n"):
                if not line:
                    continue
                data = json.loads(line)
                created_at = datetime.fromisoformat(data["created_at"].replace("Z", "+00:00"))
                
                if since and created_at < since:
                    continue
                
                comment = Comment(
                    id=str(data["id"]),
                    issue_id=str(issue_id),
                    author=data["user"],
                    body=data["body"],
                    created_at=created_at,
                    url=data["html_url"]
                )
                comments.append(comment)
            
            return comments
        except RuntimeError:
            return []

    async def close_issue(self, issue_id: str, comment: Optional[str] = None) -> None:
        """Close an issue."""
        if comment:
            await self.add_comment(issue_id, comment)
        
        self._run_gh_command(["issue", "close", str(issue_id)])
        logger.info(f"Closed issue #{issue_id}")

    async def search_linked_prs(self, issue_id: str) -> List[PullRequest]:
        """Find PRs linked to this issue."""
        # Search for PRs mentioning this issue
        args = [
            "pr", "list",
            "--search", f"#{issue_id}",
            "--json", "number,title,state,headRefName,baseRefName,url"
        ]
        
        try:
            output = self._run_gh_command(args)
            data = json.loads(output)
            
            prs = []
            for pr_data in data:
                pr = PullRequest(
                    id=str(pr_data["number"]),
                    number=pr_data["number"],
                    title=pr_data["title"],
                    state=pr_data["state"].lower(),
                    head_branch=pr_data["headRefName"],
                    base_branch=pr_data["baseRefName"],
                    url=pr_data["url"],
                    linked_issues=[issue_id]
                )
                prs.append(pr)
            
            return prs
        except RuntimeError:
            return []

    async def create_branch(self, branch_name: str, base_branch: str = "main") -> str:
        """Create a new branch. Returns branch URL."""
        # Note: gh CLI doesn't have a direct branch create command
        # This would typically be done via git commands in a local repo
        # For now, we'll return a URL where the branch would be
        logger.warning("Branch creation not implemented in GitHub adapter")
        return f"https://github.com/{self.repo}/tree/{branch_name}"

    def _parse_issue(self, data: dict) -> Issue:
        """Parse issue data from gh CLI JSON output."""
        labels = [label["name"] for label in data.get("labels", [])]
        
        return Issue(
            id=str(data["number"]),
            number=data["number"],
            title=data["title"],
            body=data.get("body", ""),
            state=data["state"].lower(),
            labels=labels,
            created_at=datetime.fromisoformat(data["createdAt"].replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(data["updatedAt"].replace("Z", "+00:00")),
            url=data["url"]
        )
