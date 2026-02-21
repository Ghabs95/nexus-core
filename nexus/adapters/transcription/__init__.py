"""Transcription adapter interfaces and implementations."""

from nexus.adapters.transcription.base import (
    TranscriptionInput,
    TranscriptionProvider,
    TranscriptionResult,
    TranscriptionSegment,
)
from nexus.adapters.transcription.whisper_provider import WhisperTranscriptionProvider

__all__ = [
    "TranscriptionInput",
    "TranscriptionProvider",
    "TranscriptionResult",
    "TranscriptionSegment",
    "WhisperTranscriptionProvider",
]
