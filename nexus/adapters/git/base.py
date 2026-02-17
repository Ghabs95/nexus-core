"""Base interface for Git platforms (GitHub, GitLab, etc.)."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional


@dataclass
class Issue:
    """Platform-agnostic issue representation."""

    id: str
    number: int
    title: str
    body: str
    state: str  # "open", "closed"
    labels: List[str]
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
    linked_issues: List[str] = None


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
    async def create_issue(
        self, title: str, body: str, labels: Optional[List[str]] = None
    ) -> Issue:
        """Create a new issue."""
        pass

    @abstractmethod
    async def get_issue(self, issue_id: str) -> Optional[Issue]:
        """Get issue by ID or number."""
        pass

    @abstractmethod
    async def update_issue(
        self,
        issue_id: str,
        title: Optional[str] = None,
        body: Optional[str] = None,
        state: Optional[str] = None,
        labels: Optional[List[str]] = None,
    ) -> Issue:
        """Update issue properties."""
        pass

    @abstractmethod
    async def add_comment(self, issue_id: str, body: str) -> Comment:
        """Add a comment to an issue."""
        pass

    @abstractmethod
    async def get_comments(self, issue_id: str, since: Optional[datetime] = None) -> List[Comment]:
        """Get comments for an issue."""
        pass

    @abstractmethod
    async def close_issue(self, issue_id: str, comment: Optional[str] = None) -> None:
        """Close an issue."""
        pass

    @abstractmethod
    async def search_linked_prs(self, issue_id: str) -> List[PullRequest]:
        """Find PRs linked to this issue."""
        pass

    @abstractmethod
    async def create_branch(self, branch_name: str, base_branch: str = "main") -> str:
        """Create a new branch. Returns branch URL."""
        pass
