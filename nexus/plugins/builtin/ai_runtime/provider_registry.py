from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any


@dataclass(frozen=True)
class ToolPreferenceSpec:
    """Parsed tool preference specification."""

    provider: Enum | None
    provider_name: str
    model: str
    valid: bool
    reason: str = ""
    raw: Any = None


def _extract_provider_and_model(candidate: Any) -> tuple[str, str, str]:
    """Return provider/model/parse_error from string or mapping candidate."""
    if isinstance(candidate, Mapping):
        provider = str(
            candidate.get("provider")
            or candidate.get("tool")
            or candidate.get("name")
            or ""
        ).strip()
        model = str(candidate.get("model") or candidate.get("model_name") or "").strip()
        if not provider:
            return "", "", "missing provider in mapping"
        return provider, model, ""

    value = str(candidate or "").strip()
    if not value:
        return "", "", "empty provider"

    if "[" not in value:
        return value, "", ""

    # Supported syntax: provider["model"] or provider['model']
    start = value.find("[")
    end = value.rfind("]")
    if start <= 0 or end <= start:
        return "", "", f"invalid model syntax: {value}"

    provider = value[:start].strip()
    model_literal = value[start + 1 : end].strip()
    if not provider:
        return "", "", f"missing provider name: {value}"
    if len(model_literal) < 2:
        return "", "", f"missing model literal: {value}"
    if (model_literal[0], model_literal[-1]) not in {('"', '"'), ("'", "'")}:
        return "", "", f"model must be quoted: {value}"

    model = model_literal[1:-1].strip()
    if not model:
        return "", "", f"empty model name: {value}"
    return provider, model, ""


def parse_tool_preference(candidate: Any, provider_enum: type[Enum]) -> ToolPreferenceSpec:
    """Parse provider/model preference from config entry."""
    provider_name, model, error = _extract_provider_and_model(candidate)
    normalized = provider_name.lower().strip()
    if error:
        return ToolPreferenceSpec(
            provider=None,
            provider_name=normalized,
            model=model,
            valid=False,
            reason=error,
            raw=candidate,
        )
    if not normalized:
        return ToolPreferenceSpec(
            provider=None,
            provider_name="",
            model=model,
            valid=False,
            reason="empty provider",
            raw=candidate,
        )

    for provider in provider_enum:
        if getattr(provider, "value", None) == normalized:
            return ToolPreferenceSpec(
                provider=provider,
                provider_name=normalized,
                model=model,
                valid=True,
                raw=candidate,
            )

    return ToolPreferenceSpec(
        provider=None,
        provider_name=normalized,
        model=model,
        valid=False,
        reason=f"unsupported provider '{normalized}'",
        raw=candidate,
    )


def parse_provider(candidate: Any, provider_enum: type[Enum]) -> Enum | None:
    """Parse a provider string into the configured provider enum."""
    spec = parse_tool_preference(candidate, provider_enum)
    return spec.provider if spec.valid else None


def supports_analysis(
    tool: Any,
    *,
    gemini_provider: Any,
    copilot_provider: Any,
    codex_provider: Any | None = None,
) -> bool:
    """Return whether the provider supports analysis tasks."""
    supported = {gemini_provider, copilot_provider}
    if codex_provider is not None:
        supported.add(codex_provider)
    return tool in supported


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
