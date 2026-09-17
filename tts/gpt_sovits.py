from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .base import TTSProvider
from video.reliability import require_space, validate_wav
from subtitles.align_worker import spoken_text


class GPTSoVITSError(RuntimeError):
    pass


class GPTSoVITSProvider(TTSProvider):
    """Client for the official GPT-SoVITS api_v2.py local HTTP service."""

    def __init__(self, config_path: str | Path = "config.json") -> None:
        self.config_path = Path(config_path).resolve()
        with self.config_path.open("r", encoding="utf-8") as handle:
            self.config: dict[str, Any] = json.load(handle)
        self.project_root = self.config_path.parent
        self.host = str(self.config.get("tts_host", "127.0.0.1"))
        self.port = int(self.config.get("tts_port", 9880))
        self.timeout = float(self.config.get("tts_timeout_seconds", 300))

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def _project_path(self, value: str) -> Path:
        path = Path(value)
        return path.resolve() if path.is_absolute() else (self.project_root / path).resolve()

    def _read_reference_text(self) -> str:
        path = self._project_path(str(self.config["reference_text_file"]))
        if not path.is_file():
            raise GPTSoVITSError(f"参考文本不存在：{path}")
        text = path.read_text(encoding="utf-8-sig").strip()
        if not text:
            raise GPTSoVITSError(f"参考文本为空：{path}")
        return text

    def _reference_audio(self) -> Path:
        path = self._project_path(str(self.config["reference_audio"]))
        if not path.is_file():
            raise GPTSoVITSError(f"参考音频不存在：{path}")
        return path

    def is_service_ready(self) -> bool:
        try:
            with urlopen(f"{self.base_url}/docs", timeout=2) as response:
                return 200 <= response.status < 500
        except (OSError, URLError):
            return False

    def start_service(self, wait_seconds: float = 120) -> subprocess.Popen[bytes]:
        root_value = str(self.config.get("gpt_sovits_root", "")).strip()
        if not root_value:
            raise GPTSoVITSError("尚未配置 GPT-SoVITS 整合包目录。")
        root = self._project_path(root_value)
        python_exe = root / "runtime" / "python.exe"
        api_script = root / str(self.config.get("gpt_sovits_api_script", "api_v2.py"))
        tts_config = root / str(
            self.config.get("gpt_sovits_tts_config", "GPT_SoVITS/configs/tts_infer.yaml")
        )
        for path, label in ((python_exe, "整合包 Python"), (api_script, "API 脚本"), (tts_config, "推理配置")):
            if not path.is_file():
                raise GPTSoVITSError(f"未找到{label}：{path}")

        log_path = self.project_root / "logs" / "gpt_sovits_api.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("ab") as log_handle:
            process = subprocess.Popen(
                [
                    str(python_exe),
                    str(api_script),
                    "-a",
                    self.host,
                    "-p",
                    str(self.port),
                    "-c",
                    str(tts_config),
                ],
                cwd=root,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            if process.poll() is not None:
                try:
                    detail = log_path.read_text(encoding="utf-8", errors="replace")[-3000:]
                except OSError:
                    detail = ""
                raise GPTSoVITSError(
                    f"GPT-SoVITS 服务启动失败，退出码：{process.returncode}\n{detail}"
                )
            if self.is_service_ready():
                return process
            time.sleep(1)
        process.terminate()
        raise GPTSoVITSError(f"等待 GPT-SoVITS 服务启动超时。日志：{log_path}")

    def generate(self, text: str, output_path: str | Path) -> str:
        target_text = spoken_text(text.strip())
        if not target_text:
            raise GPTSoVITSError("待生成文案不能为空。")
        if not self.is_service_ready():
            raise GPTSoVITSError("GPT-SoVITS 本地服务未启动。")

        output = Path(output_path).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        require_space(output.parent)
        payload = {
            "text": target_text,
            "text_lang": str(self.config.get("target_language", "zh")),
            "ref_audio_path": str(self._reference_audio()),
            "prompt_text": self._read_reference_text(),
            "prompt_lang": str(self.config.get("reference_language", "zh")),
            "text_split_method": "cut5",
            "batch_size": 1,
            "media_type": "wav",
            "streaming_mode": False,
            "speed_factor": float(self.config.get("tts_speed_factor", 1.0)),
        }
        request = Request(
            f"{self.base_url}/tts",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                audio = response.read()
                content_type = response.headers.get("Content-Type", "")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise GPTSoVITSError(f"GPT-SoVITS 生成失败（HTTP {exc.code}）：{detail}") from exc
        except (OSError, URLError) as exc:
            raise GPTSoVITSError(f"无法连接 GPT-SoVITS：{exc}") from exc

        if "audio" not in content_type.lower() and not audio.startswith(b"RIFF"):
            detail = audio[:500].decode("utf-8", errors="replace")
            raise GPTSoVITSError(f"GPT-SoVITS 未返回 WAV 音频：{detail}")
        temporary = output.with_suffix(".partial.wav")
        try:
            temporary.write_bytes(audio)
            validate_wav(temporary)
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)
        return str(output)
