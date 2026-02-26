"""JSON-file implementation of :class:`WorkflowStateStore`.

Reads/writes two sidecar JSON files inside a configurable *base_path*:
- ``workflow_mapping.json``  — issue → workflow-id map
- ``approval_state.json``    — pending approval records
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class FileWorkflowStateStore:
    """Persist workflow state as JSON files on the local filesystem."""

    def __init__(self, base_path: Path) -> None:
        self._base = base_path
        self._mapping_file = base_path / "workflow_mapping.json"
        self._approval_file = base_path / "approval_state.json"

    # ── helpers ──────────────────────────────────────────────────────

    def _read(self, path: Path, default: dict) -> dict:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text()) or default
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read %s: %s", path, exc)
            return default

    def _write(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data, indent=2))
            tmp.replace(path)
        except OSError as exc:
            logger.error("Failed to write %s: %s", path, exc)

    # ── Workflow mapping ────────────────────────────────────────────

    def map_issue(self, issue_num: str, workflow_id: str) -> None:
        data = self._read(self._mapping_file, {})
        data[str(issue_num)] = workflow_id
        self._write(self._mapping_file, data)
        logger.info("Mapped issue #%s -> workflow %s", issue_num, workflow_id)

    def get_workflow_id(self, issue_num: str) -> str | None:
        data = self._read(self._mapping_file, {})
        return data.get(str(issue_num))

    def remove_mapping(self, issue_num: str) -> None:
        data = self._read(self._mapping_file, {})
        data.pop(str(issue_num), None)
        self._write(self._mapping_file, data)
        logger.info("Removed workflow mapping for issue #%s", issue_num)

    def load_all_mappings(self) -> dict[str, str]:
        return self._read(self._mapping_file, {})

    # ── Approval gate ───────────────────────────────────────────────

    def set_pending_approval(
        self,
        issue_num: str,
        step_num: int,
        step_name: str,
        approvers: list[str],
        approval_timeout: int,
    ) -> None:
        data = self._read(self._approval_file, {})
        data[str(issue_num)] = {
            "step_num": step_num,
            "step_name": step_name,
            "approvers": approvers,
            "approval_timeout": approval_timeout,
            "requested_at": time.time(),
        }
        self._write(self._approval_file, data)
        logger.info(
            "Set pending approval for issue #%s step %d (%s)",
            issue_num,
            step_num,
            step_name,
        )

    def clear_pending_approval(self, issue_num: str) -> None:
        data = self._read(self._approval_file, {})
        data.pop(str(issue_num), None)
        self._write(self._approval_file, data)
        logger.info("Cleared pending approval for issue #%s", issue_num)

    def get_pending_approval(self, issue_num: str) -> dict | None:
        data = self._read(self._approval_file, {})
        return data.get(str(issue_num))

    def load_all_approvals(self) -> dict[str, dict]:
        return self._read(self._approval_file, {})
