from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from publish.assistant import normalize_hashtags
from publish.browser_uploader import (
    BrowserUploadService, PortalUploader, UploadCancelled, UploadControl, caption_matches,
)


class BrowserUploaderTests(unittest.TestCase):
    def adapter(self, platform="douyin"):
        page = Mock()
        page.is_closed.return_value = False
        page.url = "https://creator.douyin.com/creator-micro/content/upload"
        adapter = PortalUploader(platform, page, UploadControl(), Mock(), Mock())
        adapter._pin_field = lambda field: field
        return adapter

    def test_caption_requires_hash_and_exact_token_boundaries(self):
        self.assertTrue(caption_matches("111 #1 #11", "111", "#1#11"))
        self.assertFalse(caption_matches("111", "111", "#1#11"))
        self.assertFalse(caption_matches("111 1#1 1", "111", "#1#11"))
        self.assertFalse(caption_matches("111 #11", "111", "#1#11"))
        self.assertTrue(caption_matches("111\n#1\u00a0#11", "111", "#1#11"))

    def test_field_is_pinned_before_placeholder_disappears(self):
        locator, handle = Mock(), Mock()
        locator.element_handle.return_value = handle
        self.assertIs(PortalUploader._pin_field(locator), handle)
        locator.element_handle.assert_called_once_with(timeout=5000)

    def test_topics_are_inserted_as_whole_tokens(self):
        adapter = self.adapter()
        editor = Mock()
        editor.get_attribute.return_value = "true"
        content = ["111"]
        editor.inner_text.side_effect = lambda: content[0]
        adapter.page.keyboard.insert_text.side_effect = lambda value: content.__setitem__(0, content[0] + value)
        adapter._wait = Mock(return_value=None)
        self.assertEqual(adapter._fill_topics(editor, "#1#11"), ["#1", "#11"])
        self.assertEqual(content[0], "111 #1 #11")
        self.assertEqual([c.args[0] for c in adapter.page.keyboard.insert_text.call_args_list], [" #1", " #11"])
        editor.press_sequentially.assert_not_called()

    def test_corrupt_topic_input_restores_entire_caption(self):
        adapter = self.adapter()
        editor = Mock()
        editor.get_attribute.return_value = "true"
        content = ["111"]
        editor.inner_text.side_effect = lambda: content[0]
        editor.fill.side_effect = lambda value, **_: content.__setitem__(0, value)
        adapter.page.keyboard.insert_text.side_effect = lambda _: content.__setitem__(0, "111 1#1 1")
        adapter._wait = Mock(return_value=None)
        self.assertEqual(adapter._fill_topics(editor, "#1#11"), ["#1", "#11"])
        self.assertEqual(content[0], "111 #1 #11")
        editor.fill.assert_called_once_with("111 #1 #11", timeout=5000)

    def test_consecutive_hashtags(self):
        self.assertEqual(normalize_hashtags("#猫咪#搞笑 ＃猫咪，萌宠"), "#猫咪 #搞笑 #萌宠")

    def test_cancel_prevents_file_upload(self):
        adapter = self.adapter()
        adapter.control.cancelled.set()
        with self.assertRaises(UploadCancelled):
            adapter.run(Path("test.mp4"), "测试", "")
        adapter.page.locator.assert_not_called()

    def test_foreign_host_never_gets_video(self):
        adapter = self.adapter()
        adapter.page.url = "https://example.org/login"
        self.assertIsNone(adapter._file_input())
        adapter.page.locator.assert_not_called()

    def test_ambiguous_video_input_rejected(self):
        adapter = self.adapter()
        inputs = [Mock(), Mock()]
        for item in inputs:
            item.get_attribute.return_value = "video/*"
        adapter.page.locator.return_value.all.return_value = inputs
        self.assertIsNone(adapter._file_input())

    def test_progress_is_not_success(self):
        adapter = self.adapter()
        adapter._failure = Mock(return_value=None)
        progress = Mock()
        progress.is_visible.return_value = True
        adapter.page.get_by_text.return_value.all.return_value = [progress]
        self.assertFalse(adapter._uploaded())

    def test_upload_then_fill_but_never_click_publish(self):
        adapter = self.adapter()
        native_input, editor, title_field = Mock(), Mock(), Mock()
        title_field.get_attribute.return_value = None
        title_field.input_value.return_value = "测试"
        editor.get_attribute.return_value = "true"
        editor.inner_text.side_effect = ["测试", "测试", "测试 #猫咪"]
        adapter._file_input = Mock(return_value=native_input)
        adapter._uploaded = Mock(return_value=True)
        adapter._editor = Mock(side_effect=[None, editor])
        adapter._title = Mock(return_value=title_field)
        adapter._fill_topics = Mock(return_value=[])
        result = adapter.run(Path("test.mp4"), "测试", "#猫咪")
        self.assertTrue(result.ready)
        native_input.set_input_files.assert_called_once()
        title_field.fill.assert_called_once_with("测试", timeout=5000)
        # No page-level button search or click is allowed in the ready path.
        adapter.page.get_by_role.assert_not_called()
        adapter.page.click.assert_not_called()

    def test_unmatched_topic_is_not_reported_ready(self):
        adapter = self.adapter()
        editor = Mock()
        editor.get_attribute.return_value = "true"
        editor.inner_text.side_effect = ["测试", "测试", "测试 #猫咪"]
        adapter._file_input = Mock(return_value=Mock())
        adapter._uploaded = Mock(return_value=True)
        adapter._editor = Mock(side_effect=[None, editor])
        title_field = Mock()
        title_field.get_attribute.return_value = None
        title_field.input_value.return_value = "测试"
        adapter._title = Mock(return_value=title_field)
        adapter._fill_topics = Mock(return_value=["#猫咪"])
        result = adapter.run(Path("test.mp4"), "测试", "#猫咪")
        self.assertFalse(result.ready)
        self.assertIn("仅作为文字", result.message)

    def test_login_resume_only_uploads_once(self):
        adapter = self.adapter()
        editor = Mock()
        editor.get_attribute.return_value = "true"
        editor.inner_text.return_value = "测试"
        file_input = Mock()
        title_field = Mock()
        title_field.get_attribute.return_value = None
        title_field.input_value.return_value = "测试"
        adapter._editor = Mock(return_value=None)
        adapter._wait = Mock(side_effect=[None, file_input, title_field, editor, True])
        adapter._pause = Mock()
        adapter._title = Mock(return_value=None)
        adapter._fill_topics = Mock(return_value=[])
        self.assertTrue(adapter.run(Path("test.mp4"), "测试", "").ready)
        adapter._pause.assert_called_once()
        file_input.set_input_files.assert_called_once()

    def test_one_platform_failure_does_not_skip_other_platform(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "video.mp4"
            video.touch()
            service = BrowserUploadService(Path(directory) / "profiles")
            with patch("playwright.sync_api.sync_playwright") as runtime, patch(
                "publish.browser_uploader.PortalUploader"
            ) as portal:
                runtime.return_value.start.return_value.chromium.launch_persistent_context.return_value.pages = []
                portal.return_value.run.side_effect = [RuntimeError("页面需要更新"), "second result"]
                result = service.submit(video, "标题", "", ["douyin", "xiaohongshu"],
                                        Mock(), Mock(), UploadControl()).result(timeout=5)
                self.assertFalse(result[0].ready)
                self.assertEqual(result[1], "second result")
                self.assertEqual(portal.return_value.run.call_count, 2)
                service.close()
                service.join()

    def test_multiple_completion_indicators_are_success(self):
        adapter = self.adapter()
        adapter._failure = Mock(return_value=None)
        progress, finished = Mock(), Mock()
        progress.all.return_value = []
        finished.all.return_value = [Mock(is_visible=lambda: True), Mock(is_visible=lambda: True)]
        adapter.page.get_by_text.side_effect = [progress, finished]
        self.assertTrue(adapter._uploaded())

    def test_missing_body_does_not_prevent_title_fill(self):
        adapter = self.adapter()
        title = Mock()
        title.get_attribute.return_value = None
        title.input_value.return_value = "测试"
        adapter._wait = Mock(side_effect=[title, None])
        with self.assertRaisesRegex(RuntimeError, "标题已填写"):
            adapter._finish_fields("测试", "")
        title.fill.assert_called_once_with("测试", timeout=5000)


if __name__ == "__main__":
    unittest.main()
