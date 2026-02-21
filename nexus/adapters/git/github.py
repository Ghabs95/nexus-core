"""GitHub platform adapter using gh CLI."""
import json
import logging
import subprocess
from datetime import datetime, timezone
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
        # gh api uses full URL paths; --repo is only for issue/pr subcommands
        if args and args[0] == "api":
            cmd = ["gh"] + args
        else:
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

    async def list_open_issues(
        self,
        limit: int = 100,
        labels: Optional[List[str]] = None,
    ) -> List[Issue]:
        """List open issues with optional label filtering."""
        args = [
            "issue", "list",
            "--state", "open",
            "--limit", str(limit),
            "--json", "number,title,body,state,labels,createdAt,updatedAt,url",
        ]
        if labels:
            args.extend(["--label", ",".join(labels)])

        try:
            output = self._run_gh_command(args)
            data = json.loads(output)
            return [self._parse_issue(item) for item in data]
        except RuntimeError:
            return []

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
            created_at=datetime.now(timezone.utc),
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
        logger.warning("Branch creation not implemented in GitHub adapter")
        return f"https://github.com/{self.repo}/tree/{branch_name}"

    async def create_pr_from_changes(
        self,
        repo_dir: str,
        issue_number: str,
        title: str,
        body: str,
        issue_repo: Optional[str] = None,
        base_branch: str = "main",
        branch_prefix: str = "nexus",
    ) -> Optional[PullRequest]:
        """Create a PR from uncommitted changes in a local repository.

        Performs: detect changes → create branch → stage → commit → push → open PR.
        Automatically appends a GitHub closing keyword reference when the body
        does not already contain one:
        - same repo: ``Closes #<issue_number>``
        - cross repo: ``Closes <owner/repo>#<issue_number>``
        Returns None if no changes are detected.
        """
        import os
        import re as _re

        if not os.path.isdir(os.path.join(repo_dir, ".git")):
            logger.warning(f"Not a git repo: {repo_dir}")
            return None

        issue_repo_ref = (issue_repo or self.repo or "").strip()
        same_repo_issue = not issue_repo_ref or issue_repo_ref == self.repo
        if same_repo_issue:
            issue_ref = f"#{issue_number}"
            closing_ref_pattern = rf"(?:#{_re.escape(issue_number)}|{_re.escape(self.repo)}#{_re.escape(issue_number)})"
        else:
            issue_ref = f"{issue_repo_ref}#{issue_number}"
            issue_url = rf"https?://github\.com/{_re.escape(issue_repo_ref)}/issues/{_re.escape(issue_number)}"
            closing_ref_pattern = rf"(?:{_re.escape(issue_repo_ref)}#{_re.escape(issue_number)}|{issue_url})"

        closing_pattern = _re.compile(
            rf"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+{closing_ref_pattern}\b",
            _re.IGNORECASE,
        )
        if issue_number and not closing_pattern.search(body):
            body = f"{body}\n\nCloses {issue_ref}"

        def _git(args: list, timeout: int = 30) -> subprocess.CompletedProcess:
            return subprocess.run(
                ["git"] + args,
                cwd=repo_dir,
                text=True,
                capture_output=True,
                timeout=timeout,
            )

        # Detect changes
        diff = _git(["diff", "--stat", "HEAD"])
        staged = _git(["diff", "--cached", "--stat"])
        untracked = _git(["ls-files", "--others", "--exclude-standard"])
        has_changes = bool(
            (diff.stdout and diff.stdout.strip())
            or (staged.stdout and staged.stdout.strip())
            or (untracked.stdout and untracked.stdout.strip())
        )
        if not has_changes:
            logger.info(f"No uncommitted changes in {repo_dir} — skipping PR creation")
            return None

        # Resolve current branch as base
        current = _git(["rev-parse", "--abbrev-ref", "HEAD"])
        resolved_base = current.stdout.strip() or base_branch

        branch_name = f"{branch_prefix}/issue-{issue_number}"

        try:
            # Create and switch to branch
            result = _git(["checkout", "-b", branch_name])
            if result.returncode != 0:
                logger.warning(f"Could not create branch {branch_name}: {result.stderr}")
                return None

            # Stage all changes
            _git(["add", "-A"])

            # Commit
            commit_msg = f"feat: resolve issue #{issue_number} (automated by Nexus)"
            _git(["commit", "-m", commit_msg])

            # Push
            push = _git(["push", "-u", "origin", branch_name], timeout=60)
            if push.returncode != 0:
                logger.warning(f"Could not push branch {branch_name}: {push.stderr}")
                _git(["checkout", resolved_base])
                return None

            # Create PR via gh CLI
            pr_result = subprocess.run(
                [
                    "gh", "pr", "create",
                    "--repo", self.repo,
                    "--base", resolved_base,
                    "--head", branch_name,
                    "--title", title,
                    "--body", body,
                ],
                cwd=repo_dir,
                text=True,
                capture_output=True,
                timeout=30,
            )
            if pr_result.returncode != 0:
                logger.warning(
                    f"Could not create PR for issue #{issue_number}: "
                    f"{pr_result.stderr or pr_result.stdout}"
                )
                _git(["checkout", resolved_base])
                return None

            pr_url = pr_result.stdout.strip()
            logger.info(f"Created PR for issue #{issue_number}: {pr_url}")

            return PullRequest(
                id="0",
                number=0,
                title=title,
                state="open",
                head_branch=branch_name,
                base_branch=resolved_base,
                url=pr_url,
                linked_issues=[issue_number],
            )
        except Exception as exc:
            logger.warning(f"Error during PR creation for issue #{issue_number}: {exc}")
            return None
        finally:
            # Always switch back to the base branch
            _git(["checkout", resolved_base])

    def get_workflow_type_from_issue(
        self,
        issue_number: int,
        label_prefix: str = "workflow:",
        default: Optional[str] = None,
    ) -> Optional[str]:
        """Extract the workflow type from an issue's labels.

        Looks for labels matching *label_prefix* (e.g. ``workflow:full``,
        ``workflow:shortened``, ``workflow:fast-track``) and returns the
        normalised workflow_type string via
        :py:meth:`~nexus.core.workflow.WorkflowDefinition.normalize_workflow_type`.

        The engine does **not** validate the extracted type against a
        hardcoded list — the workflow definition YAML is the source of
        truth for valid types.

        Args:
            issue_number: GitHub issue number.
            label_prefix: Prefix to match on labels (default ``"workflow:"``).
            default: Value returned when no matching label is found.

        Returns:
            Normalised workflow_type string, or *default*.
        """
        from nexus.core.workflow import WorkflowDefinition

        try:
            args = [
                "issue", "view", str(issue_number),
                "--json", "labels",
            ]
            output = self._run_gh_command(args, timeout=10)
            data = json.loads(output)
            labels = [l.get("name", "") for l in data.get("labels", [])]

            for label in labels:
                if label.startswith(label_prefix):
                    raw = label[len(label_prefix):]
                    return WorkflowDefinition.normalize_workflow_type(raw, default=default)

            return default
        except Exception as e:
            logger.error(
                f"Failed to get workflow_type from issue #{issue_number}: {e}"
            )
            return default

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
