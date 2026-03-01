from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any


@dataclass(frozen=True)
class ToolPreferenceSpec:
    """Parsed tool preference specification."""

    provider: Enum | None
    provider_name: str
    profile: str
    valid: bool
    reason: str = ""
    raw: Any = None


def _extract_provider_and_profile(candidate: Any) -> tuple[str, str, str]:
    """Return provider/profile/parse_error from mapping candidate."""
    if not isinstance(candidate, Mapping):
        return "", "", "tool preference must be a mapping"

    provider = str(candidate.get("provider") or "auto").strip().lower()
    profile = str(candidate.get("profile") or "").strip()

    if not profile:
        return "", "", "missing profile in mapping"
    if not provider:
        provider = "auto"
    return provider, profile, ""


def parse_tool_preference(candidate: Any, provider_enum: type[Enum]) -> ToolPreferenceSpec:
    """Parse provider/profile preference from config entry."""
    provider_name, profile, error = _extract_provider_and_profile(candidate)
    normalized = provider_name.strip().lower()
    if error:
        return ToolPreferenceSpec(
            provider=None,
            provider_name=normalized,
            profile=profile,
            valid=False,
            reason=error,
            raw=candidate,
        )
    if normalized == "auto":
        return ToolPreferenceSpec(
            provider=None,
            provider_name="auto",
            profile=profile,
            valid=True,
            raw=candidate,
        )

    for provider in provider_enum:
        if getattr(provider, "value", None) == normalized:
            return ToolPreferenceSpec(
                provider=provider,
                provider_name=normalized,
                profile=profile,
                valid=True,
                raw=candidate,
            )

    return ToolPreferenceSpec(
        provider=None,
        provider_name=normalized,
        profile=profile,
        valid=False,
        reason=f"unsupported provider '{normalized}'",
        raw=candidate,
    )


def parse_provider(candidate: Any, provider_enum: type[Enum]) -> Enum | None:
    """Parse a provider reference into the configured provider enum."""
    spec = parse_tool_preference(candidate, provider_enum)
    return spec.provider if spec.valid else None


def supports_analysis(
    tool: Any,
    supported_tools: Iterable[Any],
) -> bool:
    """Return whether the provider supports analysis tasks."""
    return tool in supported_tools


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
