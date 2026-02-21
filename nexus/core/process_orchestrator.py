"""ProcessOrchestrator — centralised workflow process management.

Extracts dead/stuck-agent detection, completion scanning, auto-chain, and
finalisation from host applications (e.g. nexus's inbox_processor) into a
reusable, agent-agnostic nexus-core class.

Typical host usage (nexus Phase-3 thin shell)::

    from nexus.core.process_orchestrator import ProcessOrchestrator, AgentRuntime

    class NexusAgentRuntime(AgentRuntime):
        def launch_agent(self, issue_number, agent_type, *, trigger_source="", exclude_tools=None):
            return invoke_copilot_agent(agent_type=agent_type, ...)
        # … implement remaining abstract methods

    orchestrator = ProcessOrchestrator(
        runtime=NexusAgentRuntime(),
        complete_step_fn=complete_step_for_issue,   # async callable
        stuck_threshold_seconds=STUCK_AGENT_THRESHOLD,
        nexus_dir=get_nexus_dir_name(),
    )

    # Polling loop:
    orchestrator.scan_and_process_completions(BASE_DIR, dedup_seen)
    orchestrator.check_stuck_agents(BASE_DIR)
"""

import asyncio
import glob
import logging
import os
import re
import signal
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from nexus.core.completion import build_completion_comment, scan_for_completions

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AgentRuntime — abstract interface to the host application
# ---------------------------------------------------------------------------


class AgentRuntime(ABC):
    """Interface that :class:`ProcessOrchestrator` uses to interact with the host.

    Implement this in the host application to provide concrete agent
    launching, state tracking, alerting, and workflow finalisation.
    All methods are synchronous; async host operations must be wrapped with
    ``asyncio.run`` or equivalent inside the implementation.
    """

    @abstractmethod
    def launch_agent(
        self,
        issue_number: str,
        agent_type: str,
        *,
        trigger_source: str = "orchestrator",
        exclude_tools: Optional[List[str]] = None,
    ) -> Tuple[Optional[int], Optional[str]]:
        """Spawn an agent process for *issue_number*.

        Returns:
            ``(pid, tool_name)`` on success, ``(None, None)`` on failure.
        """

    @abstractmethod
    def load_launched_agents(self, recent_only: bool = True) -> Dict[str, dict]:
        """Return the running-agent tracker dict.

        Each value must contain at minimum::

            {
                "pid": int,
                "timestamp": float,   # unix timestamp of launch
                "agent_type": str,
                "tool": str,          # "copilot" | "gemini" | …
            }
        """

    @abstractmethod
    def save_launched_agents(self, data: Dict[str, dict]) -> None:
        """Persist the tracker dict."""

    @abstractmethod
    def clear_launch_guard(self, issue_number: str) -> None:
        """Remove the launch-cooldown for *issue_number*.

        Called before a retry so the dead/stuck agent can be relaunched
        immediately without waiting for the cooldown window.
        """

    @abstractmethod
    def should_retry(self, issue_number: str, agent_type: str) -> bool:
        """Return ``True`` if another retry attempt is allowed."""

    @abstractmethod
    def send_alert(self, message: str) -> bool:
        """Send an out-of-band alert (Telegram, Slack, …).

        Returns ``True`` if the alert was delivered successfully so the caller
        can decide whether to update state (we hold off state mutations when the
        alert fails so we can retry on the next poll).
        """

    @abstractmethod
    def audit_log(self, issue_number: str, event: str, details: str = "") -> None:
        """Record an audit event for *issue_number*."""

    @abstractmethod
    def finalize_workflow(
        self,
        issue_number: str,
        repo: str,
        last_agent: str,
        project_name: str,
    ) -> dict:
        """Handle workflow completion: close issue, create PR, send notifications.

        Returns a result dict with optional keys ``pr_url`` and ``issue_closed``.
        """

    # --- Optional hooks (concrete defaults provided) ---

    def get_workflow_state(self, issue_number: str) -> Optional[str]:
        """Return the control state of the workflow, or ``None`` if active.

        Recognised values: ``"STOPPED"``, ``"PAUSED"``.  Return ``None`` (the
        default) when no pause/stop mechanism is in use.
        """
        return None

    def should_retry_dead_agent(self, issue_number: str, agent_type: str) -> bool:
        """Return ``True`` when dead-agent retry is still workflow-valid.

        Override in hosts that can inspect workflow storage and confirm there is
        still a matching RUNNING step for ``agent_type``.
        """
        return True

    def is_process_running(self, issue_number: str) -> bool:
        """Return ``True`` if an agent process is still running for this issue.

        Override to check against a live process table.  The default returns
        ``False`` (no check), which is safe — completions will simply be
        processed immediately.
        """
        return False

    def check_log_timeout(
        self, issue_number: str, log_file: str
    ) -> Tuple[bool, Optional[int]]:
        """Check whether the agent owning *log_file* has timed out.

        Returns:
            ``(timed_out, pid_or_None)``.  The default implementation uses
            the log file's modification time; override to use
            ``AgentMonitor.check_timeout()`` or equivalent.
        """
        age = time.time() - os.path.getmtime(log_file)
        # pid cannot be determined without host knowledge from base class
        return age > 3600, None  # threshold overridden by stuck_threshold_seconds in orchestrator

    def notify_timeout(self, issue_number: str, agent_type: str, will_retry: bool) -> None:
        """Send a timeout notification.  No-op by default; override to use host
        notification system (e.g. ``notify_agent_timeout``)."""

    def get_expected_running_agent(self, issue_number: str) -> Optional[str]:
        """Return the workflow's current RUNNING step agent type, if available.

        Hosts can override this when they can inspect workflow storage even if
        launched-agent tracker metadata is missing.
        """
        return None

    def get_agent_timeout_seconds(
        self,
        issue_number: str,
        agent_type: Optional[str] = None,
    ) -> Optional[int]:
        """Return timeout for an issue/agent from workflow metadata, if available.

        Hosts can override this to source timeout from the active workflow step
        (e.g. ``step.timeout`` or ``step.agent.timeout``). Returning ``None``
        falls back to orchestrator's configured global threshold.
        """
        return None

    def post_completion_comment(self, issue_number: str, repo: str, body: str) -> bool:
        """Post a workflow completion comment to the issue.

        Hosts can override this to integrate with GitHub/GitLab. Returning
        ``False`` blocks auto-chaining so workflows do not advance silently when
        comment delivery fails.
        """
        return True

    def get_latest_issue_log(self, issue_number: str) -> Optional[str]:
        """Return latest session log path for *issue_number*, if available.

        Host runtimes can override this to expose issue-scoped task/session log
        locations for richer operator alerts.
        """
        return None

    # --- Default OS-level process helpers (rarely need overriding) ---

    def is_pid_alive(self, pid: int) -> bool:
        """Return ``True`` if the process identified by *pid* is still running."""
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # Process exists but we can't signal it — still alive.
            return True
        except Exception:
            return False

    def kill_process(self, pid: int) -> bool:
        """Send SIGTERM to *pid*.  Returns ``True`` on success."""
        try:
            os.kill(pid, signal.SIGTERM)
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# ProcessOrchestrator
# ---------------------------------------------------------------------------


