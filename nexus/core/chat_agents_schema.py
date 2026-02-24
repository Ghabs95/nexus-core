"""Agent Handoff Protocol — agent-to-agent communication schema.

Defines the standardized contract for agents to pass tasks, context, and
verification tokens to one another with guaranteed context integrity.

Three components:
1. ``HandoffPayload`` — canonical agent↔agent data contract (schema).
2. ``sign_handoff`` / ``verify_handoff`` — HMAC-SHA256 state-signing.
3. ``HandoffDispatcher`` — retry and timeout logic for agent-to-agent delegation.

Design decisions (ADRs):
- ADR-001: HMAC-SHA256 over canonical JSON (sorted keys) for state-signing.
  Preferred over asymmetric signing for intra-system trust — simpler key
  management with no PKI overhead. Secret read from ``NEXUS_HANDOFF_SECRET``.
- ADR-002: ``HandoffDispatcher`` delegates spawning to ``AgentRuntime.launch_agent()``
  (abstract interface in process_orchestrator.py) — no tight coupling to a
  specific provider or launch mechanism.
- ADR-003: ``expires_at`` enforces timeout at the dispatcher level; expired
  payloads are rejected *before* signature verification to avoid unnecessary
  crypto work.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

if TYPE_CHECKING:
    from nexus.core.process_orchestrator import AgentRuntime

logger = logging.getLogger(__name__)

_DEFAULT_SECRET_ENV = "NEXUS_HANDOFF_SECRET"
_SIGN_FIELDS = (
    "handoff_id",
    "issued_by",
    "target_agent",
    "issue_number",
    "workflow_id",
    "task_context",
    "created_at",
    "expires_at",
)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


@dataclass
class HandoffPayload:
    """Canonical agent↔agent handoff contract.

    Attributes:
        handoff_id: UUID4 used for deduplication and audit trail.
        issued_by: ``agent_type`` of the sending agent.
        target_agent: ``agent_type`` of the receiving agent.
        issue_number: GitHub issue number this handoff belongs to.
        workflow_id: Workflow run identifier (e.g. ``nexus-69-full``).
        task_context: Arbitrary context bag passed to the target agent.
        verification_token: HMAC-SHA256 hex digest (set by :func:`sign_handoff`).
        created_at: ISO-8601 UTC timestamp of creation.
        expires_at: Optional ISO-8601 UTC expiry; ``None`` means no expiry.
        retry_count: Current retry attempt number (starts at 0).
        max_retries: Maximum number of dispatch attempts before giving up.
        retry_backoff_s: Initial exponential backoff delay in seconds.
    """

    handoff_id: str
    issued_by: str
    target_agent: str
    issue_number: str
    workflow_id: str
    task_context: Dict[str, Any]
    verification_token: str
    created_at: str
    expires_at: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 3
    retry_backoff_s: float = 5.0

    @classmethod
    def create(
        cls,
        issued_by: str,
        target_agent: str,
        issue_number: str,
        workflow_id: str,
        task_context: Optional[Dict[str, Any]] = None,
        expires_at: Optional[str] = None,
        max_retries: int = 3,
        retry_backoff_s: float = 5.0,
    ) -> "HandoffPayload":
        """Factory that creates a new, *unsigned* :class:`HandoffPayload`.

        Call :func:`sign_handoff` on the result before dispatching.

        Args:
            issued_by: ``agent_type`` of the sending agent.
            target_agent: ``agent_type`` of the receiving agent.
            issue_number: GitHub issue number.
            workflow_id: Workflow run identifier.
            task_context: Arbitrary context dict; defaults to ``{}``.
            expires_at: Optional ISO-8601 UTC expiry timestamp.
            max_retries: Maximum dispatch attempts.
            retry_backoff_s: Initial backoff delay in seconds.

        Returns:
            A new :class:`HandoffPayload` with an empty ``verification_token``.
        """
        return cls(
            handoff_id=str(uuid.uuid4()),
            issued_by=issued_by,
            target_agent=target_agent,
            issue_number=str(issue_number),
            workflow_id=workflow_id,
            task_context=task_context or {},
            verification_token="",
            created_at=datetime.now(timezone.utc).isoformat(),
            expires_at=expires_at,
            max_retries=max_retries,
            retry_backoff_s=retry_backoff_s,
        )

    def is_expired(self) -> bool:
        """Return ``True`` if ``expires_at`` is set and the payload has expired."""
        if not self.expires_at:
            return False
        try:
            expiry = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
            return datetime.now(timezone.utc) > expiry
        except (ValueError, TypeError):
            logger.warning("Invalid expires_at value %r — treating as expired", self.expires_at)
            return True

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dict (suitable for JSON encoding)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HandoffPayload":
        """Deserialize from a plain dict."""
        return cls(
            handoff_id=data["handoff_id"],
            issued_by=data["issued_by"],
            target_agent=data["target_agent"],
            issue_number=str(data["issue_number"]),
            workflow_id=data["workflow_id"],
            task_context=data.get("task_context", {}),
            verification_token=data.get("verification_token", ""),
            created_at=data["created_at"],
            expires_at=data.get("expires_at"),
            retry_count=int(data.get("retry_count", 0)),
            max_retries=int(data.get("max_retries", 3)),
            retry_backoff_s=float(data.get("retry_backoff_s", 5.0)),
        )


# ---------------------------------------------------------------------------
# State-signing (ADR-001)
# ---------------------------------------------------------------------------


def _canonical_bytes(payload: HandoffPayload) -> bytes:
    """Return canonical JSON bytes of the signable fields (sorted keys).

    Raises:
        ValueError: If any signable field (notably ``task_context``) contains
            non-JSON-serializable data.
    """
    signable: Dict[str, Any] = {k: getattr(payload, k) for k in _SIGN_FIELDS}
    try:
        return json.dumps(signable, sort_keys=True, ensure_ascii=True).encode("utf-8")
    except TypeError as exc:
        raise ValueError(
            "HandoffPayload contains non-JSON-serializable data in signable "
            "fields (e.g. 'task_context'). Ensure all values are JSON-"
            "serializable primitives (str, int, float, bool, None, lists, "
            "and dicts)."
        ) from exc


def sign_handoff(payload: HandoffPayload, secret: str) -> str:
    """Compute HMAC-SHA256 over the signable fields and return the hex digest.

    The result is *not* written back to ``payload.verification_token``; the
    caller is responsible for that assignment.

    Args:
        payload: The :class:`HandoffPayload` to sign.
        secret: Shared secret key.

    Returns:
        HMAC-SHA256 hex digest string.
    """
    mac = hmac.new(secret.encode("utf-8"), _canonical_bytes(payload), hashlib.sha256)
    return mac.hexdigest()


def verify_handoff(payload: HandoffPayload, secret: str) -> bool:
    """Verify the HMAC-SHA256 ``verification_token`` on *payload*.

    Uses :func:`hmac.compare_digest` to prevent timing attacks.

    Args:
        payload: The :class:`HandoffPayload` whose token to verify.
        secret: Shared secret key.

    Returns:
        ``True`` if the token is valid, ``False`` otherwise.
    """
    expected = sign_handoff(payload, secret)
    return hmac.compare_digest(expected, payload.verification_token)


# ---------------------------------------------------------------------------
# HandoffDispatcher (ADR-002, ADR-003)
# ---------------------------------------------------------------------------


class HandoffDispatcher:
    """Dispatch a :class:`HandoffPayload` to a target agent with retry/timeout.

    Uses exponential back-off consistent with ``WorkflowStep.backoff_strategy``
    in the existing workflow schema.  Actual agent spawning is delegated to
    ``AgentRuntime.launch_agent()`` to avoid coupling to any specific provider.

    Args:
        secret_env: Name of the environment variable holding the HMAC secret.
            Defaults to ``NEXUS_HANDOFF_SECRET``.
        secret: Explicit secret string.  If provided, overrides *secret_env*.
    """

    def __init__(
        self,
        secret_env: str = _DEFAULT_SECRET_ENV,
        secret: Optional[str] = None,
    ) -> None:
        self._secret_env = secret_env
        self._explicit_secret = secret

    def _get_secret(self) -> str:
        if self._explicit_secret:
            return self._explicit_secret
        val = __import__("os").environ.get(self._secret_env, "")
        if not val:
            raise ValueError(
                f"Handoff secret not set. "
                f"Set the {self._secret_env!r} environment variable."
            )
        return val

    def dispatch(
        self,
        payload: HandoffPayload,
        runtime: "AgentRuntime",
    ) -> Tuple[Optional[int], Optional[str]]:
        """Sign, validate, and dispatch *payload* to the target agent.

        Applies exponential back-off on failure up to ``payload.max_retries``.
        Expired payloads (ADR-003) are rejected before signature verification.

        Args:
            payload: The handoff payload to dispatch.
            runtime: Host-provided :class:`AgentRuntime` implementation.

        Returns:
            ``(pid, tool_name)`` on success, ``(None, None)`` on all failures.

        Raises:
            ValueError: If the HMAC secret is not configured.
        """
        # ADR-003: reject expired payloads before any crypto work
        if payload.is_expired():
            logger.error(
                "Handoff %s rejected: payload expired at %s",
                payload.handoff_id,
                payload.expires_at,
            )
            return None, None

        secret = self._get_secret()

        # Sign (or re-sign) the payload before dispatch
        payload.verification_token = sign_handoff(payload, secret)

        last_result: Tuple[Optional[int], Optional[str]] = (None, None)
        attempt = 0
        max_attempts = max(1, payload.max_retries + 1)

        while attempt < max_attempts:
            payload.retry_count = attempt
            logger.info(
                "Dispatching handoff %s → %s (attempt %d/%d)",
                payload.issued_by,
                payload.target_agent,
                attempt + 1,
                max_attempts,
            )

            try:
                pid, tool = runtime.launch_agent(
                    payload.issue_number,
                    payload.target_agent,
                    trigger_source=f"handoff:{payload.handoff_id}",
                )
            except Exception as exc:
                logger.warning(
                    "launch_agent raised on attempt %d: %s",
                    attempt + 1,
                    exc,
                )
                pid, tool = None, None

            if pid is not None:
                logger.info(
                    "Handoff %s dispatched successfully (pid=%s, tool=%s)",
                    payload.handoff_id,
                    pid,
                    tool,
                )
                return pid, tool

            last_result = (pid, tool)
            attempt += 1

            if attempt < max_attempts:
                delay = payload.retry_backoff_s * (2 ** (attempt - 1))
                logger.warning(
                    "Handoff %s attempt %d failed; retrying in %.1fs",
                    payload.handoff_id,
                    attempt,
                    delay,
                )
                time.sleep(delay)

        logger.error(
            "Handoff %s to %s failed after %d attempt(s)",
            payload.handoff_id,
            payload.target_agent,
            max_attempts,
        )
        return last_result
