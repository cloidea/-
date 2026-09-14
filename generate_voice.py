from __future__ import annotations

import subprocess
from pathlib import Path

from tts.gpt_sovits import GPTSoVITSProvider


def main() -> None:
    root = Path(__file__).resolve().parent
    text = (root / "文案.txt").read_text(encoding="utf-8-sig").strip()
    provider = GPTSoVITSProvider(root / "config.json")
    output = root / "胖猫测试.wav"
    process = None
    try:
        if not provider.is_service_ready():
            process = provider.start_service(wait_seconds=300)
        print(provider.generate(text, output))
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    main()
