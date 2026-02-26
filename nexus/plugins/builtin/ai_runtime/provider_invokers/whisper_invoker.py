import os
from typing import Any, Callable


def import_whisper_module() -> Any:
    import whisper  # type: ignore

    return whisper


def transcribe_with_local_whisper(
    *,
    audio_file_path: str,
    current_model_instance: Any,
    current_model_name: str,
    configured_model: str,
    whisper_language: str | None,
    whisper_languages: list[str],
    normalize_local_whisper_model_name: Callable[[str], str],
    import_whisper: Callable[[], Any] = import_whisper_module,
    tool_unavailable_error: type[Exception],
    logger: Any,
) -> dict[str, Any]:
    if not os.path.exists(audio_file_path):
        raise ValueError(f"Audio file not found: {audio_file_path}")

    try:
        whisper = import_whisper()
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise tool_unavailable_error(
            "Local Whisper requires the 'openai-whisper' package. "
            "Install with: pip install openai-whisper"
        ) from exc

    model_name = normalize_local_whisper_model_name(configured_model)
    model_instance = current_model_instance
    if model_instance is None or current_model_name != model_name:
        logger.info("🔧 Loading local Whisper model: %s", model_name)
        model_instance = whisper.load_model(model_name)

    logger.info("🎧 Transcribing with local Whisper model %s: %s", model_name, audio_file_path)
    try:
        transcribe_kwargs: dict[str, Any] = {"fp16": False}
        if whisper_language:
            transcribe_kwargs["language"] = whisper_language
        elif whisper_languages:
            transcribe_kwargs["language"] = whisper_languages[0]
        response = model_instance.transcribe(audio_file_path, **transcribe_kwargs)
    except Exception as exc:
        raise Exception(f"Local Whisper error: {exc}") from exc

    detected_language = ""
    if isinstance(response, dict):
        detected_language = str(response.get("language") or "").strip().lower()
    if whisper_languages and detected_language and detected_language not in whisper_languages:
        raise Exception(
            f"Detected language {detected_language!r} is outside allowed set: {whisper_languages}"
        )

    text = response.get("text") if isinstance(response, dict) else None
    text = str(text or "").strip()
    if not text:
        raise Exception("Whisper returned empty transcription")

    return {
        "text": text,
        "model_instance": model_instance,
        "model_name": model_name,
    }
