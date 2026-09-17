from __future__ import annotations

import json
import shutil
import subprocess
import webbrowser
from dataclasses import dataclass
from pathlib import Path

from video.ffmpeg_utils import resolve_executable, run_checked


PLATFORM_URLS = {
    "douyin": "https://creator.douyin.com/creator-micro/content/upload",
    "xiaohongshu": "https://creator.xiaohongshu.com/publish/publish",
}


def normalize_hashtags(value: str) -> str:
    parts = value.replace("，", " ").replace(",", " ").split()
    normalized: list[str] = []
    for part in parts:
        tag = part.strip().lstrip("#").strip()
        if tag:
            normalized.append(f"#{tag}")
    return " ".join(dict.fromkeys(normalized))


def compose_caption(title: str, hashtags: str) -> str:
    title = title.strip()
    tags = normalize_hashtags(hashtags)
    return "\n".join(part for part in (title, tags) if part)


@dataclass(frozen=True)
class PublishPackage:
    directory: Path
    video_path: Path
    cover_path: Path | None
    caption_path: Path
    caption: str


class PublishAssistant:
    def __init__(self, ffmpeg_path: str = "") -> None:
        self.ffmpeg_path = ffmpeg_path

    def prepare(
        self,
        source_video: str | Path,
        output_root: str | Path,
        stamp: str,
        title: str,
        hashtags: str,
        platforms: list[str],
    ) -> PublishPackage:
        source = Path(source_video).resolve()
        if not source.is_file():
            raise FileNotFoundError(f"待发布视频不存在：{source}")
        unknown = [platform for platform in platforms if platform not in PLATFORM_URLS]
        if unknown:
            raise ValueError(f"不支持的发布平台：{', '.join(unknown)}")

        package_dir = Path(output_root).resolve() / "publish" / stamp
        package_dir.mkdir(parents=True, exist_ok=True)
        # Keep the real container extension for an existing finished video.
        # Renaming a MOV/MKV file to .mp4 without transcoding would produce a
        # misleading and sometimes un-uploadable file.
        video = package_dir / f"视频{source.suffix.lower()}"
        shutil.copy2(source, video)

        caption = compose_caption(title, hashtags)
        caption_path = package_dir / "发布文案.txt"
        caption_path.write_text(caption, encoding="utf-8-sig")
        metadata = {
            "title": title.strip(),
            "hashtags": normalize_hashtags(hashtags),
            "platforms": platforms,
            "source_video": str(source),
        }
        (package_dir / "发布信息.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        cover = package_dir / "封面.jpg"
        try:
            ffmpeg = resolve_executable("ffmpeg", self.ffmpeg_path)
            run_checked(
                [
                    str(ffmpeg), "-y", "-ss", "1", "-i", str(video),
                    "-frames:v", "1", "-q:v", "2", str(cover),
                ]
            )
        except Exception:
            cover = None
        return PublishPackage(package_dir, video, cover, caption_path, caption)

    @staticmethod
    def open_upload_pages(platforms: list[str]) -> None:
        for platform in platforms:
            url = PLATFORM_URLS.get(platform)
            if url:
                webbrowser.open(url, new=2)

    @staticmethod
    def reveal_video(video_path: str | Path) -> None:
        video = Path(video_path).resolve()
        subprocess.Popen(
            ["explorer.exe", f"/select,{video}"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

