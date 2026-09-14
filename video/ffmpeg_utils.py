from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


class FFmpegError(RuntimeError):
    pass


def _winget_candidates(executable: str) -> list[Path]:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return []
    packages = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
    if not packages.is_dir():
        return []
    return list(packages.glob(f"Gyan.FFmpeg_*/*/bin/{executable}.exe"))


def resolve_executable(executable: str, configured_path: str = "") -> Path:
    if configured_path:
        path = Path(configured_path).expanduser().resolve()
        if path.is_file():
            return path
        raise FFmpegError(f"配置的 {executable} 不存在：{path}")
    found = shutil.which(executable)
    if found:
        return Path(found).resolve()
    candidates = _winget_candidates(executable)
    if candidates:
        return max(candidates, key=lambda item: item.stat().st_mtime).resolve()
    raise FFmpegError(f"未找到 {executable}，请先安装 FFmpeg。")


def run_checked(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise FFmpegError(f"FFmpeg 执行失败（退出码 {result.returncode}）：\n{detail}")
    return result


def probe(path: str | Path, ffprobe_path: str = "") -> dict[str, Any]:
    source = Path(path).resolve()
    if not source.is_file():
        raise FFmpegError(f"媒体文件不存在：{source}")
    executable = resolve_executable("ffprobe", ffprobe_path)
    result = run_checked(
        [
            str(executable),
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(source),
        ]
    )
    return json.loads(result.stdout)


def duration_seconds(path: str | Path, ffprobe_path: str = "") -> float:
    data = probe(path, ffprobe_path)
    try:
        duration = float(data["format"]["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise FFmpegError(f"无法读取媒体时长：{Path(path).resolve()}") from exc
    if duration <= 0:
        raise FFmpegError(f"媒体时长无效：{duration}")
    return duration
