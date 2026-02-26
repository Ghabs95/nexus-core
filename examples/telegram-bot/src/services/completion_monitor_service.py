"""Completion monitoring loop wrappers extracted from inbox_processor."""

from collections.abc import Callable


def run_completion_monitor_cycle(*, post_completion_comments_from_logs: Callable[[], None]) -> None:
    """Run one completion-monitor cycle."""
    post_completion_comments_from_logs()
