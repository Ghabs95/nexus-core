from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from nexus.core.handlers.audio_transcription_handler import (
    AudioTranscriptionDeps,
    is_audio_attachment,
    transcribe_audio_bytes,
    transcribe_discord_attachment,
)


def _deps(transcribe_audio):
    return AudioTranscriptionDeps(logger=Mock(), transcribe_audio=transcribe_audio)


def test_is_audio_attachment_detects_mime_and_filename():
    assert is_audio_attachment(content_type="audio/ogg", filename=None) is True
    assert is_audio_attachment(content_type="application/octet-stream", filename="voice.OGG") is True
    assert is_audio_attachment(content_type=None, filename="recording.mp3") is True
    assert is_audio_attachment(content_type="image/png", filename="image.png") is False


def test_transcribe_audio_bytes_uses_suffix_and_cleans_temp_file():
    captured_path: str | None = None

    def _transcribe(path: str) -> str | None:
        nonlocal captured_path
        captured_path = path
        assert path.endswith(".mp3")
        assert os.path.exists(path)
        return " hello world "

    text = transcribe_audio_bytes(
        audio_bytes=b"abc",
        suffix=".mp3",
        deps=_deps(_transcribe),
    )

    assert text == "hello world"
    assert captured_path is not None
    assert not os.path.exists(captured_path)


@pytest.mark.asyncio
async def test_transcribe_discord_attachment_skips_non_audio():
    attachment = SimpleNamespace(
        content_type="image/png",
        filename="image.png",
        read=lambda: b"",
    )

    result = await transcribe_discord_attachment(attachment, _deps(lambda _path: "unused"))

    assert result is None


@pytest.mark.asyncio
async def test_transcribe_discord_attachment_supports_filename_when_mime_missing():
    class _Attachment:
        content_type = None
        filename = "voice_message.mp3"

        async def read(self) -> bytes:
            return b"mp3-audio"

    captured_path: str | None = None

    def _transcribe(path: str) -> str | None:
        nonlocal captured_path
        captured_path = path
        return " transcribed text "

    result = await transcribe_discord_attachment(_Attachment(), _deps(_transcribe))

    assert result == "transcribed text"
    assert captured_path is not None
    assert captured_path.endswith(".mp3")
    assert not os.path.exists(captured_path)


@pytest.mark.asyncio
async def test_transcribe_discord_attachment_handles_read_error():
    class _Attachment:
        content_type = "audio/ogg"
        filename = "voice.ogg"

        async def read(self) -> bytes:
            raise RuntimeError("boom")

    result = await transcribe_discord_attachment(_Attachment(), _deps(lambda _path: "unused"))

    assert result is None
