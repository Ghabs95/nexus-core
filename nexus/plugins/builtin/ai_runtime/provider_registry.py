from collections.abc import Iterable
from enum import Enum
from typing import Any


def parse_provider(candidate: Any, provider_enum: type[Enum]) -> Enum | None:
    """Parse a provider string into the configured provider enum."""
    value = str(candidate or "").strip().lower()
    if not value:
        return None
    for provider in provider_enum:
        if getattr(provider, "value", None) == value:
            return provider
    return None


def supports_analysis(tool: Any, *, gemini_provider: Any, copilot_provider: Any) -> bool:
    """Return whether the provider supports analysis tasks."""
    return tool in {gemini_provider, copilot_provider}


def unique_tools(order: Iterable[Any]) -> list[Any]:
    """Deduplicate provider order while preserving order."""
    unique: list[Any] = []
    seen: set[Any] = set()
    for tool in order:
        if tool in seen:
            continue
        unique.append(tool)
        seen.add(tool)
    return unique

