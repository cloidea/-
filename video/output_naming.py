from __future__ import annotations

import re
from datetime import datetime


_INVALID_WINDOWS_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _filename_summary(text: str, max_length: int = 20) -> str:
    value = re.sub(r"\s+", "", text.strip())
    value = _INVALID_WINDOWS_FILENAME_CHARS.sub("", value)
    value = value.strip(" ._，。！？!?、；;：:")
    return value[:max_length] or "未命名"


def build_video_filename(
    text: str,
    publish_title: str = "",
    generated_at: datetime | None = None,
) -> str:
    """Build a readable, sortable and Windows-safe generated video name."""
    moment = generated_at or datetime.now()
    summary = _filename_summary(publish_title or text)
    return f"胖猫成片_{moment:%Y%m%d_%H%M%S}_{summary}.mp4"

