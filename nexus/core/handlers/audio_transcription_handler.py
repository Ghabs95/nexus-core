"""Audio transcription handlers extracted from telegram_bot."""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class AudioTranscriptionDeps:
    logger: Any
    transcribe_audio: Callable[[str], str | None]


_AUDIO_FILENAME_RE = re.compile(r"\.(ogg|oga|opus|mp3|m4a|wav|flac|aac|webm|mp4)$", re.IGNORECASE)
_AUDIO_SUFFIX_BY_MIME = {
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/aac": ".aac",
    "audio/flac": ".flac",
    "audio/webm": ".webm",
}


def is_audio_attachment(*, content_type: str | None, filename: str | None) -> bool:
    mime = str(content_type or "").strip().lower()
    if mime.startswith("audio/"):
        return True
    return bool(_AUDIO_FILENAME_RE.search(str(filename or "")))


def _resolve_audio_suffix(*, content_type: str | None, filename: str | None) -> str:
    mime = str(content_type or "").strip().lower()
    if mime in _AUDIO_SUFFIX_BY_MIME:
        return _AUDIO_SUFFIX_BY_MIME[mime]
    match = _AUDIO_FILENAME_RE.search(str(filename or ""))
    if match:
        return "." + str(match.group(1)).lower()
    return ".ogg"


def transcribe_audio_bytes(
    *,
    audio_bytes: bytes,
    suffix: str,
    deps: AudioTranscriptionDeps,
) -> str | None:
    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp_file:
            temp_path = tmp_file.name
            tmp_file.write(audio_bytes)

        deps.logger.info("🎧 Transcribing audio with orchestrator...")
        text = deps.transcribe_audio(temp_path)
        if text:
            cleaned = str(text).strip()
            deps.logger.info("✅ Transcription successful (%s chars)", len(cleaned))
            return cleaned
        deps.logger.error("❌ Transcription failed")
        return None
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                deps.logger.warning("Failed to clean temp audio file: %s", temp_path)


async def transcribe_discord_attachment(
    attachment: Any,
    deps: AudioTranscriptionDeps,
) -> str | None:
    content_type = getattr(attachment, "content_type", None)
    filename = getattr(attachment, "filename", None)
    if not is_audio_attachment(content_type=content_type, filename=filename):
        return None

    try:
        audio_bytes = await attachment.read()
    except Exception as exc:
        deps.logger.error("❌ Failed to read Discord audio attachment: %s", exc)
        return None

    if not audio_bytes:
        deps.logger.warning("Discord audio attachment was empty")
        return None

    suffix = _resolve_audio_suffix(content_type=content_type, filename=filename)
    return transcribe_audio_bytes(
        audio_bytes=audio_bytes,
        suffix=suffix,
        deps=deps,
    )


async def transcribe_telegram_voice(
    voice_file_id: str,
    context: Any,  # "ContextTypes.DEFAULT_TYPE"
    deps: AudioTranscriptionDeps,
) -> str | None:
    """Download Telegram voice file and return transcribed text."""
    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp_file:
            temp_path = tmp_file.name
        new_file = await context.bot.get_file(voice_file_id)
        await new_file.download_to_drive(temp_path)
        with open(temp_path, "rb") as handle:
            return transcribe_audio_bytes(
                audio_bytes=handle.read(),
                suffix=".ogg",
                deps=deps,
            )
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                deps.logger.warning("Failed to clean temp audio file: %s", temp_path)
