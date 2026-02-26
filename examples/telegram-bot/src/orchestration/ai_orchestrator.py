"""Compatibility wrapper for AI orchestrator backed by nexus-core plugin."""

from collections.abc import Mapping
from typing import Any

from orchestration.plugin_runtime import clear_cached_plugin, get_profiled_plugin

from nexus.plugins.builtin.ai_runtime_plugin import (
    AIOrchestrator,
)

_orchestrator: AIOrchestrator | None = None


def _resolve_tasks_logs_dir(workspace: str, project: str | None = None) -> str:
    """Resolve tasks logs directory via config."""
    from config import get_tasks_logs_dir

    return get_tasks_logs_dir(workspace, project)


def get_orchestrator(config: Any | None = None) -> AIOrchestrator:
    """Get or create global orchestrator instance."""
    global _orchestrator
    if _orchestrator is None:
        if config is None:
            overrides: dict[str, Any] = {}
        elif isinstance(config, dict):
            overrides = dict(config)
        elif isinstance(config, Mapping) or hasattr(config, "items"):
            overrides = dict(config.items())
        elif hasattr(config, "get"):
            keys = (
                "copilot_cli_path",
                "gemini_cli_path",
                "gemini_model",
                "codex_cli_path",
                "codex_model",
                "tool_preferences",
                "tool_preferences_resolver",
                "operation_agents",
                "operation_agents_resolver",
                "chat_agent_types_resolver",
                "fallback_enabled",
                "rate_limit_ttl",
                "max_retries",
                "analysis_timeout",
                "refine_description_timeout",
                "transcription_primary",
                "gemini_transcription_timeout",
                "copilot_transcription_timeout",
                "whisper_model",
                "whisper_language",
                "whisper_languages",
            )
            overrides = {key: config.get(key) for key in keys if config.get(key) is not None}
        else:
            overrides = dict(config)
        overrides["tasks_logs_dir_resolver"] = _resolve_tasks_logs_dir
        plugin = get_profiled_plugin(
            "ai_runtime_default",
            overrides=overrides,
            cache_key="ai:orchestrator",
        )
        _orchestrator = plugin or AIOrchestrator(overrides)
    return _orchestrator


def reset_orchestrator() -> None:
    """Reset global orchestrator (for testing)."""
    global _orchestrator
    _orchestrator = None
    clear_cached_plugin("ai:orchestrator")
