from __future__ import annotations

import json
import statistics
import subprocess
import tempfile
from pathlib import Path

from .base import AlignmentError, AlignmentProvider, TimedToken


class KnownTextCTCAligner(AlignmentProvider):
    """Runs known-transcript CTC forced alignment in the GPT-SoVITS runtime."""

    def __init__(self, config_path: str | Path = "config.json") -> None:
        self.config_path = Path(config_path).resolve()
        self.project_root = self.config_path.parent
        self.config = json.loads(self.config_path.read_text(encoding="utf-8"))

    def _path(self, value: str) -> Path:
        path = Path(value)
        return path.resolve() if path.is_absolute() else (self.project_root / path).resolve()

    def _python(self) -> Path:
        configured = str(self.config.get("alignment_python", "")).strip()
        if configured:
            return self._path(configured)
        root = self._path(str(self.config["gpt_sovits_root"]))
        return root / "runtime" / "python.exe"

    def align(self, audio_path: str | Path, known_text: str) -> list[TimedToken]:
        audio = Path(audio_path).resolve()
        if not audio.is_file():
            raise AlignmentError(f"待对齐音频不存在：{audio}")
        if not known_text.strip():
            raise AlignmentError("待对齐文案不能为空。")

        python = self._python()
        worker = self.project_root / "subtitles" / "align_worker.py"
        model = str(
            self.config.get(
                "alignment_model", "jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn"
            )
        )
        model_dir = self._path(str(self.config.get("alignment_model_dir", "models/alignment")))
        model_dir.mkdir(parents=True, exist_ok=True)
        temp_dir = self.project_root / "voice" / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)

        request_data = {
            "audio_path": str(audio),
            "text": known_text,
            "model": model,
            "model_dir": str(model_dir),
            "device": str(self.config.get("alignment_device", "cuda")),
            "local_files_only": True,
        }
        request_file: Path | None = None
        response_file: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", suffix=".json", encoding="utf-8", dir=temp_dir, delete=False
            ) as handle:
                json.dump(request_data, handle, ensure_ascii=False)
                request_file = Path(handle.name)
            response_file = request_file.with_suffix(".result.json")
            result = subprocess.run(
                [str(python), str(worker), str(request_file), str(response_file)],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode != 0:
                detail = result.stderr.strip() or result.stdout.strip()
                if "MODEL_NOT_FOUND" in detail:
                    raise AlignmentError(
                        "尚未安装中文强制对齐模型，无法生成同步字幕。"
                    )
                raise AlignmentError(f"字幕强制对齐失败：\n{detail}")
            data = json.loads(response_file.read_text(encoding="utf-8"))
            tokens = [
                TimedToken(
                    str(x["text"]),
                    float(x["start"]),
                    float(x["end"]),
                    float(x.get("confidence", 0.0)),
                )
                for x in data
            ]
            median_confidence = statistics.median(token.confidence for token in tokens)
            low_token_confidence = float(
                self.config.get("alignment_low_token_confidence", 0.001)
            )
            low_ratio = sum(
                token.confidence < low_token_confidence for token in tokens
            ) / len(tokens)
            minimum_median = float(self.config.get("alignment_min_median_confidence", 0.005))
            maximum_low_ratio = float(self.config.get("alignment_max_low_confidence_ratio", 0.30))
            if median_confidence < minimum_median or low_ratio > maximum_low_ratio:
                raise AlignmentError(
                    "最终 WAV 与输入文案的语音内容不匹配，已阻止生成错误字幕。"
                    f"（对齐置信度中位数 {median_confidence:.3f}，低置信字符占比 {low_ratio:.0%}）"
                )
            return tokens
        finally:
            for path in (request_file, response_file):
                if path is not None:
                    path.unlink(missing_ok=True)
