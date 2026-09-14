from __future__ import annotations

import json
from pathlib import Path

from video.ffmpeg_utils import probe

from .ass_writer import write_ass
from .known_text_aligner import KnownTextCTCAligner
from .pagination import PaginationSettings, SubtitlePaginator


class SubtitlePipeline:
    def __init__(self, config_path: str | Path = "config.json") -> None:
        self.config_path = Path(config_path).resolve()
        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.ffprobe_path = str(config.get("ffprobe_path", ""))
        settings = PaginationSettings(
            preferred_min_chars=int(config.get("subtitle_preferred_min_chars", 6)),
            preferred_max_chars=int(config.get("subtitle_preferred_max_chars", 12)),
            absolute_max_chars=int(config.get("subtitle_absolute_max_chars", 18)),
            pause_threshold=float(config.get("subtitle_pause_threshold", 0.30)),
            min_duration=float(config.get("subtitle_min_duration", 0.70)),
            max_duration=float(config.get("subtitle_max_duration", 2.50)),
            end_buffer=float(config.get("subtitle_end_buffer", 0.08)),
        )
        self.aligner = KnownTextCTCAligner(self.config_path)
        self.paginator = SubtitlePaginator(settings)

    def create(
        self,
        known_text: str,
        audio_path: str | Path,
        video_path: str | Path,
        output_ass: str | Path,
    ) -> Path:
        tokens = self.aligner.align(audio_path, known_text)
        cues = self.paginator.paginate(known_text, tokens)
        media = probe(video_path, self.ffprobe_path)
        video_stream = next(stream for stream in media["streams"] if stream["codec_type"] == "video")
        return write_ass(cues, output_ass, int(video_stream["width"]), int(video_stream["height"]))
