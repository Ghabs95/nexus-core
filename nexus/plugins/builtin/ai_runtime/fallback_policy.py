from collections.abc import Mapping
from typing import Any, Callable


def fallback_order_from_preferences(
    *,
    resolved_tool_preferences: Mapping[str, Any],
    parse_provider: Callable[[Any], Any | None],
) -> list[Any]:
    """Build ordered unique provider list from tool preferences values."""
    ordered: list[Any] = []
    seen: set[Any] = set()
    for provider_name in resolved_tool_preferences.values():
        provider = parse_provider(provider_name)
        if provider and provider not in seen:
            ordered.append(provider)
            seen.add(provider)
    return ordered


def resolve_analysis_tool_order(
    *,
    task: str,
    text: str,
    project_name: str | None,
    fallback_enabled: bool,
    operation_agents: Mapping[str, Any],
    default_chat_agent_type: str,
    resolve_issue_override_agent: Callable[..., str],
    get_primary_tool: Callable[[str | None, str | None], Any],
    fallback_order_from_preferences_fn: Callable[[str | None], list[Any]],
    unique_tools: Callable[[list[Any]], list[Any]],
    supports_analysis: Callable[[Any], bool],
    gemini_provider: Any,
    copilot_provider: Any,
) -> list[Any]:
    """Resolve ordered provider attempts for analysis tasks."""
    task_key = str(task or "").strip().lower()
    mapped_agent = ""
    if task_key == "chat":
        mapped_agent = str(operation_agents.get(task_key) or "").strip()
        if not mapped_agent:
            mapped_agent = default_chat_agent_type
        if not mapped_agent:
            mapped_agent = str(operation_agents.get("default") or "").strip()
    else:
        mapped_agent = str(operation_agents.get(task_key) or operation_agents.get("default") or "").strip()

    mapped_agent = resolve_issue_override_agent(
        task_key=task_key,
        mapped_agent=mapped_agent,
        text=text,
        operation_agents=operation_agents,
    )

    preferred = get_primary_tool(mapped_agent or None, project_name)
    base_order = fallback_order_from_preferences_fn(project_name)
    if not base_order:
        base_order = [gemini_provider, copilot_provider]

    ordered = [preferred] + [tool for tool in base_order if tool != preferred]
    filtered = [tool for tool in unique_tools(ordered) if supports_analysis(tool)]
    if not filtered:
        filtered = [gemini_provider, copilot_provider]

    if not fallback_enabled:
        return filtered[:1]
    return filtered
