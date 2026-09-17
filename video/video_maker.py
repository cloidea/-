from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass
from pathlib import Path

from .ffmpeg_utils import duration_seconds, resolve_executable, run_checked
from .reliability import require_space, validate_wav


def _filter_path(path: Path) -> str:
    value = path.resolve().as_posix()
    return value.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


@dataclass(frozen=True)
class VideoResult:
    output_path: str
    video_duration: float
    audio_duration: float
    output_duration: float
    loops: int


class VideoMaker:
    def __init__(self, config_path: str | Path = "config.json") -> None:
        config_file = Path(config_path).resolve()
        with config_file.open("r", encoding="utf-8") as handle:
            config = json.load(handle)
        self.ffmpeg_path = str(config.get("ffmpeg_path", ""))
        self.ffprobe_path = str(config.get("ffprobe_path", ""))

    def make(
        self,
        video_path: str | Path,
        audio_path: str | Path,
        output_path: str | Path,
        subtitle_path: str | Path | None = None,
    ) -> VideoResult:
        video = Path(video_path).resolve()
        audio = Path(audio_path).resolve()
        output = Path(output_path).resolve()
        if not video.is_file():
            raise FileNotFoundError(f"视频不存在：{video}")
        if not audio.is_file():
            raise FileNotFoundError(f"配音不存在：{audio}")

        video_duration = duration_seconds(video, self.ffprobe_path)
        audio_duration = duration_seconds(audio, self.ffprobe_path)
        # 配音决定成片时长；基础视频不足时循环，足够时直接裁剪。
        output_duration = audio_duration
        loops = max(1, math.ceil(output_duration / video_duration))
        output.parent.mkdir(parents=True, exist_ok=True)
        validate_wav(audio)
        require_space(output.parent, max(256 * 1024**2, int(video.stat().st_size * loops * 2)))
        if output.exists():
            raise FileExistsError("输出文件已存在，已保留原文件，请换一个输出名称。")
        temporary = output.with_name(output.stem + "." + uuid.uuid4().hex[:8] + ".partial.mp4")
        ffmpeg = resolve_executable("ffmpeg", self.ffmpeg_path)
        command = [
                str(ffmpeg),
                "-y",
                "-stream_loop",
                str(loops - 1),
                "-i",
                str(video),
                "-i",
                str(audio),
        ]
        filters = ["pad=ceil(iw/2)*2:ceil(ih/2)*2"]
        if subtitle_path is not None:
            subtitle = Path(subtitle_path).resolve()
            if not subtitle.is_file():
                raise FileNotFoundError(f"字幕文件不存在：{subtitle}")
            filters.append(f"ass=filename='{_filter_path(subtitle)}'")
        command.extend(["-vf", ",".join(filters)])
        command.extend(
            [
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-t",
                f"{output_duration:.6f}",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                "-map_metadata",
                "0",
                "-map_chapters",
                "-1",
                str(temporary),
            ]
        )
        try:
            run_checked(command)
            actual = duration_seconds(temporary, self.ffprobe_path)
            if abs(actual - audio_duration) > 0.15:
                raise RuntimeError("成片时长与配音不一致，已保留配音，请重试合成。")
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)
        return VideoResult(str(output), video_duration, audio_duration, actual, loops)
