"""File-based AuditQueryProvider — local-dev fallback.

Scans JSONL audit files on disk (same format written by
:class:`StructuredLogAuditBackend`).  Production deployments should
prefer :class:`LokiAnalyticsAdapter` which queries Loki via LogQL.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class FileAuditQueryProvider:
    """Implements :class:`AuditQueryProvider` by scanning local JSONL files."""

    def __init__(self, audit_dir: str | Path) -> None:
        self._audit_dir = Path(audit_dir)

    # ------------------------------------------------------------------
    # AuditQueryProvider interface
    # ------------------------------------------------------------------

    def count_events(self, event_types: set[str], since_hours: int) -> int:
        """Count events matching *any* of the given types."""
        events = self._scan(since_hours)
        return sum(1 for e in events if e.get("event_type") in event_types)

    def get_events(self, since_hours: int) -> list[dict[str, Any]]:
        """Return all events within the window."""
        return self._scan(since_hours)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _scan(self, since_hours: int) -> list[dict[str, Any]]:
        """Scan ``*.jsonl`` files in the audit directory."""
        if not self._audit_dir.exists():
            return []

        cutoff = datetime.now(UTC) - timedelta(hours=since_hours)
        all_events: list[dict[str, Any]] = []

        for audit_file in self._audit_dir.glob("*.jsonl"):
            try:
                # Quick mtime pre-filter — skip files untouched before cutoff
                if audit_file.stat().st_mtime < cutoff.timestamp():
                    continue

                with open(audit_file) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        ts_str = data.get("timestamp", "")
                        try:
                            ts = datetime.fromisoformat(ts_str)
                            if ts.tzinfo is None:
                                ts = ts.replace(tzinfo=UTC)
                            if ts < cutoff:
                                continue
                        except (ValueError, TypeError):
                            continue

                        all_events.append(data)
            except Exception as exc:
                logger.warning("Error reading audit file %s: %s", audit_file, exc)

        all_events.sort(key=lambda e: e.get("timestamp", ""))
        return all_events
