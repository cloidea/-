"""Local validation and reusable generation checkpoints."""
import hashlib
import json
import shutil
import wave
import array
from pathlib import Path


def require_space(directory, required=256 * 1024 * 1024):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(directory).free < required:
        raise RuntimeError(f"磁盘空间不足，需要至少 {required / 1024**2:.0f} MB 可用空间。请清理后重试。")


def validate_wav(path):
    with wave.open(str(path), "rb") as audio:
        frames = audio.getnframes()
        rate = audio.getframerate()
        channels = audio.getnchannels()
        width = audio.getsampwidth()
        data = audio.readframes(frames)
    if not rate or frames / rate < 0.15 or len(data) != frames * channels * width:
        raise ValueError("配音为空、过短或文件不完整，请重新生成配音。")
    if width == 2:
        samples = array.array("h", data)
        if max(map(abs, samples), default=0) < 32:
            raise ValueError("配音几乎无声，请重新生成配音。")
    return frames / rate


def voice_cache_key(text, config, root):
    references = []
    for key in ("reference_audio", "reference_text_file"):
        path = Path(config[key])
        if not path.is_absolute():
            path = Path(root) / path
        references.append(hashlib.sha256(path.read_bytes()).hexdigest())
    payload = ["speech-v2", text, config, references]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]
