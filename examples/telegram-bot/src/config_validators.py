"""Validation helpers for Telegram bot project configuration."""

from __future__ import annotations

import os
from enum import Enum
import logging
from typing import Any

from nexus.plugins.builtin.ai_runtime.provider_registry import parse_tool_preference

logger = logging.getLogger(__name__)


class _AIProviderEnum(Enum):
    COPILOT = "copilot"
    GEMINI = "gemini"
    CODEX = "codex"


def _validate_tool_preferences_block(
    payload: Any,
    *,
    strict: bool,
    label: str,
) -> None:
    if payload is None:
        return
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a mapping")

    for agent_name, value in payload.items():
        spec = parse_tool_preference(value, _AIProviderEnum)
        if getattr(spec, "valid", False):
            continue
        message = (
            f"{label}.{agent_name} is invalid: {getattr(spec, 'reason', 'parse error')} "
            f"(value={value!r})"
        )
        if strict:
            raise ValueError(message)
        logger.warning("%s", message)


def validate_project_config(config: dict[str, Any]) -> None:
    """Validate project configuration dict."""
    if not config:
        return
    strict_tool_prefs = os.getenv("AI_TOOL_PREFERENCES_STRICT", "false").lower() == "true"

    global_keys = {
        "nexus_dir",
        "workflow_definition_path",
        "projects",
        "task_types",
        "ai_tool_preferences",
        "operation_agents",
        "merge_queue",
        "workflow_chains",
        "final_agents",
        "issue_triage",
        "shared_agents_dir",
    }

    for project, proj_config in config.items():
        if project in global_keys:
            continue
        if not isinstance(proj_config, dict):
            raise ValueError(f"PROJECT_CONFIG['{project}'] must be a dict")
        if "workspace" not in proj_config:
            raise ValueError(f"PROJECT_CONFIG['{project}'] missing 'workspace' key")

        repos_list = proj_config.get("git_repos")
        if repos_list is not None:
            if not isinstance(repos_list, list):
                raise ValueError(f"PROJECT_CONFIG['{project}']['git_repos'] must be a list")
            for repo_name in repos_list:
                if not isinstance(repo_name, str) or not repo_name.strip():
                    raise ValueError(
                        f"PROJECT_CONFIG['{project}']['git_repos'] contains invalid repo entry"
                    )

        git_platform = str(proj_config.get("git_platform", "github")).lower().strip()
        if git_platform not in {"github", "gitlab"}:
            raise ValueError(
                f"PROJECT_CONFIG['{project}']['git_platform'] must be 'github' or 'gitlab'"
            )
        _validate_tool_preferences_block(
            proj_config.get("ai_tool_preferences"),
            strict=strict_tool_prefs,
            label=f"PROJECT_CONFIG['{project}']['ai_tool_preferences']",
        )

    _validate_tool_preferences_block(
        config.get("ai_tool_preferences"),
        strict=strict_tool_prefs,
        label="PROJECT_CONFIG['ai_tool_preferences']",
    )

    registry = config.get("projects")
    if registry is None:
        return
    if not isinstance(registry, dict) or not registry:
        raise ValueError("PROJECT_CONFIG['projects'] must be a non-empty mapping")

    for short_key, payload in registry.items():
        normalized_short = str(short_key).strip().lower()
        if not normalized_short:
            raise ValueError("PROJECT_CONFIG['projects'] contains empty short key")
        if not isinstance(payload, dict):
            raise ValueError(f"PROJECT_CONFIG['projects']['{normalized_short}'] must be a mapping")

        code = str(payload.get("code", "")).strip().lower()
        if not code:
            raise ValueError(
                f"PROJECT_CONFIG['projects']['{normalized_short}'] missing non-empty 'code'"
            )
        if code not in config or not isinstance(config.get(code), dict):
            raise ValueError(
                f"PROJECT_CONFIG['projects']['{normalized_short}']['code'] references unknown project '{code}'"
            )

        aliases = payload.get("aliases", [])
        if aliases is not None and not isinstance(aliases, list):
            raise ValueError(
                f"PROJECT_CONFIG['projects']['{normalized_short}']['aliases'] must be a list"
            )
        if isinstance(aliases, list):
            for alias in aliases:
                if not isinstance(alias, str) or not alias.strip():
                    raise ValueError(
                        f"PROJECT_CONFIG['projects']['{normalized_short}']['aliases'] contains invalid alias"
                    )
