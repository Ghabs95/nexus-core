"""Base interface for Git platforms (GitHub, GitLab, etc.)."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Issue:
    """Platform-agnostic issue representation."""

    id: str
    number: int
    title: str
    body: str
    state: str  # "open", "closed"
    labels: list[str]
    created_at: datetime
    updated_at: datetime
    url: str


@dataclass
class PullRequest:
    """Platform-agnostic PR/MR representation."""

    id: str
    number: int
    title: str
    state: str  # "open", "merged", "closed"
    head_branch: str
    base_branch: str
    url: str
    linked_issues: list[str] = field(default_factory=list)


@dataclass
class Comment:
    """Platform-agnostic comment representation."""

    id: str
    issue_id: str
    author: str
    body: str
    created_at: datetime
    url: str


class GitPlatform(ABC):
    """Abstract interface for Git platform operations."""

    @abstractmethod
    async def list_open_issues(
        self,
        limit: int = 100,
        labels: list[str] | None = None,
    ) -> list[Issue]:
        """List open issues, optionally filtered by labels."""
        pass

    @abstractmethod
    async def create_issue(self, title: str, body: str, labels: list[str] | None = None) -> Issue:
        """Create a new issue."""
        pass

    @abstractmethod
    async def get_issue(self, issue_id: str) -> Issue | None:
        """Get issue by ID or number."""
        pass

    @abstractmethod
    async def update_issue(
        self,
        issue_id: str,
        title: str | None = None,
        body: str | None = None,
        state: str | None = None,
        labels: list[str] | None = None,
    ) -> Issue:
        """Update issue properties."""
        pass

    @abstractmethod
    async def add_comment(self, issue_id: str, body: str) -> Comment:
        """Add a comment to an issue."""
        pass

    @abstractmethod
    async def get_comments(self, issue_id: str, since: datetime | None = None) -> list[Comment]:
        """Get comments for an issue."""
        pass

    @abstractmethod
    async def close_issue(self, issue_id: str, comment: str | None = None) -> None:
        """Close an issue."""
        pass

    @abstractmethod
    async def search_linked_prs(self, issue_id: str) -> list[PullRequest]:
        """Find PRs linked to this issue."""
        pass

    @abstractmethod
    async def create_branch(self, branch_name: str, base_branch: str = "main") -> str:
        """Create a new branch. Returns branch URL."""
        pass

    @abstractmethod
    async def create_pr_from_changes(
        self,
        repo_dir: str,
        issue_number: str,
        title: str,
        body: str,
        issue_repo: str | None = None,
        base_branch: str = "main",
        branch_prefix: str = "nexus",
    ) -> PullRequest | None:
        """Create a PR from uncommitted changes in a local repository.

        Performs the full pipeline: detect changes → create branch →
        stage → commit → push → open PR.

        Args:
            repo_dir: Absolute path to the local git repository.
            issue_number: Issue number this PR addresses.
            title: PR title.
            body: PR body (Markdown).
            issue_repo: Repository where the issue lives ("owner/repo").
                When omitted, implementations assume the PR repository.
            base_branch: Branch to merge into (default "main").
            branch_prefix: Prefix for the new branch name.

        Returns:
            PullRequest object if created, ``None`` if no changes detected.
        """
        pass

    async def merge_pull_request(
        self,
        pr_id: str,
        *,
        squash: bool = True,
        delete_branch: bool = True,
        auto: bool = True,
    ) -> str:
        """Merge a pull/merge request by provider-native identifier.

        Implementations should accept the provider's visible PR/MR number/IID.
        Returns a short provider response string on success.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not implement merge_pull_request()"
        )

    async def ensure_label(
        self,
        name: str,
        *,
        color: str,
        description: str = "",
    ) -> bool:
        """Ensure a repository label exists.

        Returns True when the label exists after the call, False on best-effort
        failure. Implementations may treat "already exists" as success.
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not implement ensure_label()")
