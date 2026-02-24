"""Base interface for transcription providers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TranscriptionSegment:
    """A single segment of transcribed audio."""

    start: float
    end: float
    text: str


@dataclass
class TranscriptionInput:
    """Input specification for a transcription request.

    Args:
        source: Audio source — a filesystem :class:`~pathlib.Path`, raw bytes,
            or a URL string pointing to a remote audio file.
        language: BCP-47 language hint (e.g. ``"en"``, ``"pt"``).  When
            ``None`` the provider auto-detects the language.
        format: Container/codec hint.  Use ``"auto"`` to let the provider
            detect it from the file extension or MIME type.
        metadata: Free-form key/value bag forwarded to the provider.
    """

    source: Path | bytes | str
    language: str | None = None
    format: str = "auto"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TranscriptionResult:
    """The output of a transcription request.

    Args:
        text: Full transcript text.
        language: Detected or provided language code.
        duration_seconds: Total audio duration, if reported by the provider.
        segments: Time-aligned segments (may be empty for providers that do
            not support segmentation).
        provider_used: Name of the :class:`TranscriptionProvider` that
            produced this result.
        metadata: Provider-specific extra fields (e.g. confidence scores).
    """

    text: str
    provider_used: str
    language: str | None = None
    duration_seconds: float | None = None
    segments: list[TranscriptionSegment] = field(default_factory=list)
    provider_used: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class TranscriptionProvider(ABC):
    """Abstract provider interface for audio transcription."""

    @abstractmethod
    async def transcribe(self, audio_input: TranscriptionInput) -> TranscriptionResult:
        """Transcribe audio to text.

        Args:
            audio_input: Specification of the audio to transcribe.

        Returns:
            :class:`TranscriptionResult` with the transcript and metadata.
        """

    @abstractmethod
    async def check_availability(self) -> bool:
        """Return ``True`` if this provider is reachable and ready."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable identifier for this provider (e.g. ``"whisper"``)."""
