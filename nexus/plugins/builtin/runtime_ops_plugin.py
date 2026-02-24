"""Built-in plugin: runtime process operations and guard helpers."""

import logging
import re
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


class RuntimeOpsPlugin:
    """Process discovery and termination helpers for issue-linked agent runtimes."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.process_name = self.config.get("process_name", "copilot")
        self.pgrep_timeout = int(self.config.get("pgrep_timeout", 5))
        self.kill_timeout = int(self.config.get("kill_timeout", 5))

    def build_issue_pattern(self, issue_number: str) -> str:
        """Return pgrep regex pattern for issue-linked process detection."""
        return (
            f"{self.process_name}.*issues/{issue_number}[^0-9]|"
            f"{self.process_name}.*issues/{issue_number}$"
        )

    def find_issue_processes(self, issue_number: str) -> list[dict[str, Any]]:
        """Return process matches for an issue number."""
        pattern = self.build_issue_pattern(issue_number)
        try:
            result = subprocess.run(
                ["pgrep", "-af", pattern],
                text=True,
                capture_output=True,
                timeout=self.pgrep_timeout,
                check=False,
            )
        except Exception as exc:
            logger.error("Failed to list processes for issue #%s: %s", issue_number, exc)
            return []

        if not result.stdout:
            return []

        matches: list[dict[str, Any]] = []
        for line in result.stdout.strip().splitlines():
            parts = line.split(None, 1)
            if not parts:
                continue
            pid = self._parse_pid(parts[0])
            if pid is None:
                continue
            command = parts[1] if len(parts) > 1 else ""
            matches.append({"pid": pid, "command": command})
        return matches

    def find_agent_pid_for_issue(self, issue_number: str) -> int | None:
        """Return first PID for issue-linked process, if any."""
        matches = self.find_issue_processes(issue_number)
        if not matches:
            return None
        return matches[0]["pid"]

    def is_issue_process_running(self, issue_number: str) -> bool:
        """Return whether issue-linked process is running."""
        return bool(self.find_issue_processes(issue_number))

    def kill_process(self, pid: int, force: bool = False) -> bool:
        """Kill process by PID."""
        signal = "-9" if force else "-15"
        try:
            subprocess.run(
                ["kill", signal, str(pid)],
                check=True,
                timeout=self.kill_timeout,
                capture_output=True,
                text=True,
            )
            return True
        except Exception as exc:
            logger.error("Failed to kill pid %s (force=%s): %s", pid, force, exc)
            return False

    def stop_issue_agent(self, issue_number: str, force: bool = True) -> int | None:
        """Find and kill issue-linked process; return pid if killed."""
        pid = self.find_agent_pid_for_issue(issue_number)
        if not pid:
            return None
        killed = self.kill_process(pid, force=force)
        return pid if killed else None

    @staticmethod
    def _parse_pid(value: str) -> int | None:
        match = re.match(r"^(\d+)$", value.strip())
        if not match:
            return None
        return int(match.group(1))


def register_plugins(registry) -> None:
    """Register built-in runtime ops plugin."""
    from nexus.plugins import PluginKind

    registry.register_factory(
        kind=PluginKind.INPUT_ADAPTER,
        name="runtime-ops-process-guard",
        version="0.1.0",
        factory=lambda config: RuntimeOpsPlugin(config),
        description="Process discovery and safe termination helpers for runtime agent operations",
    )
