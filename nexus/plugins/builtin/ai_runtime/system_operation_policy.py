from collections.abc import Mapping
from typing import Any


def resolve_issue_override_agent(
    *,
    task_key: str,
    mapped_agent: str,
    text: str,
    system_operations: Mapping[str, Any] | None,
    looks_like_bug_issue: Any,
) -> str:
    """Apply issue/bug-specific operation-agent override when configured."""
    if not looks_like_bug_issue(text):
        return mapped_agent
    if not isinstance(system_operations, Mapping):
        return mapped_agent

    overrides = system_operations.get("overrides")
    if not isinstance(overrides, Mapping):
        return mapped_agent

    issue_overrides = overrides.get("issue")
    if not isinstance(issue_overrides, Mapping):
        return mapped_agent

    override_agent = str(
        issue_overrides.get(task_key) or issue_overrides.get("default") or ""
    ).strip()
    return override_agent or mapped_agent
