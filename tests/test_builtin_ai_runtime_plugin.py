"""Tests for built-in AI runtime plugin."""

import subprocess

from nexus.plugins.builtin.ai_runtime_plugin import AIOrchestrator, AIProvider, RateLimitedError


class _FakeCompletedProcess:
    def __init__(self, stdout: str = "{\"text\": \"ok\"}"):
        self.returncode = 0
        self.stdout = stdout
        self.stderr = ""


def test_refine_description_default_keeps_original_text(monkeypatch):
    orchestrator = AIOrchestrator()

    monkeypatch.setattr(orchestrator, "get_fallback_tool", lambda _primary: AIProvider.COPILOT)

    def _raise_rate_limit(*_args, **_kwargs):
        raise RateLimitedError("gemini quota")

    def _raise_timeout(*_args, **_kwargs):
        raise Exception("Copilot analysis timed out")

    monkeypatch.setattr(orchestrator, "_run_gemini_cli_analysis", _raise_rate_limit)
    monkeypatch.setattr(orchestrator, "_run_copilot_analysis", _raise_timeout)

    source_text = "Preserve this exact fallback text"
    result = orchestrator.run_text_to_speech_analysis(source_text, task="refine_description")

    assert result["text"] == source_text


def test_copilot_analysis_timeout_respects_config(monkeypatch):
    orchestrator = AIOrchestrator({"analysis_timeout": 45})
    captured = {"timeout": None}

    monkeypatch.setattr(orchestrator, "check_tool_available", lambda _tool: True)

    def _fake_run(*_args, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return _FakeCompletedProcess('{"project": "nexus", "type": "feature", "issue_name": "abc"}')

    monkeypatch.setattr(subprocess, "run", _fake_run)

    result = orchestrator._run_copilot_analysis("input", "classify")

    assert captured["timeout"] == 45
    assert result["project"] == "nexus"


class TestStripCliToolOutput:
    """Tests for _strip_cli_tool_output."""

    def test_no_op_true_block(self):
        text = (
            "● No-op\n"
            "  $ true\n"
            "  └ 1 line...\n"
            "\n"
            "Add a dry-run validation mode."
        )
        assert AIOrchestrator._strip_cli_tool_output(text) == "Add a dry-run validation mode."

    def test_multiple_tool_blocks(self):
        text = (
            "● List directory .\n"
            "  └ 7 files found\n"
            "\n"
            "● Read file.py\n"
            "  └ 100 lines read\n"
            "\n"
            "The answer is 42."
        )
        assert AIOrchestrator._strip_cli_tool_output(text) == "The answer is 42."

    def test_no_artifacts_passthrough(self):
        text = "Clean text\nwith no tool output."
        assert AIOrchestrator._strip_cli_tool_output(text) == text

    def test_mixed_content(self):
        text = (
            "Summary:\n"
            "● No-op\n"
            "  $ true\n"
            "  └ 1 line...\n"
            "\n"
            "Details here."
        )
        result = AIOrchestrator._strip_cli_tool_output(text)
        assert "No-op" not in result
        assert "$ true" not in result
        assert "Summary:" in result
        assert "Details here." in result

    def test_empty_string(self):
        assert AIOrchestrator._strip_cli_tool_output("") == ""

    def test_json_not_stripped(self):
        text = '{"project": "nexus", "type": "feature"}'
        assert AIOrchestrator._strip_cli_tool_output(text) == text