class ProcessOrchestrator:
    """Workflow process management extracted from host applications.

    Responsibilities:

    - Scan completion files and drive the workflow engine forward.
    - Detect and kill timed-out (stuck) agents, then retry.
    - Detect crashed (dead) agents and alert / retry.
    - Auto-chain the next agent after a step completes.
    - Finalise completed workflows (close issue, create PR).

    This class is *agent-agnostic* — all concrete operations (launching agents,
    sending alerts, persisting state) are delegated to the injected
    :class:`AgentRuntime`.

    Args:
        runtime: Host-provided implementation of :class:`AgentRuntime`.
        complete_step_fn: Async callable with signature
            ``(issue_number: str, agent_type: str, outputs: dict) -> Optional[Workflow]``.
            Typically ``WorkflowStateEnginePlugin.complete_step_for_issue``.
            Signature: ``(issue_number: str, agent_type: str, outputs: dict,
            event_id: str) -> Optional[Workflow]``.
        stuck_threshold_seconds: Seconds of inactivity before an agent is
            considered stuck.  Defaults to 3600 (1 hour).
        nexus_dir: Name of the hidden nexus directory inside project roots.
            Defaults to ``".nexus"``.
    """

    def __init__(
        self,
        runtime: AgentRuntime,
        complete_step_fn: Callable[[str, str, dict], Any],
        *,
        stuck_threshold_seconds: int = 3600,
        nexus_dir: str = ".nexus",
    ) -> None:
        self._runtime = runtime
        self._complete_step_fn = complete_step_fn
        self._stuck_threshold = stuck_threshold_seconds
        self._nexus_dir = nexus_dir
        # Per-session set of (issue:pid) keys we've already alerted for.
        self._dead_agent_alerted: Set[str] = set()
        # Per-session set of orphaned-running-step alerts.
        self._orphaned_step_alerted: Set[str] = set()

    # ------------------------------------------------------------------
    # Completion scanning / auto-chain
    # ------------------------------------------------------------------

    def scan_and_process_completions(
        self,
        base_dir: str,
        dedup_seen: Set[str],
        *,
        resolve_project: Optional[Callable[[str], Optional[str]]] = None,
        resolve_repo: Optional[Callable[[str, str], str]] = None,
        build_transition_message: Optional[Callable[..., str]] = None,
        build_autochain_failed_message: Optional[Callable[..., str]] = None,
    ) -> None:
        """Scan *base_dir* for completion files and handle each new one.

        Args:
            base_dir: Root directory to scan.
            dedup_seen: Mutable set of dedup keys already processed this
                session.  Updated in-place as completions are handled.
            resolve_project: ``(file_path) -> project_name | None`` resolver.
                Called to determine which project a completion belongs to.
            resolve_repo: ``(project_name, issue_number) -> repo`` resolver.
                Falls back to returning *project_name* when omitted.
            build_transition_message: Factory for the transition alert message.
                Receives kwargs ``issue_number``, ``completed_agent``,
                ``next_agent``, ``repo``.
            build_autochain_failed_message: Factory for the failure alert.
                Receives the same kwargs.
        """
        detected = scan_for_completions(base_dir, nexus_dir=self._nexus_dir)
        for detection in detected:
            issue_num = detection.issue_number
            summary = detection.summary
            try:
                comment_key = detection.dedup_key
                if comment_key in dedup_seen:
                    continue

                # Skip if the agent process is still running (completion may be
                # partial / not yet flushed).
                if self._runtime.is_process_running(detection.issue_number):
                    continue

                project_name = resolve_project(detection.file_path) if resolve_project else None
                if resolve_repo:
                    repo = resolve_repo(project_name or "", issue_num)
                else:
                    repo = project_name or issue_num

                completed_agent = summary.agent_type
                logger.info(f"📋 Agent completed for issue #{issue_num} ({completed_agent})")

                comment_body = build_completion_comment(summary)
                if not self._runtime.post_completion_comment(issue_num, repo, comment_body):
                    self._runtime.send_alert(
                        "⚠️ Completion detected but Git comment delivery failed; "
                        f"auto-chain blocked for issue #{issue_num} ({completed_agent})."
                    )
                    continue

                dedup_seen.add(comment_key)

                # Ask the workflow engine what happens next.
                engine_workflow = asyncio.run(
                    self._complete_step_fn(issue_num, completed_agent, summary.to_dict(), comment_key)
                )

                next_agent: Optional[str] = None

                if engine_workflow is not None:
                    from nexus.core.models import WorkflowState

                    if engine_workflow.state in (
                        WorkflowState.COMPLETED,
                        WorkflowState.FAILED,
                    ):
                        self._handle_workflow_done(
                            issue_num, repo, completed_agent, project_name or "",
                            reason=engine_workflow.state.value,
                        )
                        continue

                    next_agent = engine_workflow.active_agent_type
                    if not next_agent:
                        logger.warning(
                            f"Engine returned no active agent for issue #{issue_num}; "
                            "skipping auto-chain"
                        )
                        continue
                    logger.info(
                        f"🔀 Engine routed #{issue_num}: {completed_agent} → {next_agent}"
                    )

                else:
                    # Manual fallback (no engine workflow mapped to this issue).
                    if summary.is_workflow_done:
                        self._handle_workflow_done(
                            issue_num, repo, completed_agent, project_name or "",
                            reason="manual",
                        )
                        continue

                    next_agent = summary.next_agent.strip()
                    if _is_terminal(next_agent):
                        self._handle_workflow_done(
                            issue_num, repo, completed_agent, project_name or "",
                            reason="terminal-agent-ref",
                        )
                        continue

                # Send transition alert.
                if build_transition_message:
                    msg = build_transition_message(
                        issue_number=issue_num,
                        completed_agent=completed_agent,
                        next_agent=next_agent,
                        repo=repo,
                    )
                else:
                    msg = f"🔀 Chaining #{issue_num}: {completed_agent} → {next_agent}"
                self._runtime.send_alert(msg)

                # Launch next agent.
                pid, tool_used = self._runtime.launch_agent(
                    issue_num, next_agent, trigger_source="completion-scan"
                )
                if pid:
                    logger.info(
                        f"🔗 Auto-chained {completed_agent} → {next_agent} "
                        f"for issue #{issue_num} (PID: {pid}, tool: {tool_used})"
                    )
                elif tool_used in {"duplicate-suppressed", "workflow-terminal", "launch-skipped"}:
                    logger.info(
                        f"⏭️ Auto-chain launch skipped for issue #{issue_num} "
                        f"({completed_agent} → {next_agent}, reason: {tool_used})"
                    )
                else:
                    logger.error(
                        f"❌ Failed to auto-chain to {next_agent} for issue #{issue_num}"
                    )
                    if build_autochain_failed_message:
                        fail_msg = build_autochain_failed_message(
                            issue_number=issue_num,
                            completed_agent=completed_agent,
                            next_agent=next_agent,
                            repo=repo,
                        )
                    else:
                        fail_msg = (
                            f"❌ Auto-chain failed for issue #{issue_num}: "
                            f"could not launch {next_agent} after {completed_agent}"
                        )
                    self._runtime.send_alert(fail_msg)

            except Exception as exc:
                logger.warning(
                    f"Error processing completion for issue "
                    f"#{detection.issue_number}: {exc}",
                    exc_info=True,
                )

    # ------------------------------------------------------------------
    # Stuck / dead agent detection
    # ------------------------------------------------------------------

    def check_stuck_agents(self, base_dir: str) -> None:
        """Strategy-1: find stale log files and kill timed-out agents.

        Scans for log files matching ``<tool>_<issue>_<timestamp>.log``,
        checks each for timeout (via :meth:`AgentRuntime.check_log_timeout`),
        kills the process, and retries if allowed.  Finishes by calling
        :meth:`detect_dead_agents` (Strategy-2).
        """
        log_pattern = os.path.join(
            base_dir, "**", self._nexus_dir, "tasks", "*", "logs", "*_*.log"
        )
        log_files = glob.glob(log_pattern, recursive=True)
        launched = self._runtime.load_launched_agents(recent_only=False)

        for log_file in log_files:
            match = re.search(
                r"(?:copilot|gemini)_(\d+)_\d{8}_", os.path.basename(log_file)
            )
            if not match:
                continue
            issue_num = match.group(1)

            # Only inspect the newest log for each issue.
            issue_logs = sorted(
                [
                    f for f in log_files
                    if re.search(
                        rf"(?:copilot|gemini)_{issue_num}_\d{{8}}_",
                        os.path.basename(f),
                    )
                ],
                key=os.path.getmtime,
                reverse=True,
            )
            if issue_logs and log_file != issue_logs[0]:
                continue

            agent_data = launched.get(str(issue_num), {}) if isinstance(launched, dict) else {}
            if not isinstance(agent_data, dict):
                agent_data = {}
            tracked_agent = agent_data.get("agent_type")
            expected_agent = self._runtime.get_expected_running_agent(str(issue_num))
            effective_agent = tracked_agent or expected_agent

            age = time.time() - os.path.getmtime(log_file)
            timeout_seconds = self._resolve_timeout_seconds(issue_num, effective_agent)
            if age <= timeout_seconds:
                continue

            _, pid = self._runtime.check_log_timeout(issue_num, log_file)

            self._handle_timeout(issue_num, log_file, pid)

        # Always follow up with dead-process detection.
        self.detect_dead_agents()

    def detect_dead_agents(self) -> None:
        """Strategy-2: detect agents that exited without posting a completion.

        Reads the launched-agents tracker, checks each PID, and alerts /
        retries if the process is dead but no completion was recorded.  Gives
        a grace period of ``stuck_threshold_seconds`` before acting so that
        the completion scanner gets to run first.
        """
        launched = self._runtime.load_launched_agents(recent_only=False)
        if not launched:
            return

        now = time.time()

        for issue_num, agent_data in list(launched.items()):
            pid = agent_data.get("pid")
            launch_ts = agent_data.get("timestamp", 0.0)
            agent_type = agent_data.get("agent_type", "unknown")

            if not pid:
                continue

            grace_seconds = self._resolve_timeout_seconds(issue_num, agent_type)
            # Grace period — let completion scanner run first.
            if (now - launch_ts) < grace_seconds:
                continue

            if self._runtime.is_pid_alive(pid):
                continue  # Strategy-1 handles timeout kills.

            # Respect workflow-level pause / terminal states.
            state = self._runtime.get_workflow_state(str(issue_num))
            if state in ("STOPPED", "PAUSED", "COMPLETED", "FAILED", "CANCELLED"):
                logger.debug(
                    f"Skipping dead-agent check for issue #{issue_num}: "
                    f"workflow state is {state}"
                )
                continue

            alert_key = f"{issue_num}:{pid}"
            if alert_key in self._dead_agent_alerted:
                continue

            if not self._runtime.should_retry_dead_agent(str(issue_num), agent_type):
                logger.info(
                    f"Skipping dead-agent retry for issue #{issue_num}: "
                    f"workflow no longer expects agent {agent_type}"
                )
                self._runtime.audit_log(
                    issue_num,
                    "AGENT_DEAD_STALE",
                    f"PID {pid} ({agent_type}) no longer matches workflow RUNNING step",
                )
                launched.pop(issue_num, None)
                self._runtime.save_launched_agents(launched)
                self._dead_agent_alerted.add(alert_key)
                continue

            crashed_tool = agent_data.get("tool", "")
            age_min = (now - launch_ts) / 60
            will_retry = self._runtime.should_retry(issue_num, agent_type)
            latest_log = self._runtime.get_latest_issue_log(str(issue_num))
            log_suffix = f"\nLog: {latest_log}" if latest_log else ""

            logger.warning(
                f"💀 Dead agent: issue #{issue_num} ({agent_type}, "
                f"PID {pid}, age {age_min:.0f}min)"
            )
            self._runtime.audit_log(
                issue_num,
                "AGENT_DEAD",
                f"PID {pid} ({agent_type}) exited without completion "
                f"after {age_min:.0f}min",
            )

            if will_retry:
                msg = (
                    f"💀 **Agent Crashed → Retrying**\n\n"
                    f"Issue: #{issue_num}\n"
                    f"Agent: {agent_type} (PID {pid}, tool: {crashed_tool})\n"
                    f"Status: Process exited without completion, retry scheduled"
                    f"{log_suffix}"
                )
                if not self._runtime.send_alert(msg):
                    # Alert failed — retry on next poll without mutating state.
                    continue

                self._dead_agent_alerted.add(alert_key)
                launched.pop(issue_num, None)
                self._runtime.save_launched_agents(launched)
                self._runtime.clear_launch_guard(issue_num)

                try:
                    pid_new, _ = self._runtime.launch_agent(
                        issue_num,
                        agent_type,
                        trigger_source="dead-agent-retry",
                        exclude_tools=[crashed_tool] if crashed_tool else None,
                    )
                    if pid_new:
                        logger.info(
                            f"🔄 Dead-agent retry launched: {agent_type} for #{issue_num}"
                        )
                    else:
                        logger.error(
                            f"Dead-agent retry failed to launch {agent_type} for #{issue_num}"
                        )
                except Exception as exc:
                    logger.error(
                        f"Exception during dead-agent retry for #{issue_num}: {exc}"
                    )

            else:
                msg = (
                    f"💀 **Agent Crashed → Manual Intervention**\n\n"
                    f"Issue: #{issue_num}\n"
                    f"Agent: {agent_type} (PID {pid})\n"
                    f"Status: Process exited without completion, "
                    f"max retries reached\n\n"
                    f"{f'Log: {latest_log}\n' if latest_log else ''}"
                    f"Use /reprocess to retry"
                )
                if not self._runtime.send_alert(msg):
                    continue

                self._dead_agent_alerted.add(alert_key)
                launched.pop(issue_num, None)
                self._runtime.save_launched_agents(launched)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _handle_workflow_done(
        self,
        issue_num: str,
        repo: str,
        last_agent: str,
        project_name: str,
        reason: str,
    ) -> None:
        logger.info(
            f"✅ Workflow {reason} for issue #{issue_num} (last agent: {last_agent})"
        )
        tracker = self._runtime.load_launched_agents(recent_only=False)
        tracker.pop(str(issue_num), None)
        self._runtime.save_launched_agents(tracker)
        self._runtime.finalize_workflow(issue_num, repo, last_agent, project_name)

    def _handle_timeout(
        self,
        issue_num: str,
        log_file: str,
        pid: Optional[int],
    ) -> None:
        """Kill a timed-out agent and trigger a retry if allowed."""
        launched = self._runtime.load_launched_agents(recent_only=False)
        agent_data = launched.get(str(issue_num), {})
        tracker_pid = agent_data.get("pid")
        effective_pid = tracker_pid or pid
        agent_type = agent_data.get("agent_type", "unknown")
        crashed_tool = agent_data.get("tool", "")
        age = time.time() - os.path.getmtime(log_file)

        # True orphan: workflow expects a RUNNING step but there is no tracker/runtime PID.
        # If we do have a PID but it's dead, let detect_dead_agents() handle it consistently.
        if effective_pid and not self._runtime.is_pid_alive(effective_pid):
            return

        if not effective_pid:
            expected_agent = self._runtime.get_expected_running_agent(str(issue_num))
            if not expected_agent:
                return

            alert_key = f"{issue_num}:orphan:{expected_agent}"
            if alert_key in self._orphaned_step_alerted:
                return

            if not self._runtime.should_retry_dead_agent(str(issue_num), expected_agent):
                self._runtime.audit_log(
                    issue_num,
                    "AGENT_DEAD_STALE",
                    f"Orphaned RUNNING step ({expected_agent}) no longer workflow-valid",
                )
                self._orphaned_step_alerted.add(alert_key)
                return

            will_retry = self._runtime.should_retry(issue_num, expected_agent)
            if will_retry:
                msg = (
                    f"⚠️ **Orphaned Running Step Detected**\n\n"
                    f"Issue: #{issue_num}\n"
                    f"Agent: {expected_agent}\n"
                    f"Status: RUNNING in workflow but no live process/tracker entry; "
                    f"retry scheduled"
                )
            else:
                msg = (
                    f"❌ **Orphaned Running Step — Manual Intervention Required**\n\n"
                    f"Issue: #{issue_num}\n"
                    f"Agent: {expected_agent}\n"
                    f"Status: RUNNING in workflow but no live process/tracker entry\n"
                    f"Action: retry limit reached"
                )

            if not self._runtime.send_alert(msg):
                return

            self._runtime.audit_log(
                issue_num,
                "AGENT_ORPHANED_RUNNING_STEP",
                f"{expected_agent} has no live process/tracker (log age {age / 60:.0f}min)",
            )
            self._orphaned_step_alerted.add(alert_key)

            if will_retry:
                self._runtime.clear_launch_guard(issue_num)
                try:
                    self._runtime.launch_agent(
                        issue_num,
                        expected_agent,
                        trigger_source="orphan-timeout-retry",
                    )
                except Exception as exc:
                    logger.error(
                        f"Orphaned-step retry exception for issue #{issue_num}: {exc}"
                    )
            return

        killed = self._runtime.kill_process(effective_pid)
        if not killed:
            return

        logger.info(
            f"⏰ Killed stuck agent PID {effective_pid} for issue #{issue_num} "
            f"(log age {age / 60:.0f}min)"
        )
        self._runtime.audit_log(
            issue_num,
            "AGENT_KILLED",
            f"PID {effective_pid} killed after {age / 60:.0f}min of inactivity",
        )

        will_retry = self._runtime.should_retry(issue_num, agent_type)
        self._runtime.notify_timeout(issue_num, agent_type, will_retry)

        if will_retry:
            launched.pop(str(issue_num), None)
            self._runtime.save_launched_agents(launched)
            self._runtime.clear_launch_guard(issue_num)
            try:
                self._runtime.launch_agent(
                    issue_num,
                    agent_type,
                    trigger_source="timeout-retry",
                    exclude_tools=[crashed_tool] if crashed_tool else None,
                )
            except Exception as exc:
                logger.error(
                    f"Timeout retry exception for issue #{issue_num}: {exc}"
                )

    def _resolve_timeout_seconds(self, issue_num: str, agent_type: Optional[str]) -> int:
        """Resolve timeout for issue/agent from runtime, fallback to global threshold."""
        timeout = self._runtime.get_agent_timeout_seconds(str(issue_num), agent_type)
        if isinstance(timeout, (int, float)):
            value = int(timeout)
            if value > 0:
                return value
        return int(self._stuck_threshold)


# ---------------------------------------------------------------------------
# Module-level helper
# ---------------------------------------------------------------------------


def _is_terminal(agent_ref: str) -> bool:
    """Return ``True`` if *agent_ref* signals end-of-workflow."""
    from nexus.core.completion import _TERMINAL_VALUES

    return agent_ref.strip().lower() in _TERMINAL_VALUES
