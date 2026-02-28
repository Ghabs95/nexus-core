"""WorkflowStateStore adapter backed by StorageBackend implementations."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from nexus.adapters.storage.base import StorageBackend


class StorageWorkflowStateStore:
    """Synchronous WorkflowStateStore facade over async StorageBackend methods."""

    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage

    @staticmethod
    def _run(coro):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        # Support sync call-sites invoked from async contexts.
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result()

    def map_issue(self, issue_num: str, workflow_id: str) -> None:
        self._run(self._storage.map_issue_to_workflow(issue_num, workflow_id))

    def get_workflow_id(self, issue_num: str) -> str | None:
        return self._run(self._storage.get_workflow_id_for_issue(issue_num))

    def remove_mapping(self, issue_num: str) -> None:
        self._run(self._storage.remove_issue_workflow_mapping(issue_num))

    def load_all_mappings(self) -> dict[str, str]:
        return self._run(self._storage.load_issue_workflow_mappings())

    def set_pending_approval(
        self,
        issue_num: str,
        step_num: int,
        step_name: str,
        approvers: list[str],
        approval_timeout: int,
    ) -> None:
        self._run(
            self._storage.set_pending_workflow_approval(
                issue_num=issue_num,
                step_num=step_num,
                step_name=step_name,
                approvers=approvers,
                approval_timeout=approval_timeout,
            )
        )

    def clear_pending_approval(self, issue_num: str) -> None:
        self._run(self._storage.clear_pending_workflow_approval(issue_num))

    def get_pending_approval(self, issue_num: str) -> dict[str, Any] | None:
        return self._run(self._storage.get_pending_workflow_approval(issue_num))

    def load_all_approvals(self) -> dict[str, dict[str, Any]]:
        return self._run(self._storage.load_pending_workflow_approvals())
