import pytest

from config_validators import validate_project_config


def test_validate_project_config_allows_invalid_tool_pref_in_warn_mode(monkeypatch):
    monkeypatch.setenv("AI_TOOL_PREFERENCES_STRICT", "false")
    payload = {
        "ai_tool_preferences": {"triage": 'gemini["valid-model"]', "writer": "oops["},
        "nexus": {"workspace": "x", "agents_dir": "a", "git_platform": "github"},
    }
    validate_project_config(payload)


def test_validate_project_config_rejects_invalid_tool_pref_in_strict_mode(monkeypatch):
    monkeypatch.setenv("AI_TOOL_PREFERENCES_STRICT", "true")
    payload = {
        "ai_tool_preferences": {"writer": "oops["},
        "nexus": {"workspace": "x", "agents_dir": "a", "git_platform": "github"},
    }
    with pytest.raises(ValueError):
        validate_project_config(payload)
