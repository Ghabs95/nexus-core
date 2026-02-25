from services import command_contract


def test_command_parity_report_contains_expected_sections():
    report = command_contract.get_command_parity_report()

    assert "telegram_only" in report
    assert "discord_only" in report
    assert "shared" in report
    for required in {"chat", "track", "tracked", "myissues", "status"}:
        assert required in report["shared"]


def test_validate_command_parity_non_strict_returns_report():
    report = command_contract.validate_command_parity(strict=False)

    assert isinstance(report, dict)
    assert "telegram_only" in report


def test_validate_command_parity_strict_raises_on_mismatch():
    try:
        command_contract.validate_command_parity(strict=True)
        assert False, "Expected strict parity mismatch to raise ValueError"
    except ValueError as exc:
        assert "Command parity mismatch detected" in str(exc)


def test_validate_required_command_interface_passes_for_required_set():
    command_contract.validate_required_command_interface()
