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
