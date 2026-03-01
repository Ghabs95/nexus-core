from collections.abc import Mapping
from typing import Any, Callable


def _coerce_chat_agent_type(chat_config: Any) -> str:
    """Resolve chat config payload into a concrete agent_type string."""
    if isinstance(chat_config, str):
        return str(chat_config).strip()
    if isinstance(chat_config, list):
        for item in chat_config:
            if isinstance(item, str) and item.strip():
                return item.strip()
            if isinstance(item, dict):
                explicit = str(item.get("agent_type") or "").strip()
                if explicit:
                    return explicit
                for key in item:
                    normalized = str(key).strip()
                    if normalized:
                        return normalized
    if isinstance(chat_config, Mapping):
        explicit = str(chat_config.get("agent_type") or "").strip()
        if explicit:
            return explicit
        default_item = chat_config.get("default")
        if isinstance(default_item, str) and default_item.strip():
            return default_item.strip()
        if isinstance(default_item, Mapping):
            nested = str(default_item.get("agent_type") or "").strip()
            if nested:
                return nested
        for key in chat_config:
            normalized = str(key).strip()
            if normalized and normalized not in {"default", "agent_type"}:
                return normalized
    return ""


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
    system_operations: Mapping[str, Any],
    default_chat_agent_type: str,
    resolve_issue_override_agent: Callable[..., str],
    get_primary_tool: Callable[[str | None, str | None], Any],
    fallback_order_from_preferences_fn: Callable[[str | None], list[Any]],
    unique_tools: Callable[[list[Any]], list[Any]],
    supports_analysis: Callable[[Any], bool],
    default_tools: list[Any],
) -> list[Any]:
    """Resolve ordered provider attempts for analysis tasks."""
    task_key = str(task or "").strip().lower()
    mapped_agent = ""
    if task_key == "chat":
        mapped_agent = _coerce_chat_agent_type(system_operations.get(task_key))
        if not mapped_agent:
            mapped_agent = default_chat_agent_type
        if not mapped_agent:
            mapped_agent = str(system_operations.get("default") or "").strip()
    else:
        mapped_agent = str(
            system_operations.get(task_key) or system_operations.get("default") or ""
        ).strip()

    mapped_agent = resolve_issue_override_agent(
        task_key=task_key,
        mapped_agent=mapped_agent,
        text=text,
        system_operations=system_operations,
    )

    preferred = get_primary_tool(mapped_agent or None, project_name)
    base_order = fallback_order_from_preferences_fn(project_name)
    if not base_order:
        base_order = default_tools

    ordered = [preferred] + [tool for tool in base_order if tool != preferred]
    filtered = [tool for tool in unique_tools(ordered) if supports_analysis(tool)]
    if not filtered:
        filtered = default_tools[:2] if len(default_tools) >= 2 else default_tools

    if not fallback_enabled:
        return filtered[:1]
    return filtered
