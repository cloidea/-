from __future__ import annotations

import math
import re


_SPEAKABLE_PATTERN = re.compile(r"[\u3400-\u9fffA-Za-z0-9]")
_PAUSE_PATTERN = re.compile(r"[，,。！？!?；;：:]")


def count_speakable_characters(text: str) -> int:
    """Count characters that normally consume speaking time."""
    return len(_SPEAKABLE_PATTERN.findall(text))


def estimate_duration_range(
    text: str,
    chars_per_second: float = 4.5,
) -> tuple[int, int]:
    """Return a deliberately approximate whole-second duration range."""
    if chars_per_second <= 0:
        raise ValueError("chars_per_second 必须大于 0。")
    characters = count_speakable_characters(text)
    if characters == 0:
        return 0, 0
    pause_seconds = len(_PAUSE_PATTERN.findall(text)) * 0.18
    estimate = characters / chars_per_second + pause_seconds
    lower = max(1, math.floor(estimate * 0.88))
    upper = max(lower + 1, math.ceil(estimate * 1.12))
    return lower, upper
