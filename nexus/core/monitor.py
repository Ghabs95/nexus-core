"""Agent monitoring and reliability utilities.

Provides logic for timeout detection and process health management.
"""

import logging
import os
import time

from nexus.core.models import StepStatus, Workflow

logger = logging.getLogger(__name__)


class MonitorEngine:
    """Engine for monitoring agent execution and identifying timeouts."""

    @staticmethod
    def check_log_timeout(
        log_file: str,
        timeout_seconds: int = 3600,
    ) -> bool:
        """
        Check if a log file indicates a timeout (lack of updates).

        Args:
            log_file: Path to the log file.
            timeout_seconds: Maximum allowed time since last update.

        Returns:
            True if timeout detected.
        """
        if not os.path.exists(log_file):
            # If log doesn't exist yet, it's not timed out (just starting)
            return False

        try:
            current_time = time.time()
            last_modified = os.path.getmtime(log_file)
            time_since_update = current_time - last_modified

            return time_since_update > timeout_seconds
        except Exception as e:
            logger.error(f"Error checking log timeout for {log_file}: {e}")
            return False

    @staticmethod
    def is_step_timed_out(workflow: Workflow, step_num: int) -> bool:
        """
        Check if a specific workflow step has exceeded its defined timeout.
        Note: This is based on wall clock time since started_at.

        Args:
            workflow: The workflow object.
            step_num: The step number to check.

        Returns:
            True if wall clock timeout exceeded.
        """
        step = workflow.get_step(step_num)
        if not step or step.status != StepStatus.RUNNING or not step.started_at:
            return False

        from datetime import datetime, UTC

        elapsed = (datetime.now(UTC) - step.started_at).total_seconds()

        # Effective timeout
        timeout = step.timeout or step.agent.timeout
        return elapsed > timeout
