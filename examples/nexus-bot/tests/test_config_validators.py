import pytest

from config_validators import validate_project_config


def test_validate_project_config_rejects_invalid_tool_pref_shape():
    payload = {
        "model_profiles": {"small": {"gemini": "gemini-2.0-flash"}},
        "ai_tool_preferences": {"triage": "gemini"},
        "nexus": {"workspace": "x", "agents_dir": "a", "git_platform": "github"},
    }
    with pytest.raises(ValueError):
        validate_project_config(payload)


def test_validate_project_config_rejects_unknown_profile_reference():
    payload = {
        "model_profiles": {"small": {"gemini": "gemini-2.0-flash"}},
        "ai_tool_preferences": {"writer": {"provider": "gemini", "profile": "large"}},
        "nexus": {"workspace": "x", "agents_dir": "a", "git_platform": "github"},
    }
    with pytest.raises(ValueError):
        validate_project_config(payload)


def test_validate_project_config_rejects_invalid_profile_priority_provider():
    payload = {
        "model_profiles": {"fast": {"gemini": "gemini-2.0-flash"}},
        "profile_provider_priority": {"fast": ["invalid-provider"]},
        "ai_tool_preferences": {"writer": {"provider": "gemini", "profile": "fast"}},
        "nexus": {"workspace": "x", "agents_dir": "a", "git_platform": "github"},
    }
    with pytest.raises(ValueError):
        validate_project_config(payload)
