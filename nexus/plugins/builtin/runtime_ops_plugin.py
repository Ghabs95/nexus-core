"""Built-in plugin: runtime process operations and guard helpers."""

import logging
import os
import re
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


class RuntimeOpsPlugin:
    """Process discovery and termination helpers for issue-linked agent runtimes."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        # Match all supported agent CLIs by default so runtime checks remain
        # accurate regardless of selected provider.
        self.process_name = self.config.get("process_name", "copilot|codex|gemini")
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
            if not self._is_safe_agent_match(command):
                continue
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

    def _is_safe_agent_match(self, command: str) -> bool:
        """Return True only for likely CLI agent invocations (not IDE extension hosts)."""
        if not command:
            return False

        lowered = command.lower()
        # Avoid killing IDE extension hosts that may mention codex/copilot in args.
        blocked_markers = (
            "extensionhost",
            "visual studio code",
            "vscode",
            "--ms-enable-electron-run-as-node",
            ".vscode/extensions",
        )
        if any(marker in lowered for marker in blocked_markers):
            return False

        allowed_names = self._allowed_process_names()
        if not allowed_names:
            return False

        tokens = command.split()
        if not tokens:
            return False

        # Direct invocation: first executable token is the agent CLI itself.
        for token in tokens[:3]:
            name = self._token_basename(token)
            if name in allowed_names:
                return True

        # Wrapped invocation (shell/node/python wrapper), where CLI appears soon after.
        wrapper_names = {"bash", "sh", "zsh", "node", "nodejs", "python", "python3"}
        head = self._token_basename(tokens[0])
        if head in wrapper_names:
            for token in tokens[1:8]:
                if self._token_basename(token) in allowed_names:
                    return True

        return False

    def _allowed_process_names(self) -> set[str]:
        # process_name can be configured as alternation regex (e.g. "copilot|codex").
        return {p.lower() for p in re.findall(r"[A-Za-z0-9_-]+", str(self.process_name or ""))}

    @staticmethod
    def _token_basename(token: str) -> str:
        value = str(token or "").strip("\"' ")
        if not value:
            return ""
        # Skip env assignments in command preambles (e.g. FOO=bar copilot ...).
        if "=" in value and not value.startswith(("/", "./", "../")):
            key, _, _rest = value.partition("=")
            if key and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                return ""
        return os.path.basename(value).lower()


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
