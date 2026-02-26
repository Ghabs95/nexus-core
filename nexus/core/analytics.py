"""Analytics module for parsing audit logs and generating workflow statistics.

Provides insights into:
- Issue completion rates
- Agent performance metrics
- Timeout and retry frequencies
- Workflow duration analysis
"""

import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from nexus.core.models import AuditEvent

logger = logging.getLogger(__name__)


@dataclass
class WorkflowMetrics:
    """Metrics for a single workflow execution."""

    workflow_id: str
    issue_num: int | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    duration_seconds: float | None = None
    agents_launched: int = 0
    timeouts: int = 0
    retries: int = 0
    failures: int = 0
    workflow_tier: str | None = None
    completed: bool = False


@dataclass
class AgentMetrics:
    """Performance metrics for a specific agent."""

    agent_name: str
    launches: int = 0
    timeouts: int = 0
    retries: int = 0
    failures: int = 0
    successes: int = 0
    avg_duration_seconds: float | None = None


@dataclass
class SystemMetrics:
    """Overall system performance metrics."""

    total_workflows: int = 0
    completed_workflows: int = 0
    active_workflows: int = 0
    failed_workflows: int = 0
    total_timeouts: int = 0
    total_retries: int = 0
    completion_rate: float = 0.0
    avg_workflow_duration_hours: float | None = None
    issues_per_tier: dict[str, int] = field(default_factory=dict)


