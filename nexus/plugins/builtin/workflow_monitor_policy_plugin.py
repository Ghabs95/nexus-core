"""Built-in plugin: workflow monitor policy (platform-neutral)."""

import re
from typing import Any, Callable, Optional


class WorkflowMonitorPolicyPlugin:
    """Workflow policy for issue discovery, comments, PR lookup, and repo resolution."""

    def __init__(self, config: Optional[dict[str, Any]] = None):
        self.config = config or {}

    def _callback(self, name: str) -> Optional[Callable[..., Any]]:
        callback = self.config.get(name)
        return callback if callable(callback) else None

    @staticmethod
    def _extract_label_names(issue: Any) -> set[str]:
        labels_obj: Any
        if isinstance(issue, dict):
            labels_obj = issue.get("labels", [])
        else:
            labels_obj = getattr(issue, "labels", [])

        label_names: set[str] = set()
        for label in labels_obj or []:
            if isinstance(label, dict):
                value = label.get("name")
                if value:
                    label_names.add(str(value))
            elif isinstance(label, str):
                if label:
                    label_names.add(label)
        return label_names

    @staticmethod
    def _extract_issue_number(issue: Any) -> Optional[str]:
        if isinstance(issue, dict):
            number = issue.get("number")
        else:
            number = getattr(issue, "number", None)
        return str(number) if number is not None else None

    def list_workflow_issue_numbers(
        self,
        *,
        repo: str,
        workflow_labels: set[str],
        limit: int = 100,
    ) -> list[str]:
        list_open_issues = self._callback("list_open_issues")
        list_issues = self._callback("list_issues")

        if list_open_issues:
            issues = list_open_issues(
                repo=repo,
                limit=limit,
                workflow_labels=workflow_labels,
            )
        elif list_issues:
            issues = list_issues(
                repo=repo,
                state="open",
                limit=limit,
                fields=["number", "labels"],
            )
        else:
            raise RuntimeError("list_open_issues or list_issues callback is required")

        issue_numbers: list[str] = []
        for issue in issues:
            label_names = self._extract_label_names(issue)
            if label_names.intersection(workflow_labels):
                number = self._extract_issue_number(issue)
                if number is not None:
                    issue_numbers.append(number)

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
    """Register built-in workflow monitor policy plugin."""
    from nexus.plugins import PluginKind

    registry.register_factory(
        kind=PluginKind.INPUT_ADAPTER,
        name="workflow-monitor-policy",
        version="0.1.0",
        factory=lambda config: WorkflowMonitorPolicyPlugin(config),
        description="Workflow monitor policy for issue/comments/PR/repo resolution",
    )
