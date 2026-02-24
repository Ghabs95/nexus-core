"""Tests for nexus.core.chat_agents_schema."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from nexus.core.chat_agents_schema import (
    HandoffDispatcher,
    HandoffPayload,
    sign_handoff,
    verify_handoff,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SECRET = "test-secret-value"


def _make_payload(**kwargs) -> HandoffPayload:
    defaults = dict(
        issued_by="designer",
        target_agent="developer",
        issue_number="69",
        workflow_id="nexus-69-full",
        task_context={"key": "value"},
        expires_at=None,
    )
    defaults.update(kwargs)
    return HandoffPayload.create(**defaults)


# ---------------------------------------------------------------------------
# HandoffPayload.create
# ---------------------------------------------------------------------------


class TestHandoffPayloadCreate:
    def test_creates_uuid_handoff_id(self):
        p = _make_payload()
        import re
        assert re.match(r"[0-9a-f-]{36}", p.handoff_id)

    def test_default_fields(self):
        p = _make_payload()
        assert p.issued_by == "designer"
        assert p.target_agent == "developer"
        assert p.issue_number == "69"
        assert p.workflow_id == "nexus-69-full"
        assert p.task_context == {"key": "value"}
        assert p.retry_count == 0
        assert p.max_retries == 3
        assert p.retry_backoff_s == 5.0
        assert p.verification_token == ""
        assert p.expires_at is None

    def test_issue_number_coerced_to_str(self):
        p = HandoffPayload.create(
            issued_by="a", target_agent="b",
            issue_number=42, workflow_id="w",
        )
        assert p.issue_number == "42"

    def test_created_at_is_utc_iso(self):
        p = _make_payload()
        dt = datetime.fromisoformat(p.created_at.replace("Z", "+00:00"))
        assert dt.tzinfo is not None


# ---------------------------------------------------------------------------
# HandoffPayload.is_expired
# ---------------------------------------------------------------------------


class TestHandoffPayloadIsExpired:
    def test_no_expiry(self):
        p = _make_payload()
        assert p.is_expired() is False

    def test_future_expiry(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        p = _make_payload(expires_at=future)
        assert p.is_expired() is False

    def test_past_expiry(self):
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        p = _make_payload(expires_at=past)
        assert p.is_expired() is True

    def test_invalid_expires_at_treated_as_expired(self):
        p = _make_payload()
        p.expires_at = "not-a-date"
        assert p.is_expired() is True


# ---------------------------------------------------------------------------
# Serialization roundtrip
# ---------------------------------------------------------------------------


class TestHandoffPayloadSerialization:
    def test_roundtrip(self):
        p = _make_payload()
        p.verification_token = sign_handoff(p, _SECRET)
        d = p.to_dict()
        p2 = HandoffPayload.from_dict(d)
        assert p2.handoff_id == p.handoff_id
        assert p2.verification_token == p.verification_token
        assert p2.task_context == p.task_context

    def test_from_dict_defaults(self):
        """from_dict should handle optional fields gracefully."""
        minimal = {
            "handoff_id": "abc",
            "issued_by": "x",
            "target_agent": "y",
            "issue_number": "1",
            "workflow_id": "w",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        p = HandoffPayload.from_dict(minimal)
        assert p.verification_token == ""
        assert p.retry_count == 0
        assert p.max_retries == 3


# ---------------------------------------------------------------------------
# sign_handoff / verify_handoff
# ---------------------------------------------------------------------------


class TestSigning:
    def test_sign_returns_hex_string(self):
        p = _make_payload()
        token = sign_handoff(p, _SECRET)
        assert isinstance(token, str)
        assert len(token) == 64  # SHA-256 hex

    def test_verify_valid_token(self):
        p = _make_payload()
        p.verification_token = sign_handoff(p, _SECRET)
        assert verify_handoff(p, _SECRET) is True

    def test_verify_wrong_secret(self):
        p = _make_payload()
        p.verification_token = sign_handoff(p, _SECRET)
        assert verify_handoff(p, "wrong-secret") is False

    def test_verify_tampered_payload(self):
        p = _make_payload()
        p.verification_token = sign_handoff(p, _SECRET)
        p.target_agent = "malicious-agent"
        assert verify_handoff(p, _SECRET) is False

    def test_sign_deterministic(self):
        p = _make_payload()
        assert sign_handoff(p, _SECRET) == sign_handoff(p, _SECRET)

    def test_different_payloads_different_tokens(self):
        p1 = _make_payload()
        p2 = _make_payload()
        # different UUIDs → different canonical bytes → different tokens
        assert sign_handoff(p1, _SECRET) != sign_handoff(p2, _SECRET)


# ---------------------------------------------------------------------------
# HandoffDispatcher
# ---------------------------------------------------------------------------


class TestHandoffDispatcher:
    def _runtime(self, pid=1234, tool="copilot"):
        rt = MagicMock()
        rt.launch_agent.return_value = (pid, tool)
        return rt

    def _dispatcher(self):
        return HandoffDispatcher(secret=_SECRET)

    def test_dispatch_success_first_try(self):
        rt = self._runtime()
        d = self._dispatcher()
        p = _make_payload()
        pid, tool = d.dispatch(p, rt)
        assert pid == 1234
        assert tool == "copilot"
        rt.launch_agent.assert_called_once()

    def test_dispatch_signs_payload(self):
        rt = self._runtime()
        d = self._dispatcher()
        p = _make_payload()
        d.dispatch(p, rt)
        assert p.verification_token != ""
        assert verify_handoff(p, _SECRET)

    def test_dispatch_rejects_expired(self):
        rt = self._runtime()
        d = self._dispatcher()
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        p = _make_payload(expires_at=past)
        pid, tool = d.dispatch(p, rt)
        assert pid is None
        rt.launch_agent.assert_not_called()

    def test_dispatch_retries_on_failure(self):
        rt = MagicMock()
        # Fail twice, succeed on third
        rt.launch_agent.side_effect = [(None, None), (None, None), (99, "copilot")]
        d = HandoffDispatcher(secret=_SECRET)
        p = _make_payload(max_retries=3, retry_backoff_s=0.0)
        pid, tool = d.dispatch(p, rt)
        assert pid == 99
        assert rt.launch_agent.call_count == 3

    def test_dispatch_exhausts_retries(self):
        rt = MagicMock()
        rt.launch_agent.return_value = (None, None)
        d = HandoffDispatcher(secret=_SECRET)
        p = _make_payload(max_retries=2, retry_backoff_s=0.0)
        pid, tool = d.dispatch(p, rt)
        assert pid is None
        assert rt.launch_agent.call_count == 3  # 1 initial + 2 retries

    def test_missing_secret_raises(self):
        import os
        env_backup = os.environ.pop("NEXUS_HANDOFF_SECRET", None)
        try:
            d = HandoffDispatcher(secret_env="NEXUS_HANDOFF_SECRET")
            rt = self._runtime()
            p = _make_payload()
            with pytest.raises(ValueError, match="NEXUS_HANDOFF_SECRET"):
                d.dispatch(p, rt)
        finally:
            if env_backup is not None:
                os.environ["NEXUS_HANDOFF_SECRET"] = env_backup

    def test_dispatch_uses_env_secret(self, monkeypatch):
        monkeypatch.setenv("NEXUS_HANDOFF_SECRET", _SECRET)
        d = HandoffDispatcher()  # no explicit secret
        rt = self._runtime()
        p = _make_payload()
        pid, tool = d.dispatch(p, rt)
        assert pid == 1234
        assert verify_handoff(p, _SECRET)

    def test_launch_agent_exception_retries(self):
        rt = MagicMock()
        rt.launch_agent.side_effect = [RuntimeError("boom"), (77, "tool")]
        d = HandoffDispatcher(secret=_SECRET)
        p = _make_payload(max_retries=2, retry_backoff_s=0.0)
        pid, tool = d.dispatch(p, rt)
        assert pid == 77
        assert rt.launch_agent.call_count == 2
