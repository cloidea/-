import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch, Mock

from subtitles.align_worker import _alignment_units, spoken_text
from video.reliability import require_space, validate_wav, voice_cache_key
from video.video_maker import VideoMaker
from video.ffmpeg_utils import run_checked, resolve_executable, probe

ROOT = Path(__file__).resolve().parents[1]


class ReliabilityTests(unittest.TestCase):
    def test_numbers_have_shared_reading(self):
        text = "2026-09-17增长12.5%，剩20米"
        speech = spoken_text(text)
        self.assertIn("二零二六年九月十七日", speech)
        self.assertIn("百分之十二点五", speech)
        self.assertEqual("".join(b for a, b in _alignment_units(text)),
                         "".join(b for a, b in _alignment_units(speech)))

    def test_invalid_date_is_preflight_error(self):
        with self.assertRaises(ValueError):
            spoken_text("2026-02-30")

    def test_silent_audio_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "silent.wav"
            with wave.open(str(path), "wb") as audio:
                audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                audio.writeframes(b"\0" * 32000)
            with self.assertRaisesRegex(ValueError, "无声"):
                validate_wav(path)

    def test_disk_full_is_reported_before_work(self):
        with tempfile.TemporaryDirectory() as directory, patch("video.reliability.shutil.disk_usage") as usage:
            usage.return_value.free = 1
            with self.assertRaisesRegex(RuntimeError, "磁盘空间不足"):
                require_space(directory)

    def test_reference_change_invalidates_cache(self):
        config = {"reference_audio": "a", "reference_text_file": "b"}
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "a").write_bytes(b"audio")
            Path(directory, "b").write_text("first")
            first = voice_cache_key("文案", config, directory)
            Path(directory, "b").write_text("second")
            self.assertNotEqual(first, voice_cache_key("文案", config, directory))

    def test_real_ffmpeg_short_and_looped_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            ffmpeg = str(resolve_executable("ffmpeg"))
            maker = VideoMaker(ROOT / "config.json")
            for duration in (1.3, 6.3):
                audio = folder / f"audio_{duration}.wav"
                output = folder / f"video_{duration}.mp4"
                run_checked([ffmpeg, "-y", "-i", str(ROOT / "音频.wav"), "-t", str(duration), str(audio)])
                result = maker.make(ROOT / "基础视频.mp4", audio, output)
                self.assertAlmostEqual(result.output_duration, duration, delta=0.15)
                self.assertEqual(result.loops, 1 if duration < 5 else 2)
                streams = probe(output)["streams"]
                self.assertEqual(next(s for s in streams if s["codec_type"] == "video")["codec_name"], "h264")
                self.assertEqual(next(s for s in streams if s["codec_type"] == "audio")["codec_name"], "aac")
                self.assertFalse(list(folder.glob("*.partial.mp4")))

    def test_failed_ffmpeg_keeps_existing_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.mp4"
            with patch("video.video_maker.run_checked", side_effect=RuntimeError("encoder failed")):
                with self.assertRaises(RuntimeError):
                    VideoMaker(ROOT / "config.json").make(ROOT / "基础视频.mp4", ROOT / "音频.wav", output)
            self.assertFalse(output.exists())

    def test_retry_reuses_voice_and_then_completed_subtitles(self):
        from ui.main_window import MainWindow
        import json
        with tempfile.TemporaryDirectory() as directory:
            window = Mock()
            window.project_root = ROOT
            window.output_dir = Path(directory)
            window.temp_dir = Path(directory)
            window.force_voice = False
            window._text.return_value = "已知测试文案"
            window._video.return_value = ROOT / "基础视频.mp4"
            window.selected_template = None
            window.subtitle_var.get.return_value = True
            window.publish_title_entry.get.return_value = "恢复测试"
            window.tts.config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
            window.tts.generate.side_effect = lambda text, target: target.write_bytes((ROOT / "音频.wav").read_bytes())
            window.video_maker.ffprobe_path = ""
            window._confirm_video_loop.return_value = True
            window._run_worker.side_effect = lambda action: action()
            window.subtitle_pipeline.create.side_effect = RuntimeError("alignment failed")
            with self.assertRaisesRegex(RuntimeError, "alignment failed"):
                MainWindow._generate_video(window)
            window.subtitle_pipeline.create.side_effect = lambda text, audio, video, target: target.write_text("completed subtitle")
            window.video_maker.make.side_effect = RuntimeError("encoding failed")
            with self.assertRaisesRegex(RuntimeError, "encoding failed"):
                MainWindow._generate_video(window)
            from video.video_maker import VideoResult
            window.video_maker.make.side_effect = None
            window.video_maker.make.return_value = VideoResult("result.mp4", 5.616, 9.845, 9.85, 2)
            MainWindow._generate_video(window)
            self.assertEqual(window.tts.generate.call_count, 1)
            self.assertEqual(window.subtitle_pipeline.create.call_count, 2)
            self.assertEqual(window.video_maker.make.call_count, 2)

    def test_upload_uses_original_and_retains_only_failed_platform(self):
        from ui.main_window import MainWindow
        from publish.browser_uploader import UploadResult
        window = Mock()
        window.video_maker.ffprobe_path = ""
        window.browser_uploader.submit.return_value.result.return_value = [
            UploadResult("douyin", True, "done"),
            UploadResult("xiaohongshu", False, "retry"),
        ]
        video = ROOT / "基础视频.mp4"
        MainWindow._prepare_publish(window, video, "unused", ("标题", "#猫咪", ["douyin", "xiaohongshu"]))
        window.publisher.prepare.assert_not_called()
        self.assertEqual(window.browser_uploader.submit.call_args.args[0], video.resolve())
        self.assertEqual(window.pending_upload[1][2], ["xiaohongshu"])


if __name__ == "__main__":
    unittest.main()
