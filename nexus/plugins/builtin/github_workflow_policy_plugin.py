"""Built-in plugin: GitHub workflow policy."""

import re
from typing import Any, Callable, Optional


class GithubWorkflowPolicyPlugin:
    """GitHub workflow policy for issue discovery, comments, PR lookup, and repo resolution."""

    def __init__(self, config: Optional[dict[str, Any]] = None):
        self.config = config or {}

    def _callback(self, name: str) -> Optional[Callable[..., Any]]:
        callback = self.config.get(name)
        return callback if callable(callback) else None

    def list_workflow_issue_numbers(
        self,
        *,
        repo: str,
        workflow_labels: set[str],
        limit: int = 100,
    ) -> list[str]:
        list_issues = self._callback("list_issues")
        if not list_issues:
            raise RuntimeError("list_issues callback is required")

        issues = list_issues(
            repo=repo,
            state="open",
            limit=limit,
            fields=["number", "labels"],
        )

        issue_numbers: list[str] = []
        for issue in issues:
            labels = issue.get("labels", [])
            label_names = {
                label.get("name")
                for label in labels
                if isinstance(label, dict) and label.get("name")
            }
            if label_names.intersection(workflow_labels):
                number = issue.get("number")
                if number is not None:
                    issue_numbers.append(str(number))

        return issue_numbers

    def get_bot_comments(self, *, repo: str, issue_number: str, bot_author: str) -> list[Any]:
        get_comments = self._callback("get_comments")
        if not get_comments:
            raise RuntimeError("get_comments callback is required")

        comments = get_comments(repo=repo, issue_number=str(issue_number))
        return [comment for comment in comments if getattr(comment, "author", "") == bot_author]

    def find_open_linked_pr(self, *, repo: str, issue_number: str) -> Optional[Any]:
        search_linked_prs = self._callback("search_linked_prs")
        if not search_linked_prs:
            raise RuntimeError("search_linked_prs callback is required")

        prs = search_linked_prs(repo=repo, issue_number=str(issue_number))
        for pr in prs:
            if getattr(pr, "state", "") == "open":
                return pr
        return None

    def resolve_repo_for_issue(
        self,
        *,
        issue_number: str,
        default_repo: str,
        project_workspaces: dict[str, str],
        project_repos: dict[str, str],
    ) -> str:
        """Resolve issue repository from issue body task-file metadata."""
        get_issue = self._callback("get_issue")
        if not get_issue:
            raise RuntimeError("get_issue callback is required")

        try:
            issue = get_issue(repo=default_repo, issue_number=str(issue_number))
            if not issue:
                return default_repo

            body = getattr(issue, "body", "") or ""
            task_file_match = re.search(r"\*\*Task File:\*\*\s*`([^`]+)`", body)
            if not task_file_match:
                return default_repo

            task_file = task_file_match.group(1)
            for project_name, workspace_abs in project_workspaces.items():
                if task_file.startswith(workspace_abs):
                    return project_repos.get(project_name, default_repo)
        except Exception:
            return default_repo

        return default_repo


def register_plugins(registry) -> None:
    """Register built-in GitHub workflow policy plugin."""
    from nexus.plugins import PluginKind

    registry.register_factory(
        kind=PluginKind.INPUT_ADAPTER,
        name="github-workflow-policy",
        version="0.1.0",
        factory=lambda config: GithubWorkflowPolicyPlugin(config),
        description="GitHub workflow policy for issue/comments/PR/repo resolution",
    )
