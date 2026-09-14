from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


class AlignmentError(RuntimeError):
    pass


@dataclass(frozen=True)
class TimedToken:
    text: str
    start: float
    end: float
    confidence: float = 1.0


@dataclass(frozen=True)
class SubtitleCue:
    text: str
    start: float
    end: float


class AlignmentProvider(ABC):
    @abstractmethod
    def align(self, audio_path: str | Path, known_text: str) -> list[TimedToken]:
        """Return real acoustic timestamps for the known transcript."""
