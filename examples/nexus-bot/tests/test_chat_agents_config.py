from nexus.core.chat_agents_schema import (
    get_default_project_chat_agent_type,
    get_project_chat_agent_config,
    get_project_chat_agent_types,
    get_project_chat_agents,
)


def test_get_project_chat_agents_from_mapping_preserves_order():
    project_cfg = {
        "system_operations": {
            "chat": {
                "business": {"label": "Business", "context_path": "business-os"},
                "marketing": {"label": "Marketing", "context_path": "marketing-os"},
            }
        },
    }

    entries = get_project_chat_agents(project_cfg)

    assert [entry["agent_type"] for entry in entries] == ["business", "marketing"]
    assert entries[0]["label"] == "Business"
    assert entries[1]["context_path"] == "marketing-os"


def test_get_project_chat_agents_from_list_supports_both_shapes():
    project_cfg = {
        "system_operations": {
            "chat": [
                {"business": {"label": "Business"}},
                {"agent_type": "marketing", "label": "Marketing"},
            ]
        },
    }

    entries = get_project_chat_agents(project_cfg)

    assert [entry["agent_type"] for entry in entries] == ["business", "marketing"]
    assert entries[0]["label"] == "Business"
    assert entries[1]["label"] == "Marketing"


def test_get_project_chat_agent_types_returns_ordered_types():
    project_cfg = {
        "system_operations": {
            "chat": [
                {"agent_type": "business"},
                {"agent_type": "marketing"},
                {"agent_type": "triage"},
            ]
        },
    }

    assert get_project_chat_agent_types(project_cfg) == ["business", "marketing", "triage"]


def test_get_default_project_chat_agent_type_uses_first_entry():
    project_cfg = {
        "system_operations": {"chat": {"business": {}, "marketing": {}}},
    }

    assert get_default_project_chat_agent_type(project_cfg) == "business"


def test_get_project_chat_agent_config_returns_payload_only():
    project_cfg = {
        "system_operations": {
            "chat": {
                "business": {"label": "Business", "context_path": "business-os"},
                "marketing": {"label": "Marketing"},
            }
        },
    }

    payload = get_project_chat_agent_config(project_cfg, "business")

    assert payload == {"label": "Business", "context_path": "business-os"}


def test_get_project_chat_agent_config_returns_empty_when_missing():
    project_cfg = {"system_operations": {"chat": {"marketing": {"label": "Marketing"}}}}

    assert get_project_chat_agent_config(project_cfg, "business") == {}


def test_get_project_chat_agents_skips_malformed_entries():
    project_cfg = {
        "system_operations": {
            "chat": [
                "business",
                {"agent_type": ""},
                {"agent_type": "marketing", "label": "Marketing"},
                {"invalid": {}, "extra": {}},
                {"business": {"label": "Business"}},
                42,
            ]
        },
    }

    entries = get_project_chat_agents(project_cfg)

    assert [entry["agent_type"] for entry in entries] == ["marketing", "business"]
    assert entries[0]["label"] == "Marketing"
    assert entries[1]["label"] == "Business"
