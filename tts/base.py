from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class TTSProvider(ABC):
    @abstractmethod
    def generate(self, text: str, output_path: str | Path) -> str:
        """Generate speech for text and return the absolute output path."""
        raise NotImplementedError
