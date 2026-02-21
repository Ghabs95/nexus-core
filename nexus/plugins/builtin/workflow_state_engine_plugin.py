"""Built-in plugin: workflow state engine adapter for issue-centric operations."""

import logging
import os
from typing import Any, Callable, Dict, Optional

from nexus.adapters.storage.file import FileStorage
from nexus.core.workflow import WorkflowDefinition, WorkflowEngine
from nexus.core.models import WorkflowState

logger = logging.getLogger(__name__)


class WorkflowStateEnginePlugin:
    """Adapter around ``WorkflowEngine`` with issue-number centric operations."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.storage_dir: str = self.config.get("storage_dir", "")
        self.engine_factory: Optional[Callable[[], WorkflowEngine]] = self.config.get("engine_factory")
        self.issue_to_workflow_id = self.config.get("issue_to_workflow_id")

    def _get_engine(self) -> WorkflowEngine:
        if callable(self.engine_factory):
            return self.engine_factory()
        if not self.storage_dir:
            raise ValueError("storage_dir is required for workflow-state-engine plugin")
        return WorkflowEngine(storage=FileStorage(base_path=self.storage_dir))

    def _resolve_workflow_id(self, issue_number: str) -> Optional[str]:
        if callable(self.issue_to_workflow_id):
            return self.issue_to_workflow_id(str(issue_number))
        mapping = self.config.get("issue_workflow_map", {})
        if isinstance(mapping, dict):
            return mapping.get(str(issue_number))
        return None

    def _run_callback(self, callback_name: str, *args, **kwargs) -> None:
        callback = self.config.get(callback_name)
        if not callable(callback):
            return
        try:
            callback(*args, **kwargs)
        except Exception as exc:
            logger.warning("Callback %s failed: %s", callback_name, exc)

    @staticmethod
    def _issue_number_as_int(issue_number: str) -> Optional[int]:
        try:
            return int(str(issue_number))
        except Exception:
            return None

    def _resolve_workflow_definition_path(self, project_name: str) -> Optional[str]:
        resolver = self.config.get("workflow_definition_path_resolver")
        if callable(resolver):
            return resolver(project_name)

        project_paths = self.config.get("project_workflow_paths", {})
        if isinstance(project_paths, dict) and project_name in project_paths:
            return project_paths[project_name]

        return self.config.get("workflow_definition_path")

    def _resolve_issue_url(self, issue_number: str) -> Optional[str]:
        resolver = self.config.get("issue_url_resolver")
        if callable(resolver):
            return resolver(issue_number)

        github_repo = self.config.get("github_repo")
        if github_repo:
            return f"https://github.com/{github_repo}/issues/{issue_number}"
        return None

    async def create_workflow_for_issue(
        self,
        issue_number: str,
        issue_title: str,
        project_name: str,
        tier_name: str,
        task_type: str,
        description: str = "",
    ) -> Optional[str]:
        """Create workflow from configured workflow definition path for an issue."""
        workflow_definition_path = self._resolve_workflow_definition_path(project_name)
        if not workflow_definition_path:
            logger.error("No workflow definition path for project '%s'", project_name)
            return None
        if not os.path.exists(workflow_definition_path):
            logger.error("Workflow definition not found at: %s", workflow_definition_path)
            return None

        workflow_type = WorkflowDefinition.normalize_workflow_type(tier_name)
        workflow_id = f"{project_name}-{issue_number}-{tier_name}"
        workflow_name = f"{project_name}/{issue_title}"
        workflow_description = description or f"Workflow for issue #{issue_number}"

        metadata = {
            "issue_number": issue_number,
            "project": project_name,
            "tier": tier_name,
            "task_type": task_type,
            "workflow_type": workflow_type,
            "workflow_definition_path": workflow_definition_path,
        }
        issue_url = self._resolve_issue_url(issue_number)
        if issue_url:
            metadata["github_issue_url"] = issue_url

        workflow = WorkflowDefinition.from_yaml(
            workflow_definition_path,
            workflow_id=workflow_id,
            name_override=workflow_name,
            description_override=workflow_description,
            metadata=metadata,
            workflow_type=workflow_type,
        )

        try:
            engine = self._get_engine()
            await engine.create_workflow(workflow)
            mapper = self.config.get("issue_to_workflow_map_setter")
            if callable(mapper):
                mapper(issue_number, workflow_id)
            logger.info("Created workflow %s for issue #%s", workflow_id, issue_number)
            return workflow_id
        except Exception as exc:
            logger.error("Failed to create workflow for issue #%s: %s", issue_number, exc)
            return None

    async def start_workflow(self, workflow_id: str) -> bool:
        """Start workflow by workflow id."""
        try:
            engine = self._get_engine()
            await engine.start_workflow(workflow_id)
            logger.info("Started workflow %s", workflow_id)
            return True
        except Exception as exc:
            logger.error("Failed to start workflow %s: %s", workflow_id, exc)
            return False

    async def pause_workflow(self, issue_number: str, reason: str = "User requested") -> bool:
        """Pause workflow mapped to issue number."""
        workflow_id = self._resolve_workflow_id(issue_number)
        if not workflow_id:
            logger.warning("No workflow mapping found for issue #%s", issue_number)
            return False
        try:
            engine = self._get_engine()
            await engine.pause_workflow(workflow_id)
            logger.info("Paused workflow %s for issue #%s: %s", workflow_id, issue_number, reason)
            return True
        except Exception as exc:
            logger.error("Failed to pause workflow for issue #%s: %s", issue_number, exc)
            return False

    async def resume_workflow(self, issue_number: str) -> bool:
        """Resume workflow mapped to issue number."""
        workflow_id = self._resolve_workflow_id(issue_number)
        if not workflow_id:
            logger.warning("No workflow mapping found for issue #%s", issue_number)
            return False
        try:
            engine = self._get_engine()
            await engine.resume_workflow(workflow_id)
            logger.info("Resumed workflow %s for issue #%s", workflow_id, issue_number)
            return True
        except Exception as exc:
            logger.error("Failed to resume workflow for issue #%s: %s", issue_number, exc)
            return False

    async def get_workflow_status(self, issue_number: str) -> Optional[Dict[str, Any]]:
        """Return status payload for workflow mapped to issue number."""
        workflow_id = self._resolve_workflow_id(issue_number)
        if not workflow_id:
            return None
        try:
            engine = self._get_engine()
            workflow = await engine.get_workflow(workflow_id)
            if not workflow:
                return None

            current_step = workflow.steps[workflow.current_step]
            return {
                "workflow_id": workflow.id,
                "name": workflow.name,
                "state": workflow.state.value,
                "current_step": workflow.current_step + 1,
                "total_steps": len(workflow.steps),
                "current_step_name": current_step.name,
                "current_agent": current_step.agent.display_name,
                "created_at": workflow.created_at.isoformat() if workflow.created_at else None,
                "updated_at": workflow.updated_at.isoformat() if workflow.updated_at else None,
                "metadata": workflow.metadata,
            }
        except Exception as exc:
            logger.error("Failed to read workflow status for issue #%s: %s", issue_number, exc)
            return None

    async def approve_step(self, issue_number: str, approved_by: str) -> bool:
        """Approve pending step for workflow mapped to issue number."""
        workflow_id = self._resolve_workflow_id(issue_number)
        if not workflow_id:
            logger.warning("No workflow mapping found for issue #%s", issue_number)
            return False
        try:
            engine = self._get_engine()
            await engine.approve_step(workflow_id, approved_by=approved_by)
            self._run_callback("clear_pending_approval", issue_number)
            issue_num_int = self._issue_number_as_int(issue_number)
            if issue_num_int is not None:
                self._run_callback(
                    "audit_log",
                    issue_num_int,
                    "APPROVAL_GRANTED",
                    f"by {approved_by}",
                )
            logger.info("Approved workflow step for %s by %s", workflow_id, approved_by)
            return True
        except Exception as exc:
            logger.error("Failed to approve workflow for issue #%s: %s", issue_number, exc)
            return False

    async def deny_step(self, issue_number: str, denied_by: str, reason: str) -> bool:
        """Deny pending step for workflow mapped to issue number."""
        workflow_id = self._resolve_workflow_id(issue_number)
        if not workflow_id:
            logger.warning("No workflow mapping found for issue #%s", issue_number)
            return False
        try:
            engine = self._get_engine()
            await engine.deny_step(workflow_id, denied_by=denied_by, reason=reason)
            self._run_callback("clear_pending_approval", issue_number)
            issue_num_int = self._issue_number_as_int(issue_number)
            if issue_num_int is not None:
                self._run_callback(
                    "audit_log",
                    issue_num_int,
                    "APPROVAL_DENIED",
                    f"by {denied_by}",
                )
            logger.info("Denied workflow step for %s by %s", workflow_id, denied_by)
            return True
        except Exception as exc:
            logger.error("Failed to deny workflow for issue #%s: %s", issue_number, exc)
            return False

    async def complete_step_for_issue(
        self,
        issue_number: str,
        completed_agent_type: str,
        outputs: Dict[str, Any],
    ) -> Optional[Any]:
        """Mark the current running step for *issue_number* as complete.

        Locates the RUNNING step whose ``agent.name`` matches
        *completed_agent_type*, then calls
        ``WorkflowEngine.complete_step()`` to advance the workflow.
        Router steps are evaluated automatically, handling loops and
        conditional branches.

        Args:
            issue_number: GitHub issue number (or any issue id).
            completed_agent_type: The ``agent_type`` of the step that just
                finished (matches the agent's own completion summary field).
            outputs: Structured outputs from the completion summary to record
                against the step and expose to subsequent route conditions.

        Returns:
            The updated :class:`~nexus.core.models.Workflow` (inspect
            ``.state`` and ``.active_agent_type`` for next steps), or
            ``None`` when no workflow is mapped to *issue_number* or the
            workflow cannot be loaded from storage.
        """
        from nexus.core.models import StepStatus  # local import to avoid circular

        workflow_id = self._resolve_workflow_id(issue_number)
        if not workflow_id:
            logger.debug("complete_step_for_issue: no workflow mapping for issue #%s", issue_number)
            return None

        engine = self._get_engine()
        workflow = await engine.get_workflow(workflow_id)
        if not workflow:
            logger.debug(
                "complete_step_for_issue: workflow %s not found (issue #%s)",
                workflow_id,
                issue_number,
            )
            return None

        if workflow.state == WorkflowState.PENDING:
            try:
                await engine.start_workflow(workflow_id)
                workflow = await engine.get_workflow(workflow_id)
            except Exception as exc:
                logger.warning(
                    "complete_step_for_issue: failed to auto-start pending workflow %s "
                    "(issue #%s): %s",
                    workflow_id,
                    issue_number,
                    exc,
                )
                return workflow

        # Find the RUNNING step whose agent_type matches the completed agent
        running_step = None
        for step in workflow.steps:
            if step.status == StepStatus.RUNNING and step.agent.name == completed_agent_type:
                running_step = step
                break

        if not running_step:
            active_agent = workflow.active_agent_type
            logger.error(
                "complete_step_for_issue: completion mismatch for issue #%s: "
                "completed_agent=%s, active_agent=%s",
                issue_number,
                completed_agent_type,
                active_agent,
            )
            raise ValueError(
                f"Completion agent mismatch for issue #{issue_number}: "
                f"completed_agent={completed_agent_type}, active_agent={active_agent}"
            )

        if not running_step:
            logger.warning(
                "complete_step_for_issue: no RUNNING step in workflow %s (issue #%s); "
                "returning workflow unchanged",
                workflow_id,
                issue_number,
            )
            return workflow

        return await engine.complete_step(
            workflow_id=workflow_id,
            step_num=running_step.step_num,
            outputs=outputs,
        )

    async def request_approval_gate(
        self,
        workflow_id: str,
        issue_number: str,
        step_num: int,
        step_name: str,
        agent_name: str,
        approvers,
        approval_timeout: int,
        project: str = "nexus",
    ) -> bool:
        """Persist and notify about a pending approval gate."""
        self._run_callback(
            "set_pending_approval",
            issue_num=issue_number,
            step_num=step_num,
            step_name=step_name,
            approvers=approvers,
            approval_timeout=approval_timeout,
        )

        issue_num_int = self._issue_number_as_int(issue_number)
        if issue_num_int is not None:
            self._run_callback(
                "audit_log",
                issue_num_int,
                "APPROVAL_REQUESTED",
                f"step {step_num} ({step_name}), approvers={approvers}",
            )

        self._run_callback(
            "notify_approval_required",
            issue_number=issue_number,
            step_num=step_num,
            step_name=step_name,
            agent=agent_name,
            approvers=approvers,
            project=project,
        )

        logger.info(
            "Approval gate requested for workflow %s issue #%s step %s (%s)",
            workflow_id,
            issue_number,
            step_num,
            step_name,
        )
        return True


def register_plugins(registry) -> None:
    """Register built-in workflow state engine plugin."""
    from nexus.plugins import PluginKind

    registry.register_factory(
        kind=PluginKind.INPUT_ADAPTER,
        name="workflow-state-engine",
        version="0.1.0",
        factory=lambda config: WorkflowStateEnginePlugin(config),
        description="Workflow state adapter for issue-based pause/resume/status/approval operations",
    )
