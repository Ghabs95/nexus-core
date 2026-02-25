"""Audit query protocol for backend-agnostic event querying.

Provides a pluggable interface so callers (alerting, health check, reports)
can query audit events without knowing whether the backend is Loki or local
JSONL files.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AuditQueryProvider(Protocol):
    """Protocol for querying audit events."""

    def count_events(self, event_types: set[str], since_hours: int) -> int:
        """Count audit events matching *any* of the given types.

        Args:
            event_types: Set of event type strings to match.
            since_hours: Only consider events within this many hours.

        Returns:
            Total matching event count.
        """
        ...

    def get_events(self, since_hours: int) -> list[dict[str, Any]]:
        """Return all audit events within the time window.

        Each event is a dict with at least ``event_type`` and ``timestamp``.

        Args:
            since_hours: Only return events within this many hours.

        Returns:
            List of event dicts, sorted by timestamp (oldest first).
        """
        ...
