"""Persistent idempotency ledger for workflow step completion events.

Enforces uniqueness per the composite key::

    (issue_id, step_num, agent_type, event_id)

where *event_id* is a caller-supplied identifier — typically a GitHub comment
ID or a hash of the completion file path/contents.  This prevents reprocessing
of stale or duplicate signals before ``complete_step_for_issue`` is called.
"""

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from typing import Set

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IdempotencyKey:
    """Composite key identifying a single workflow step completion event."""

    issue_id: str
    step_num: int
    agent_type: str
    event_id: str  # GitHub comment ID or file-path/content hash

    def as_string(self) -> str:
        """Return a stable hex digest of the composite key."""
        raw = f"{self.issue_id}:{self.step_num}:{self.agent_type}:{self.event_id}"
        return hashlib.sha256(raw.encode()).hexdigest()


class IdempotencyLedger:
    """File-backed ledger for deduplicating workflow step completion events.

    Usage::

        ledger = IdempotencyLedger("/path/to/.nexus/idempotency_ledger.json")
        key = IdempotencyKey(issue_id="42", step_num=3,
                             agent_type="developer", event_id="comment-789")
        if ledger.is_duplicate(key):
            logger.info("Duplicate event — skipping")
            return
        # ... process event ...
        ledger.record(key)
    """

    def __init__(self, ledger_path: str) -> None:
        self._path = ledger_path
        self._seen: Set[str] = set()
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_duplicate(self, key: IdempotencyKey) -> bool:
        """Return ``True`` if *key* has already been recorded."""
        return key.as_string() in self._seen

    def record(self, key: IdempotencyKey) -> None:
        """Mark *key* as processed and persist the ledger."""
        digest = key.as_string()
        if digest not in self._seen:
            self._seen.add(digest)
            self._save()

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list):
                self._seen = set(data)
        except Exception as exc:
            logger.warning("IdempotencyLedger: failed to load %s: %s", self._path, exc)

    def _save(self) -> None:
        try:
            parent = os.path.dirname(self._path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as fh:
                json.dump(sorted(self._seen), fh)
        except Exception as exc:
            logger.warning("IdempotencyLedger: failed to save %s: %s", self._path, exc)
