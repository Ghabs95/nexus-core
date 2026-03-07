"""Git platform adapters."""

from nexus.adapters.git.base import Comment, GitPlatform, Issue, PullRequest
from nexus.adapters.git.factory import get_git_platform_transport, resolve_git_platform_class
from nexus.adapters.git.github import GitHubPlatform
from nexus.adapters.git.github_cli import GitHubPlatform as GitHubCLIPlatform
from nexus.adapters.git.gitlab import GitLabPlatform
from nexus.adapters.git.gitlab_cli import GitLabCLIPlatform

__all__ = [
    "GitPlatform",
    "Issue",
    "PullRequest",
    "Comment",
    "GitHubPlatform",
    "GitHubCLIPlatform",
    "GitLabPlatform",
    "GitLabCLIPlatform",
    "get_git_platform_transport",
    "resolve_git_platform_class",
]
