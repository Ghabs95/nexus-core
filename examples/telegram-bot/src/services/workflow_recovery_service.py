"""Workflow recovery loop actions extracted from inbox_processor."""

from collections.abc import Callable


def run_stuck_agents_cycle(
    *,
    logger,
    base_dir: str,
    scope: str,
    orchestrator_check_stuck_agents: Callable[[str], None],
    recover_orphaned_running_agents: Callable[[], int],
    recover_unmapped_issues_from_completions: Callable[[], int],
    clear_polling_failures: Callable[[str], None],
    record_polling_failure: Callable[[str, Exception], None],
) -> None:
    """Run one periodic stuck-agent + recovery cycle with shared failure bookkeeping."""
    try:
        orchestrator_check_stuck_agents(base_dir)
        recovered = recover_orphaned_running_agents()
        if recovered:
            logger.info("Recovered %s orphaned workflow issue(s)", recovered)
        recovered_from_signals = recover_unmapped_issues_from_completions()
        if recovered_from_signals:
            logger.info(
                "Recovered %s issue(s) from completion signals",
                recovered_from_signals,
            )
        clear_polling_failures(scope)
    except Exception as exc:
        logger.error("Error in check_stuck_agents: %s", exc)
        record_polling_failure(scope, exc)
