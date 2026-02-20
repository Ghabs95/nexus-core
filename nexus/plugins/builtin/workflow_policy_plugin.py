"""Built-in plugin: workflow policy (finalization + notifications)."""

import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


class WorkflowPolicyPlugin:
    """Workflow policy for notification composition and completion finalization."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}

    def _callback(self, name: str) -> Optional[Callable[..., Any]]:
        callback = self.config.get(name)
        return callback if callable(callback) else None

    def build_transition_message(
        self,
        *,
        issue_number: str,
        completed_agent: str,
        next_agent: str,
        repo: str,
    ) -> str:
        return (
            "🔗 **Agent Transition**\n\n"
            f"Issue: #{issue_number}\n"
            f"Completed: `{completed_agent}`\n"
            f"Launching: `{next_agent}`\n\n"
            f"🔗 https://github.com/{repo}/issues/{issue_number}"
        )

    def build_autochain_failed_message(
        self,
        *,
        issue_number: str,
        completed_agent: str,
        next_agent: str,
        repo: str,
    ) -> str:
        return (
            "❌ **Auto-chain Failed**\n\n"
            f"Issue: #{issue_number}\n"
            f"Completed: `{completed_agent}`\n"
            f"Failed to launch: `{next_agent}`\n\n"
            f"🔗 https://github.com/{repo}/issues/{issue_number}"
        )

    def build_workflow_complete_message(
        self,
        *,
        issue_number: str,
        last_agent: str,
        repo: str,
        pr_url: Optional[str] = None,
    ) -> str:
        parts = [
            "✅ **Workflow Complete**\n\n"
            f"Issue: #{issue_number}\n"
            f"Last agent: `{last_agent}`\n"
        ]
        if pr_url:
            parts.append(f"PR: {pr_url}\n")
        parts.append(f"\n🔗 https://github.com/{repo}/issues/{issue_number}")
        return "".join(parts)

    def _resolve_git_dir(self, project_name: str) -> Optional[str]:
        resolver = self._callback("resolve_git_dir")
        if not resolver:
            return None
        return resolver(project_name)

    def _create_pr(
        self,
        *,
        repo: str,
        repo_dir: str,
        issue_number: str,
        last_agent: str,
    ) -> Optional[str]:
        creator = self._callback("create_pr_from_changes")
        if not creator:
            return None

        title = f"fix: resolve #{issue_number}"
        body = (
            f"Automated PR for issue #{issue_number}.\n\n"
            f"Workflow completed by Nexus agent chain.\n"
            f"Last agent: `{last_agent}`"
        )

        pr_url = creator(
            repo=repo,
            repo_dir=repo_dir,
            issue_number=str(issue_number),
            title=title,
            body=body,
            issue_repo=repo,
        )
        return str(pr_url) if pr_url else None

    def _close_issue(self, *, repo: str, issue_number: str, last_agent: str, pr_url: Optional[str]) -> bool:
        closer = self._callback("close_issue")
        if not closer:
            return False

        close_comment = (
            "✅ Workflow completed. All agent steps finished successfully.\n"
            f"Last agent: `{last_agent}`"
        )
        if pr_url:
            close_comment += f"\nPR: {pr_url}"

        return bool(closer(repo=repo, issue_number=str(issue_number), comment=close_comment))

    def _notify(self, *, repo: str, issue_number: str, last_agent: str, pr_url: Optional[str]) -> None:
        notifier = self._callback("send_notification")
        if not notifier:
            return

        builder = self._callback("build_workflow_complete_message") or self.build_workflow_complete_message
        message = builder(
            issue_number=str(issue_number),
            last_agent=last_agent,
            repo=repo,
            pr_url=pr_url,
        )
        notifier(message)

    def finalize_workflow(
        self,
        *,
        issue_number: str,
        repo: str,
        last_agent: str,
        project_name: str,
    ) -> Dict[str, Any]:
        """Finalize workflow and return outcome summary."""
        result: Dict[str, Any] = {
            "pr_url": None,
            "issue_closed": False,
            "notification_sent": False,
        }

        if project_name:
            git_dir = self._resolve_git_dir(project_name)
            if git_dir:
                try:
                    result["pr_url"] = self._create_pr(
                        repo=repo,
                        repo_dir=git_dir,
                        issue_number=issue_number,
                        last_agent=last_agent,
                    )
                except Exception as exc:
                    logger.warning("Error creating PR for issue #%s: %s", issue_number, exc)

        try:
            result["issue_closed"] = self._close_issue(
                repo=repo,
                issue_number=issue_number,
                last_agent=last_agent,
                pr_url=result["pr_url"],
            )
        except Exception as exc:
            logger.warning("Error closing issue #%s: %s", issue_number, exc)

        try:
            self._notify(
                repo=repo,
                issue_number=issue_number,
                last_agent=last_agent,
                pr_url=result["pr_url"],
            )
            result["notification_sent"] = self._callback("send_notification") is not None
        except Exception as exc:
            logger.warning("Error sending finalization notification for issue #%s: %s", issue_number, exc)

        return result


def register_plugins(registry) -> None:
    """Register built-in workflow policy plugin."""
    from nexus.plugins import PluginKind

    registry.register_factory(
        kind=PluginKind.INPUT_ADAPTER,
        name="workflow-policy",
        version="0.1.0",
        factory=lambda config: WorkflowPolicyPlugin(config),
        description="Workflow policy for notifications and finalization",
    )
