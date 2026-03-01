import os
import re
from collections.abc import Callable, Mapping
from typing import Any, Iterable


def resolve_transcription_attempts(
    *,
    project_name: str | None,
    system_operations: Mapping[str, Any],
    fallback_enabled: bool,
    fallback_provider: str,
    get_primary_tool: Callable[[str, str | None], Any],
    fallback_order_from_preferences_fn: Callable[[str | None], list[Any]],
    unique_tools: Callable[[list[Any]], list[Any]],
    supported_providers: Iterable[Any],
    whisper_name: str = "whisper",
    warn_unsupported_mapped_provider: Callable[[str, str], None] | None = None,
) -> list[str]:
    """Resolve ordered transcription attempts as provider names."""
    mapped_agent = str(system_operations.get("transcribe_audio") or "").strip()

    supported_set = set(supported_providers)

    if mapped_agent:
        mapped_primary = get_primary_tool(mapped_agent, project_name)
        if mapped_primary in supported_set:
            base_order = [
                tool
                for tool in fallback_order_from_preferences_fn(project_name)
                if tool in supported_set
            ]
            if not base_order:
                base_order = list(supported_providers)
            ordered = [mapped_primary] + [tool for tool in base_order if tool != mapped_primary]
            normalized = [tool.value for tool in unique_tools(ordered)]
            if not fallback_enabled:
                return normalized[:1]
            return normalized

        if warn_unsupported_mapped_provider:
            warn_unsupported_mapped_provider(
                getattr(mapped_primary, "value", str(mapped_primary)), mapped_agent
            )

    defaults = [p.value for p in supported_providers if hasattr(p, "value")]
    primary = str(fallback_provider or (defaults[0] if defaults else "")).strip().lower()
    # Default fallback chain if no mapping
    if primary == whisper_name:
        return [whisper_name] + defaults if fallback_enabled else [whisper_name]
    if primary in defaults:
        idx = defaults.index(primary)
        ordered_defaults = [primary] + [d for d in defaults if d != primary]
        return ordered_defaults if fallback_enabled else [primary]
    return defaults if fallback_enabled else defaults[:1]


def normalize_local_whisper_model_name(configured_model: str) -> str:
    model_name = (configured_model or "").strip().lower()
    if not model_name:
        return "base"
    if model_name in {"whisper-1", "gpt-4o-mini-transcribe", "gpt-4o-transcribe"}:
        return "base"
    return model_name


def is_transcription_refusal(text: str) -> bool:
    normalized = (text or "").lower().strip()
    if not normalized:
        return True

    refusal_markers = [
        "cannot directly transcribe audio",
        "can't directly transcribe audio",
        "cannot transcribe audio",
        "can't transcribe audio",
        "unable to transcribe audio",
        "capabilities are limited to text-based",
        "i do not have the ability to listen",
        "as a text-based ai",
        "i can't access audio",
        "i cannot access audio",
    ]
    return any(marker in normalized for marker in refusal_markers)


def is_non_transcription_artifact(text: str, audio_file_path: str) -> bool:
    normalized = (text or "").strip()
    if not normalized:
        return True

    if is_transcription_refusal(normalized):
        return True

    audio_basename = os.path.basename(audio_file_path).lower()
    lowered = normalized.lower()

    if lowered == audio_basename:
        return True
    if lowered == f"file: {audio_basename}":
        return True
    if re.fullmatch(r"file:\s*[^\n\r]+\.(ogg|mp3|wav|m4a)\s*", lowered):
        return True
    if "permission denied and could not request permission from user" in lowered:
        return True
    if "i'm unable to transcribe the audio file" in lowered:
        return True
    if re.search(r"(?m)^\$\s", normalized):
        return True
    if re.search(r"(?m)^✗\s", normalized):
        return True

    debug_markers = [
        "check for transcription tools",
        "check whisper availability",
        "transcribe with whisper",
        "install whisper",
        "pip install openai-whisper",
        "which whisper",
        "which ffmpeg",
    ]
    return bool(any(marker in lowered for marker in debug_markers))


def run_transcription_attempts(
    *,
    attempts: list[str],
    audio_file_path: str,
    transcribers_map: Mapping[str, Callable[[str], str | None]],
    rate_limited_error_type: type[Exception],
    record_rate_limit_with_context: Callable[[Any, Exception, str], None],
    provider_to_tool: Mapping[str, Any],
    logger: Any,
) -> str | None:
    """Execute ordered transcription attempts with fallback and rate-limit recording."""
    for tool_name in attempts:
        try:
            handler = transcribers_map.get(tool_name)
            if not handler:
                continue

            text = handler(audio_file_path)

            if text:
                logger.info("✅ Transcription successful with %s", tool_name)
                return text
        except rate_limited_error_type as exc:
            ai_tool = provider_to_tool.get(tool_name)
            if ai_tool:
                record_rate_limit_with_context(ai_tool, exc, "transcribe")
            else:
                logger.warning("⚠️  %s transcription failed (rate-limited): %s", tool_name, exc)
        except Exception as exc:
            logger.warning("⚠️  %s transcription failed: %s", tool_name, exc)

    logger.error("❌ All transcription tools failed (attempted: %s)", attempts)
    return None
