"""Git platform adapter selection helpers."""

import os
from typing import Type

from nexus.adapters.git.base import GitPlatform
from nexus.adapters.git.github import GitHubPlatform
from nexus.adapters.git.github_cli import GitHubPlatform as GitHubCLIPlatform
from nexus.adapters.git.gitlab import GitLabPlatform
from nexus.adapters.git.gitlab_cli import GitLabCLIPlatform

VALID_GIT_TRANSPORTS = {"api", "cli"}
DEFAULT_GIT_TRANSPORT = "api"


def get_git_platform_transport() -> str:
    value = str(os.getenv("NEXUS_GIT_PLATFORM_TRANSPORT", DEFAULT_GIT_TRANSPORT)).strip().lower()
    if value in VALID_GIT_TRANSPORTS:
        return value
    return DEFAULT_GIT_TRANSPORT


def resolve_git_platform_class(platform_type: str) -> Type[GitPlatform]:
    platform = str(platform_type or "github").strip().lower()
    transport = get_git_platform_transport()
    if platform == "gitlab":
        return GitLabCLIPlatform if transport == "cli" else GitLabPlatform
    return GitHubCLIPlatform if transport == "cli" else GitHubPlatform
