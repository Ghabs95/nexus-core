"""Loki Analytics Adapter.

Connects to a Loki instance to execute LogQL queries for workflow analytics,
replacing the need for local file-based metric parsing.

Implements :class:`AuditQueryProvider` so callers (alerting, health check,
reports) can query audit events via Loki instead of scanning local files.
"""

import json
import logging
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Any

from nexus.core.analytics import AgentMetrics, SystemMetrics

logger = logging.getLogger(__name__)


class LokiAnalyticsAdapter:
    """Queries Loki for workflow metrics using LogQL.

    Also satisfies the :class:`AuditQueryProvider` protocol.
    """

    def __init__(self, loki_url: str = "http://localhost:3100"):
        """
        Initialize the Loki adapter.

        Args:
            loki_url: Base URL of the Loki instance (default: http://localhost:3100)
        """
        self.loki_url = loki_url.rstrip("/")
        self.query_range_endpoint = f"{self.loki_url}/loki/api/v1/query_range"
        self.query_endpoint = f"{self.loki_url}/loki/api/v1/query"

    # ------------------------------------------------------------------
    # Low-level query helpers
    # ------------------------------------------------------------------

    def _query_range(self, query: str, lookback_days: int = 30) -> list[dict[str, Any]]:
        """Execute a LogQL ``query_range`` against Loki.

        Returns:
            List of result dicts from ``data.result``.
        """
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(days=lookback_days)

        params = {
            "query": query,
            "start": str(int(start_time.timestamp() * 1e9)),
            "end": str(int(end_time.timestamp() * 1e9)),
            "limit": "5000",
        }
        return self._http_get(self.query_range_endpoint, params)

    def _query_instant(self, query: str) -> list[dict[str, Any]]:
        """Execute an instant LogQL query (``/query``)."""
        params = {"query": query}
        return self._http_get(self.query_endpoint, params)

    def _http_get(self, endpoint: str, params: dict[str, str]) -> list[dict[str, Any]]:
        query_string = urllib.parse.urlencode(params)
        url = f"{endpoint}?{query_string}"

        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status != 200:
                    logger.error("Loki API returned %s", response.status)
                    return []

                data = json.loads(response.read().decode("utf-8"))
                if data.get("status") == "success":
                    return data.get("data", {}).get("result", [])
                logger.error("Loki query failed: %s", data)
                return []
        except Exception as e:
            logger.error("Failed to query Loki: %s", e)
            return []

    def _extract_scalar(self, results: list[dict[str, Any]]) -> int:
        """Extract a single scalar integer from a Loki ``vector`` result."""
        if not results:
            return 0
        try:
            # Instant query returns [timestamp, "value"] in result[0]["value"]
            val = results[0].get("value", [None, "0"])
            return int(float(val[1]))
        except (IndexError, ValueError, TypeError):
            return 0

    # ------------------------------------------------------------------
    # AuditQueryProvider interface
    # ------------------------------------------------------------------

    def count_events(self, event_types: set[str], since_hours: int) -> int:
        """Count audit events matching *any* of the given types via LogQL."""
        if not event_types:
            return 0

        # Build a regex alternation for the event types
        types_re = "|".join(event_types)
        lookback = f"{since_hours}h"
        query = (
            f'sum(count_over_time({{app="nexus"}} '
            f'| json | event_type=~"{types_re}" [{lookback}]))'
        )
        results = self._query_instant(query)
        return self._extract_scalar(results)

    def get_events(self, since_hours: int) -> list[dict[str, Any]]:
        """Return all audit events within the window from Loki."""
        lookback_days = max(int(since_hours / 24), 1)
        query = '{app="nexus"} | json | loki_type="audit_event"'
        raw_results = self._query_range(query, lookback_days=lookback_days)

        events: list[dict[str, Any]] = []
        for stream in raw_results:
            for _ts, line in stream.get("values", []):
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        events.sort(key=lambda e: e.get("timestamp", ""))
        return events

    # ------------------------------------------------------------------
    # System-level analytics (for /stats command and dashboards)
    # ------------------------------------------------------------------

    def get_system_metrics(self, lookback_days: int = 30) -> SystemMetrics:
        """Fetch overall system metrics from Loki via LogQL aggregated queries."""
        lookback = f"{lookback_days * 24}h"

        def _count(event_type: str) -> int:
            q = (
                f'sum(count_over_time({{app="nexus"}} '
                f'| json | event_type="{event_type}" [{lookback}]))'
            )
            return self._extract_scalar(self._query_instant(q))

        total = _count("WORKFLOW_STARTED")
        completed = _count("WORKFLOW_COMPLETED")
        failed = _count("AGENT_FAILED")
        timeouts = _count("AGENT_TIMEOUT_KILL")
        retries = _count("AGENT_RETRY")

        return SystemMetrics(
            total_workflows=total,
            completed_workflows=completed,
            failed_workflows=failed,
            total_timeouts=timeouts,
            total_retries=retries,
            completion_rate=completed / total if total else 0.0,
        )

    def get_agent_leaderboard(self, lookback_days: int = 30, top_n: int = 10) -> list[AgentMetrics]:
        """Fetch top performing agents ranked by launch count."""
        lookback = f"{lookback_days * 24}h"
        query = (
            f"sum by (agent_name) "
            f'(count_over_time({{app="nexus"}} '
            f'| json | event_type="AGENT_LAUNCHED" [{lookback}]))'
        )
        results = self._query_instant(query)

        agents: list[AgentMetrics] = []
        for r in results:
            name = r.get("metric", {}).get("agent_name", "unknown")
            try:
                launches = int(float(r.get("value", [None, "0"])[1]))
            except (IndexError, ValueError, TypeError):
                launches = 0
            agents.append(AgentMetrics(agent_name=name, launches=launches))

        agents.sort(key=lambda a: a.launches, reverse=True)
        return agents[:top_n]

    def format_stats_report(self, lookback_days: int = 30) -> str:
        """Generate formatted report sourced from Loki."""
        m = self.get_system_metrics(lookback_days)
        agents = self.get_agent_leaderboard(lookback_days, top_n=5)

        lines = [
            "📊 **Nexus System Analytics** (Loki)",
            f"Period: last {lookback_days} days",
            "",
            f"Workflows: {m.total_workflows} total, "
            f"{m.completed_workflows} completed, "
            f"{m.failed_workflows} failed",
            f"Completion rate: {m.completion_rate:.0%}",
            f"Timeouts: {m.total_timeouts}  |  Retries: {m.total_retries}",
        ]

        if agents:
            lines.append("")
            lines.append("🏆 **Top Agents**")
            for i, a in enumerate(agents, 1):
                lines.append(f"  {i}. {a.agent_name}: {a.launches} launches")

        return "\n".join(lines)