class MetricsEngine:
    """Engine for computing metrics from audit events."""

    def __init__(self):
        """Initialize engine."""
        self.workflow_metrics: dict[str, WorkflowMetrics] = {}
        self.agent_metrics: dict[str, AgentMetrics] = defaultdict(
            lambda: AgentMetrics(agent_name="")
        )

    def process_events(self, events: list[AuditEvent]) -> None:
        """Process a list of audit events to populate metrics.

        Args:
            events: List of AuditEvent objects, typically ordered by time.
        """
        for evt in events:
            workflow_id = evt.workflow_id
            event_type = evt.event_type
            data = evt.data or {}
            timestamp = evt.timestamp

            # Initialize workflow metrics if needed
            if workflow_id not in self.workflow_metrics:
                issue_num = None
                if isinstance(data, dict):
                    issue_num = data.get("issue_number")
                if issue_num is None:
                    # Fallback: parse from workflow_id (format: project-N-tier)
                    match = re.search(r"-(\d+)-", workflow_id)
                    if match:
                        issue_num = int(match.group(1))

                self.workflow_metrics[workflow_id] = WorkflowMetrics(
                    workflow_id=workflow_id, issue_num=issue_num
                )

            wm = self.workflow_metrics[workflow_id]
            details = data.get("details", "") if isinstance(data, dict) else ""

            if event_type in ("WORKFLOW_STARTED", "WORKFLOW_CREATED"):
                if wm.start_time is None:
                    wm.start_time = timestamp

                tier_match = re.search(r"tier[:\s]+(\w+)", str(details), re.IGNORECASE)
                if tier_match:
                    wm.workflow_tier = tier_match.group(1)
                elif not wm.workflow_tier:
                    for tier in ("full", "shortened", "fast-track"):
                        if workflow_id.endswith(f"-{tier}"):
                            wm.workflow_tier = tier
                            break

            elif event_type == "AGENT_LAUNCHED":
                wm.agents_launched += 1
                agent_match = re.search(r"@?(\w+)", str(details))
                if agent_match:
                    agent_name = agent_match.group(1)
                    self.agent_metrics[agent_name].agent_name = agent_name
                    self.agent_metrics[agent_name].launches += 1

            elif event_type == "AGENT_TIMEOUT_KILL":
                wm.timeouts += 1
                agent_match = re.search(r"@?(\w+)", str(details))
                if agent_match:
                    self.agent_metrics[agent_match.group(1)].timeouts += 1

            elif event_type == "AGENT_RETRY":
                wm.retries += 1
                agent_match = re.search(r"@?(\w+)", str(details))
                if agent_match:
                    self.agent_metrics[agent_match.group(1)].retries += 1

            elif event_type == "AGENT_FAILED":
                wm.failures += 1
                agent_match = re.search(r"@?(\w+)", str(details))
                if agent_match:
                    self.agent_metrics[agent_match.group(1)].failures += 1

            elif event_type == "WORKFLOW_COMPLETED":
                wm.completed = True
                wm.end_time = timestamp
                if wm.start_time and wm.end_time:
                    wm.duration_seconds = (wm.end_time - wm.start_time).total_seconds()

    def get_system_metrics(self) -> SystemMetrics:
        """Calculate overall system metrics from processed data."""
        metrics = SystemMetrics()

        metrics.total_workflows = len(self.workflow_metrics)
        metrics.completed_workflows = sum(
            1 for wm in self.workflow_metrics.values() if wm.completed
        )
        metrics.failed_workflows = sum(
            1 for wm in self.workflow_metrics.values() if wm.failures > 0 and not wm.completed
        )
        metrics.active_workflows = (
            metrics.total_workflows - metrics.completed_workflows - metrics.failed_workflows
        )

        if metrics.total_workflows > 0:
            metrics.completion_rate = (metrics.completed_workflows / metrics.total_workflows) * 100

        metrics.total_timeouts = sum(wm.timeouts for wm in self.workflow_metrics.values())
        metrics.total_retries = sum(wm.retries for wm in self.workflow_metrics.values())

        # Calculate average workflow duration (only for completed workflows)
        completed_durations = [
            wm.duration_seconds
            for wm in self.workflow_metrics.values()
            if wm.completed and isinstance(wm.duration_seconds, (int, float))
        ]
        if completed_durations:
            avg_seconds: float = sum(completed_durations) / len(completed_durations)
            metrics.avg_workflow_duration_hours = avg_seconds / 3600

        # Count issues per tier
        tier_counter: Counter[str] = Counter()
        for wm in self.workflow_metrics.values():
            tier = wm.workflow_tier
            if tier:
                tier_counter[tier] += 1
        metrics.issues_per_tier = dict(tier_counter)

        return metrics

    def get_agent_leaderboard(self, top_n: int = 10) -> list[AgentMetrics]:
        """Get top performing agents ranked by activity."""
        agent_list: list[AgentMetrics] = []
        for agent_name, metrics in self.agent_metrics.items():
            if metrics.launches > 0:
                metrics.successes = max(0, metrics.launches - metrics.timeouts - metrics.failures)
                agent_list.append(metrics)

        agent_list.sort(key=lambda a: a.launches, reverse=True)
        return list(agent_list[0 : int(top_n)])

    def format_stats_report(self, lookback_days: int = 30) -> str:
        """Generate a formatted Markdown report."""
        system_metrics = self.get_system_metrics()
        agent_leaderboard = self.get_agent_leaderboard(top_n=5)

        report = "📊 **Nexus System Analytics**\n"
        report += "=" * 40 + "\n\n"

        report += "**📈 Overall Performance:**\n"
        report += f"• Total Workflows: {system_metrics.total_workflows}\n"
        report += f"• ✅ Completed: {system_metrics.completed_workflows}\n"
        report += f"• 🔄 Active: {system_metrics.active_workflows}\n"
        report += f"• ❌ Failed: {system_metrics.failed_workflows}\n"
        report += f"• Completion Rate: {system_metrics.completion_rate:.1f}%\n"

        if system_metrics.avg_workflow_duration_hours:
            report += f"• Avg Workflow Time: {system_metrics.avg_workflow_duration_hours:.1f}h\n"

        report += "\n**⚙️ Reliability:**\n"
        report += f"• Total Timeouts: {system_metrics.total_timeouts}\n"
        report += f"• Total Retries: {system_metrics.total_retries}\n"

        if system_metrics.total_workflows > 0:
            timeout_rate = system_metrics.total_timeouts / system_metrics.total_workflows
            report += f"• Avg Timeouts per Workflow: {timeout_rate:.1f}\n"

        report += "\n"
        if system_metrics.issues_per_tier:
            report += "**🎯 Workflows by Tier:**\n"
            for tier, count in sorted(system_metrics.issues_per_tier.items()):
                emoji = {"full": "🟡", "shortened": "🟠", "fast-track": "🟢"}.get(tier, "⚪")
                report += f"• {emoji} {tier}: {count}\n"
            report += "\n"

        if agent_leaderboard:
            report += "**🤖 Top 5 Most Active Agents:**\n"
            for idx, agent in enumerate(agent_leaderboard, 1):
                success_rate = (agent.successes / agent.launches * 100) if agent.launches > 0 else 0
                report += f"{idx}. **@{agent.agent_name}**\n"
                report += f"   ├ Launches: {agent.launches}\n"
                report += f"   ├ Successes: {agent.successes} ({success_rate:.0f}%)\n"
                if agent.timeouts > 0:
                    report += f"   ├ Timeouts: {agent.timeouts}\n"
                if agent.retries > 0:
                    report += f"   └ Retries: {agent.retries}\n"
                else:
                    report += "   └ Retries: 0\n"
            report += "\n"

        report += "=" * 40 + "\n"
        report += f"_Data from last {lookback_days} days_"

        return report
