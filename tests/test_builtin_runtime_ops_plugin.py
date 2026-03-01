"""Tests for built-in runtime ops/process guard plugin."""

import subprocess

from nexus.plugins.builtin.runtime_ops_plugin import RuntimeOpsPlugin


class _Result:
    def __init__(self, stdout: str = "", returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode


def test_find_issue_processes_parses_pgrep_output(monkeypatch):
    plugin = RuntimeOpsPlugin({"process_name": "copilot"})

    def _fake_run(cmd, text, capture_output, timeout, check):
        assert cmd[0:2] == ["pgrep", "-af"]
        return _Result("1234 copilot -p prompt issues/42\n5678 copilot --other issues/42\n")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    matches = plugin.find_issue_processes("42")

    assert matches == [
        {"pid": 1234, "command": "copilot -p prompt issues/42"},
        {"pid": 5678, "command": "copilot --other issues/42"},
    ]
    assert plugin.find_agent_pid_for_issue("42") == 1234


def test_find_issue_processes_returns_empty_on_error(monkeypatch):
    plugin = RuntimeOpsPlugin()

    def _fake_run(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    assert plugin.find_issue_processes("42") == []
    assert plugin.find_agent_pid_for_issue("42") is None


def test_kill_process_and_stop_issue_agent(monkeypatch):
    plugin = RuntimeOpsPlugin()

    def _fake_run(cmd, check, timeout, capture_output, text):
        if cmd[0] == "kill":
            return _Result("", 0)
        return _Result("9876 copilot issues/55\n", 0)

    monkeypatch.setattr(subprocess, "run", _fake_run)

    assert plugin.kill_process(9876, force=False) is True
    assert plugin.kill_process(9876, force=True) is True
    assert plugin.stop_issue_agent("55", force=True) == 9876


def test_is_issue_process_running(monkeypatch):
    plugin = RuntimeOpsPlugin()

    def _fake_run_present(*_args, **_kwargs):
        return _Result("777 copilot issues/9\n")

    monkeypatch.setattr(subprocess, "run", _fake_run_present)
    assert plugin.is_issue_process_running("9") is True

    def _fake_run_missing(*_args, **_kwargs):
        return _Result("")

    monkeypatch.setattr(subprocess, "run", _fake_run_missing)
    assert plugin.is_issue_process_running("9") is False


def test_find_issue_processes_skips_ide_extension_host(monkeypatch):
    plugin = RuntimeOpsPlugin({"process_name": "copilot|codex"})

    def _fake_run(cmd, text, capture_output, timeout, check):
        assert cmd[0:2] == ["pgrep", "-af"]
        return _Result(
            "1111 /Applications/Visual Studio Code.app/Contents/MacOS/Electron "
            "--ms-enable-electron-run-as-node extensionHost issues/42 codex\n"
            "2222 codex exec --issue https://github.com/acme/repo/issues/42\n"
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)

    matches = plugin.find_issue_processes("42")

    assert matches == [{"pid": 2222, "command": "codex exec --issue https://github.com/acme/repo/issues/42"}]


def test_find_issue_processes_accepts_wrapped_cli_invocation(monkeypatch):
    plugin = RuntimeOpsPlugin({"process_name": "copilot|codex"})

    def _fake_run(cmd, text, capture_output, timeout, check):
        assert cmd[0:2] == ["pgrep", "-af"]
        return _Result("3333 bash -lc codex exec --issue https://github.com/acme/repo/issues/42\n")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    matches = plugin.find_issue_processes("42")

    assert matches == [
        {"pid": 3333, "command": "bash -lc codex exec --issue https://github.com/acme/repo/issues/42"}
    ]
