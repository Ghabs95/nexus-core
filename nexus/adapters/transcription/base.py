"""Base interfaces for transcription adapters."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class TranscriptionSegment:
    """A single segment of transcribed audio."""

    start: float
    end: float
    text: str


@dataclass
class TranscriptionInput:
    """Input specification for a transcription request."""

    source: Any  # Path, bytes, or str URL
    language: Optional[str] = None
    format: str = "auto"


@dataclass
class TranscriptionResult:
    """Transcription output, including full text and optional segments."""

    text: str
    provider_used: str
    language: Optional[str] = None
    duration_seconds: Optional[float] = None
    segments: list[TranscriptionSegment] = field(default_factory=list)


class TranscriptionProvider(ABC):
    """Abstract provider interface for audio transcription."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name (e.g., 'whisper')."""

    @abstractmethod
    async def check_availability(self) -> bool:
        """Return True if the provider is currently reachable."""

    @abstractmethod
    async def transcribe(self, audio_input: TranscriptionInput) -> TranscriptionResult:
        """Transcribe the given audio input into text."""
