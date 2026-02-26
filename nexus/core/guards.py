"""Launch guards for idempotent agent execution.

Prevents duplicate agent launches through multiple layers of detection:
1. Process detection (pgrep or custom check)
2. Timestamp-based recent-launch tracking
3. Pluggable custom guard functions
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Default cooldown: don't re-launch the same agent for the same issue
# within this many seconds.
DEFAULT_COOLDOWN_SECONDS = 300  # 5 minutes


@dataclass
class LaunchRecord:
    """Record of an agent launch."""

    issue_id: str
    agent_type: str
    timestamp: float
    pid: int | None = None


class LaunchGuard:
    """Prevents duplicate agent launches.

    Maintains an in-memory ledger of recent launches and checks against it
    before allowing a new launch.  Callers can also register custom guard
    functions (e.g. pgrep-based process checks).

    Example usage::

        guard = LaunchGuard(cooldown_seconds=300)

        if guard.can_launch(issue_id="42", agent_type="debug"):
            pid = launch_agent(...)
            guard.record_launch(issue_id="42", agent_type="debug", pid=pid)
        else:
            logger.info("Skipping — agent recently launched for this issue")
    """

    def __init__(
        self,
        cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
        custom_guard: Callable[[str, str], bool] | None = None,
    ):
        """
        Args:
            cooldown_seconds: Minimum interval between launches for the same
                issue+agent_type pair.
            custom_guard: Optional callable ``(issue_id, agent_type) -> bool``.
                Return ``True`` to **allow** the launch, ``False`` to block it.
                Called *after* the timestamp check passes.
        """
        self._cooldown = cooldown_seconds
        self._custom_guard = custom_guard
        # Key: "{issue_id}:{agent_type}" → LaunchRecord
        self._launches: dict[str, LaunchRecord] = {}

    def can_launch(self, issue_id: str, agent_type: str) -> bool:
        """Check whether launching this agent is allowed right now.

        Returns ``True`` if the launch should proceed.
        """
        key = f"{issue_id}:{agent_type}"
        record = self._launches.get(key)

        if record:
            elapsed = time.time() - record.timestamp
            if elapsed < self._cooldown:
                logger.debug(
                    f"LaunchGuard: blocked {agent_type} for issue {issue_id} "
                    f"(launched {elapsed:.0f}s ago, cooldown={self._cooldown}s)"
                )
                return False

        # Delegate to custom guard if provided
        if self._custom_guard is not None:
            try:
                allowed = self._custom_guard(issue_id, agent_type)
                if not allowed:
                    logger.debug(
                        f"LaunchGuard: custom guard blocked {agent_type} for issue {issue_id}"
                    )
                    return False
            except Exception as exc:
                logger.warning(f"LaunchGuard: custom guard raised {exc}, allowing launch")

        return True

    def record_launch(
        self,
        issue_id: str,
        agent_type: str,
        pid: int | None = None,
    ) -> None:
        """Record that an agent was launched."""
        key = f"{issue_id}:{agent_type}"
        self._launches[key] = LaunchRecord(
            issue_id=issue_id,
            agent_type=agent_type,
            timestamp=time.time(),
            pid=pid,
        )
        logger.debug(f"LaunchGuard: recorded launch {agent_type} for issue {issue_id}")

    def clear(self, issue_id: str | None = None) -> int:
        """Remove launch records.

        Args:
            issue_id: If provided, only clear records for this issue.
                Otherwise clear everything.

        Returns:
            Number of records cleared.
        """
        if issue_id is None:
            count = len(self._launches)
            self._launches.clear()
            return count

        to_remove = [k for k in self._launches if k.startswith(f"{issue_id}:")]
        for k in to_remove:
            del self._launches[k]
        return len(to_remove)

    def cleanup_expired(self) -> int:
        """Remove records older than the cooldown window.

        Returns:
            Number of records cleaned up.
        """
        now = time.time()
        expired = [k for k, rec in self._launches.items() if (now - rec.timestamp) > self._cooldown]
        for k in expired:
            del self._launches[k]
        return len(expired)

    @property
    def active_count(self) -> int:
        """Number of non-expired launch records."""
        now = time.time()
        return sum(1 for rec in self._launches.values() if (now - rec.timestamp) <= self._cooldown)
