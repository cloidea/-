from __future__ import annotations

import unittest
import math
from datetime import datetime
from pathlib import Path

from publish.assistant import compose_caption, normalize_hashtags
from tts.gpt_sovits import GPTSoVITSProvider
from subtitles.base import TimedToken
from subtitles.align_worker import _alignment_units, _integer_to_chinese
from subtitles.pagination import SubtitlePaginator
from ui.duration_estimator import count_speakable_characters, estimate_duration_range
from video.ffmpeg_utils import duration_seconds, resolve_executable
from video.output_naming import build_video_filename


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

    def test_duration_estimate_is_advisory_and_marks_long_copy(self) -> None:
        self.assertEqual(count_speakable_characters("睡了吗？"), 3)
        short_range = estimate_duration_range("睡了吗？还没睡啊。", 4.5)
        long_range = estimate_duration_range("猫咪今天决定认真工作。" * 12, 4.5)
        self.assertGreaterEqual(short_range[0], 1)
        self.assertGreater(long_range[1], 15)

    def test_arabic_numbers_are_normalized_for_chinese_alignment(self) -> None:
        self.assertEqual(_integer_to_chinese("20"), "二十")
        self.assertEqual(_integer_to_chinese("16"), "十六")
        self.assertEqual(
            _alignment_units("前方 20 米掉头。"),
            [("前", "前"), ("方", "方"), ("20", "二十"), ("米", "米"), ("掉", "掉"), ("头", "头")],
        )

    def test_publish_caption_normalizes_and_deduplicates_hashtags(self) -> None:
        self.assertEqual(
            normalize_hashtags("猫咪，#搞笑 猫咪"),
            "#猫咪 #搞笑",
        )
        self.assertEqual(
            compose_caption("猫咪冷知识", "猫咪 #搞笑"),
            "猫咪冷知识\n#猫咪 #搞笑",
        )

    def test_generated_video_filename_is_readable_and_windows_safe(self) -> None:
        generated_at = datetime(2026, 9, 17, 19, 54, 30)
        self.assertEqual(
            build_video_filename("问你个冷知识：前方20米掉头。", "猫咪冷知识", generated_at),
            "胖猫成片_20260917_195430_猫咪冷知识.mp4",
        )
        self.assertEqual(
            build_video_filename("  不能/使用:*的标题？  ", generated_at=generated_at),
            "胖猫成片_20260917_195430_不能使用的标题.mp4",
        )

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
