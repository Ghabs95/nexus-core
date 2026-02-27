"""Unit tests for WhisperTranscriptionProvider and AdapterRegistry.create_transcription."""

import asyncio
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_openai_mock() -> ModuleType:
    """Create a minimal openai mock module."""
    openai_mod = ModuleType("openai")
    openai_mod.AsyncOpenAI = MagicMock()
    openai_mod.OpenAIError = Exception
    return openai_mod


@pytest.fixture(autouse=True)
def openai_mock(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Install a mocked openai module into sys.modules for the duration of each test."""
    openai_mod = _make_openai_mock()
    monkeypatch.setitem(sys.modules, "openai", openai_mod)
    return openai_mod


# ---------------------------------------------------------------------------
# WhisperTranscriptionProvider
# ---------------------------------------------------------------------------


class TestWhisperTranscriptionProvider:
    def _make_provider(self, api_key="sk-test"):
        from nexus.adapters.transcription.whisper_provider import WhisperTranscriptionProvider

        return WhisperTranscriptionProvider(api_key=api_key)

    def test_name(self):
        provider = self._make_provider()
        assert provider.name == "whisper"

    def test_ogg_format_accepted(self, tmp_path):
        """OGG files must pass the format check without raising ValueError."""
        ogg_file = tmp_path / "voice.ogg"
        ogg_file.write_bytes(b"\x00" * 16)

        provider = self._make_provider()

        mock_response = MagicMock()
        mock_response.text = "hello world"
        mock_response.language = "en"
        mock_response.duration = 1.5
        mock_response.segments = []

        mock_client = MagicMock()
        mock_client.audio.transcriptions.create = AsyncMock(return_value=mock_response)
        sys.modules["openai"].AsyncOpenAI.return_value = mock_client

        from nexus.adapters.transcription.base import TranscriptionInput

        inp = TranscriptionInput(source=ogg_file)
        result = asyncio.run(provider.transcribe(inp))

        assert result.text == "hello world"
        assert result.provider_used == "whisper"

    def test_unsupported_format_raises(self, tmp_path):
        """An unsupported format (e.g. .xyz) must raise ValueError before API call."""
        bad_file = tmp_path / "audio.xyz"
        bad_file.write_bytes(b"\x00" * 16)

        provider = self._make_provider()

        from nexus.adapters.transcription.base import TranscriptionInput

        inp = TranscriptionInput(source=bad_file)

        with pytest.raises(ValueError, match="Unsupported audio format"):
            asyncio.run(provider.transcribe(inp))

    def test_check_availability_true(self):
        """check_availability returns True when the API responds successfully."""
        provider = self._make_provider()

        mock_client = MagicMock()
        mock_client.models.retrieve = AsyncMock(return_value=MagicMock())
        sys.modules["openai"].AsyncOpenAI.return_value = mock_client

        result = asyncio.run(provider.check_availability())
        assert result is True

    def test_check_availability_false_on_error(self):
        """check_availability returns False when an exception is raised."""
        provider = self._make_provider()

        mock_client = MagicMock()
        mock_client.models.retrieve = AsyncMock(side_effect=Exception("network error"))
        sys.modules["openai"].AsyncOpenAI.return_value = mock_client

        result = asyncio.run(provider.check_availability())
        assert result is False


# ---------------------------------------------------------------------------
# AdapterRegistry.create_transcription
# ---------------------------------------------------------------------------


class TestAdapterRegistryTranscription:
    def test_create_whisper_provider(self):
        from nexus.adapters.registry import AdapterRegistry

        registry = AdapterRegistry()
        provider = registry.create_transcription("whisper", api_key="sk-test")

        from nexus.adapters.transcription.whisper_provider import WhisperTranscriptionProvider

        assert isinstance(provider, WhisperTranscriptionProvider)

    def test_unknown_transcription_type_raises(self):
        from nexus.adapters.registry import AdapterRegistry

        registry = AdapterRegistry()
        with pytest.raises(ValueError, match="Unknown transcription adapter type"):
            registry.create_transcription("nonexistent_provider")

    def test_custom_transcription_registration(self):
        from nexus.adapters.registry import AdapterRegistry
        from nexus.adapters.transcription.base import (
            TranscriptionInput,
            TranscriptionProvider,
            TranscriptionResult,
        )

        class DummyProvider(TranscriptionProvider):
            @property
            def name(self):
                return "dummy"

            async def check_availability(self):
                return True

            async def transcribe(self, audio_input: TranscriptionInput) -> TranscriptionResult:
                return TranscriptionResult(text="dummy", provider_used="dummy")

        registry = AdapterRegistry()
        registry.register_transcription("dummy", DummyProvider)
        provider = registry.create_transcription("dummy")
        assert isinstance(provider, DummyProvider)


# ---------------------------------------------------------------------------
# Public export check
# ---------------------------------------------------------------------------


def test_whisper_exported_from_package():
    """WhisperTranscriptionProvider must be importable from nexus.adapters.transcription."""
    from nexus.adapters.transcription import WhisperTranscriptionProvider  # noqa: F401

    assert WhisperTranscriptionProvider is not None
