"""Local desktop smoke tests; no model inference or platform upload."""
import unittest
from pathlib import Path
from unittest.mock import patch

from ui.main_window import MainWindow


class LayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.window = MainWindow(Path(__file__).resolve().parents[1])
        cls.window.title("界面自动测试")
        cls.window.update()

    @classmethod
    def tearDownClass(cls):
        cls.window.destroy()

    def test_page_switch_keeps_text(self):
        w = self.window
        original = w.textbox.get("1.0", "end-1c")
        w.publish_title_entry.insert(0, "测试标题")
        w._switch_page("发布视频")
        w.update()
        self.assertTrue(w.publish_page.winfo_ismapped())
        self.assertFalse(w.make_actions.winfo_ismapped())
        w._switch_page("制作视频")
        w.update()
        self.assertEqual(w.textbox.get("1.0", "end-1c"), original)
        self.assertEqual(w.publish_title_entry.get(), "测试标题")

    def test_options_are_collapsible(self):
        w = self.window
        self.assertFalse(w.options_frame.winfo_manager())
        w._toggle_options()
        self.assertEqual(w.options_frame.winfo_manager(), "grid")
        w._toggle_options()
        self.assertFalse(w.options_frame.winfo_manager())
        w._toggle_templates()
        self.assertEqual(w.template_tools.winfo_manager(), "grid")
        w._toggle_templates()

    def test_quick_publish_validates_and_starts_combined_workflow(self):
        w = self.window
        old_title = w.publish_title_entry.get()
        old_tags = w.publish_tags_entry.get()
        old_platforms = (w.douyin_var.get(), w.xiaohongshu_var.get())
        with patch.object(w, "_generate_video") as generate:
            w._open_generate_publish()
            w.update()
            dialog = w.publish_dialog
            dialog.title_entry.delete(0, "end")
            dialog.confirm()
            generate.assert_not_called()
            self.assertIn("标题", dialog.error.get())
            dialog.title_entry.insert(0, "一键上传测试")
            dialog.douyin.set(False)
            dialog.xiaohongshu.set(False)
            dialog.confirm()
            generate.assert_not_called()
            self.assertIn("平台", dialog.error.get())
            dialog.douyin.set(True)
            dialog.xiaohongshu.set(True)
            dialog.tags_entry.delete(0, "end")
            dialog.tags_entry.insert(0, "#猫咪 #搞笑")
            dialog.confirm()
            generate.assert_called_once_with(publish_after=True)
            self.assertEqual(w._publish_fields(), ("一键上传测试", "#猫咪 #搞笑", ["douyin", "xiaohongshu"]))
        for entry, value in ((w.publish_title_entry, old_title), (w.publish_tags_entry, old_tags)):
            entry.delete(0, "end")
            entry.insert(0, value)
        w.douyin_var.set(old_platforms[0])
        w.xiaohongshu_var.set(old_platforms[1])

    def test_quick_publish_cancel_does_not_generate(self):
        w = self.window
        original = w.publish_title_entry.get()
        with patch.object(w, "_generate_video") as generate:
            w._open_generate_publish()
            w.update()
            dialog = w.publish_dialog
            dialog.title_entry.insert(0, "不保存")
            dialog.close()
            generate.assert_not_called()
        self.assertEqual(w.publish_title_entry.get(), original)

    def test_busy_disables_actions(self):
        w = self.window
        w._set_busy(True)
        w.update()
        for button in (w.generate_button, w.publish_button, w.publish_existing_button,
                       w.generate_publish_button, w.regenerate_button):
            self.assertEqual(button.cget("state"), "disabled")
        w._set_busy(False)
        w.update()
        self.assertEqual(w.generate_button.cget("state"), "normal")

    def test_result_can_be_handed_to_publish(self):
        w = self.window
        path = w.selected_template.path
        w._show_result(path)
        w._publish_result()
        w.update()
        self.assertEqual(w.selected_publish_video, path.resolve())
        self.assertEqual(w.navigation.get(), "发布视频")
        self.assertIn(path.name, w.publish_video_var.get())
        with patch.object(w, "_run_worker") as worker:
            w.publish_title_entry.delete(0, "end")
            w.publish_title_entry.insert(0, "测试标题")
            w._upload_selected()
            worker.assert_called_once()
        w.result_frame.grid_remove()

    def test_small_window_keeps_primary_action_visible(self):
        w = self.window
        w._switch_page("制作视频")
        w.geometry("820x700")
        w.update()
        button = w.generate_button
        self.assertTrue(button.winfo_ismapped())
        self.assertLessEqual(button.winfo_rooty() + button.winfo_height(),
                             w.winfo_rooty() + w.winfo_height())
        self.assertLessEqual(button.winfo_rootx() + button.winfo_width(),
                             w.winfo_rootx() + w.winfo_width())
        w._upload_handoff("请登录")
        w.update()
        self.assertTrue(w.resume_upload_button.winfo_ismapped())
        w._upload_handoff(None)
        w.update()
        self.assertFalse(w.resume_upload_button.winfo_ismapped())


if __name__ == "__main__":
    unittest.main()
