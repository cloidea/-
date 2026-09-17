"""Explicit real-model smoke test (not part of routine unittest discovery)."""
import json
import tempfile
from pathlib import Path

from tts.gpt_sovits import GPTSoVITSProvider
from subtitles.pipeline import SubtitlePipeline
from video.video_maker import VideoMaker


def main():
    root = Path(__file__).resolve().parents[1]
    tts = GPTSoVITSProvider(root / "config.json")
    pipeline = SubtitlePipeline(root / "config.json")
    text = "苏轼说，增长12.5%，还有20米。"
    pipeline.aligner.preflight(text)
    started = None
    try:
        if not tts.is_service_ready():
            print("Starting local voice service", flush=True)
            started = tts.start_service(wait_seconds=300)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            audio = folder / "test.wav"
            print("Generating real speech", flush=True)
            tts.generate(text, audio)
            print("Aligning and rendering subtitles", flush=True)
            subtitle = pipeline.create(text, audio, root / "基础视频.mp4", folder / "test.ass")
            content = subtitle.read_text(encoding="utf-8-sig")
            assert "轼" in content and "12.5%" in content
            result = VideoMaker(root / "config.json").make(root / "基础视频.mp4", audio, folder / "test.mp4", subtitle)
            print(json.dumps(result.__dict__, ensure_ascii=True), flush=True)
    finally:
        if started is not None:
            started.terminate()
            started.wait(timeout=15)


if __name__ == "__main__":
    main()
