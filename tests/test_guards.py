"""Tests for the LaunchGuard module."""
import time
from unittest.mock import MagicMock

import pytest

from nexus.core.guards import LaunchGuard


class TestLaunchGuard:
    def test_first_launch_allowed(self):
        guard = LaunchGuard(cooldown_seconds=60)
        assert guard.can_launch("42", "debug") is True

    def test_duplicate_launch_blocked(self):
        guard = LaunchGuard(cooldown_seconds=60)
        guard.record_launch("42", "debug")
        assert guard.can_launch("42", "debug") is False

    def test_different_issue_allowed(self):
        guard = LaunchGuard(cooldown_seconds=60)
        guard.record_launch("42", "debug")
        assert guard.can_launch("43", "debug") is True

    def test_different_agent_allowed(self):
        guard = LaunchGuard(cooldown_seconds=60)
        guard.record_launch("42", "debug")
        assert guard.can_launch("42", "summarizer") is True

    def test_cooldown_expires(self):
        guard = LaunchGuard(cooldown_seconds=1)
        guard.record_launch("42", "debug")
        assert guard.can_launch("42", "debug") is False
        time.sleep(1.1)
        assert guard.can_launch("42", "debug") is True

    def test_custom_guard_blocks(self):
        custom = MagicMock(return_value=False)
        guard = LaunchGuard(cooldown_seconds=0, custom_guard=custom)
        assert guard.can_launch("42", "debug") is False
        custom.assert_called_once_with("42", "debug")

    def test_custom_guard_allows(self):
        custom = MagicMock(return_value=True)
        guard = LaunchGuard(cooldown_seconds=0, custom_guard=custom)
        assert guard.can_launch("42", "debug") is True

    def test_custom_guard_exception_allows(self):
        """Custom guard raising an exception should default to allowing."""
        custom = MagicMock(side_effect=RuntimeError("boom"))
        guard = LaunchGuard(cooldown_seconds=0, custom_guard=custom)
        assert guard.can_launch("42", "debug") is True

    def test_record_and_count(self):
        guard = LaunchGuard(cooldown_seconds=60)
        assert guard.active_count == 0
        guard.record_launch("1", "triage")
        guard.record_launch("2", "debug")
        assert guard.active_count == 2

    def test_clear_specific_issue(self):
        guard = LaunchGuard(cooldown_seconds=60)
        guard.record_launch("1", "triage")
        guard.record_launch("2", "debug")
        cleared = guard.clear(issue_id="1")
        assert cleared == 1
        assert guard.active_count == 1

    def test_clear_all(self):
        guard = LaunchGuard(cooldown_seconds=60)
        guard.record_launch("1", "triage")
        guard.record_launch("2", "debug")
        cleared = guard.clear()
        assert cleared == 2
        assert guard.active_count == 0

    def test_cleanup_expired(self):
        guard = LaunchGuard(cooldown_seconds=1)
        guard.record_launch("1", "triage")
        time.sleep(1.1)
        guard.record_launch("2", "debug")
        cleaned = guard.cleanup_expired()
        assert cleaned == 1
        assert guard.active_count == 1
