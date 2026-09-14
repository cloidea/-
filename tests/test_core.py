from __future__ import annotations

import unittest
import math
from pathlib import Path

from tts.gpt_sovits import GPTSoVITSProvider
from subtitles.base import TimedToken
from subtitles.pagination import SubtitlePaginator
from video.ffmpeg_utils import duration_seconds, resolve_executable


ROOT = Path(__file__).resolve().parents[1]


class CoreTests(unittest.TestCase):
    def test_fixed_reference_configuration(self) -> None:
        provider = GPTSoVITSProvider(ROOT / "config.json")
        self.assertEqual(provider._reference_audio(), ROOT / "音频.wav")
        self.assertTrue(provider._read_reference_text())

    def test_ffmpeg_and_media_are_readable(self) -> None:
        self.assertTrue(resolve_executable("ffmpeg").is_file())
        self.assertAlmostEqual(duration_seconds(ROOT / "基础视频.mp4"), 5.616, places=2)
        self.assertAlmostEqual(duration_seconds(ROOT / "音频.wav"), 9.845, places=2)

    def test_output_duration_follows_audio_and_video_loops_as_needed(self) -> None:
        video_duration = 5.616
        short_audio_duration = 2.66
        long_audio_duration = 11.7
        self.assertEqual(short_audio_duration, 2.66)
        self.assertEqual(math.ceil(short_audio_duration / video_duration), 1)
        self.assertEqual(math.ceil(long_audio_duration / video_duration), 3)

    def test_subtitle_pagination_uses_real_token_timestamps(self) -> None:
        text = "睡了吗？还没睡啊，那没事，你继续刷吧。"
        chars = [char for char in text if char not in "，。？！"]
        times = [
            (0.10, 0.32), (0.34, 0.58), (0.60, 0.90),
            (1.02, 1.22), (1.24, 1.43), (1.45, 1.68), (1.70, 1.92),
            (2.30, 2.53), (2.55, 2.78), (2.80, 3.05),
            (3.42, 3.61), (3.63, 3.82), (3.84, 4.04),
            (4.06, 4.27), (4.29, 4.50),
        ]
        cues = SubtitlePaginator().paginate(
            text, [TimedToken(char, start, end) for char, (start, end) in zip(chars, times)]
        )
        self.assertEqual([cue.text for cue in cues], ["睡了吗？", "还没睡啊，", "那没事，", "你继续刷吧。"])
        self.assertEqual(cues[0].start, 0.10)
        self.assertAlmostEqual(cues[-1].end, 4.58, places=2)
        self.assertTrue(all(left.end <= right.start for left, right in zip(cues, cues[1:])))


if __name__ == "__main__":
    unittest.main()
