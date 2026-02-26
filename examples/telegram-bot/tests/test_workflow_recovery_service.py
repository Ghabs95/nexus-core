from unittest.mock import MagicMock

from services.workflow_recovery_service import run_stuck_agents_cycle


def test_run_stuck_agents_cycle_success_logs_and_clears():
    logger = MagicMock()
    calls = {"orchestrator": 0, "orphans": 0, "signals": 0, "clear": 0, "record": 0}

    run_stuck_agents_cycle(
        logger=logger,
        base_dir="/tmp/base",
        scope="stuck-agents:loop",
        orchestrator_check_stuck_agents=lambda _base: calls.__setitem__(
            "orchestrator", calls["orchestrator"] + 1
        ),
        recover_orphaned_running_agents=lambda: calls.__setitem__("orphans", calls["orphans"] + 1)
        or 2,
        recover_unmapped_issues_from_completions=lambda: calls.__setitem__(
            "signals", calls["signals"] + 1
        )
        or 1,
        clear_polling_failures=lambda _scope: calls.__setitem__("clear", calls["clear"] + 1),
        record_polling_failure=lambda _scope, _exc: calls.__setitem__(
            "record", calls["record"] + 1
        ),
    )

    assert calls == {"orchestrator": 1, "orphans": 1, "signals": 1, "clear": 1, "record": 0}
    assert logger.info.call_count >= 2


def test_run_stuck_agents_cycle_records_failure():
    logger = MagicMock()
    recorded = []

    def _boom(_base):
        raise RuntimeError("boom")

    run_stuck_agents_cycle(
        logger=logger,
        base_dir="/tmp/base",
        scope="stuck-agents:loop",
        orchestrator_check_stuck_agents=_boom,
        recover_orphaned_running_agents=lambda: 0,
        recover_unmapped_issues_from_completions=lambda: 0,
        clear_polling_failures=lambda _scope: None,
        record_polling_failure=lambda scope, exc: recorded.append((scope, str(exc))),
    )

    assert recorded == [("stuck-agents:loop", "boom")]
    logger.error.assert_called_once()
