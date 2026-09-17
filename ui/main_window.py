from __future__ import annotations

import os
import json
import subprocess
import threading
import logging
import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image

from publish.assistant import PublishAssistant, compose_caption
from video.reliability import require_space, validate_wav, voice_cache_key
from publish.browser_uploader import BrowserUploadService, UploadControl, UploadCancelled
from tts.gpt_sovits import GPTSoVITSProvider
from subtitles.pipeline import SubtitlePipeline
from ui.duration_estimator import count_speakable_characters, estimate_duration_range
from video.ffmpeg_utils import duration_seconds, probe, resolve_executable, run_checked
from video.output_naming import build_video_filename
from video.video_maker import VideoMaker


@dataclass(frozen=True)
class VideoTemplate:
    path: Path
    name: str
    duration: float


class MainWindow(ctk.CTk):
    def __init__(self, project_root: str | Path) -> None:
        super().__init__()
        self.project_root = Path(project_root).resolve()
        self.output_dir = self.project_root / "output"
        self.temp_dir = self.project_root / "voice" / "temp"
        self.video_assets_dir = self.project_root / "assets" / "videos"
        self.ui_state_file = self.temp_dir / "ui_state.json"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.video_assets_dir.mkdir(parents=True, exist_ok=True)

        self.tts = GPTSoVITSProvider(self.project_root / "config.json")
        self.video_maker = VideoMaker(self.project_root / "config.json")
        self.subtitle_pipeline = SubtitlePipeline(self.project_root / "config.json")
        self.publisher = PublishAssistant(self.video_maker.ffmpeg_path)
        self.browser_uploader = BrowserUploadService(self.project_root / "browser_profiles")
        self.upload_control: UploadControl | None = None
        self.pending_upload = None
        self.force_voice = False
        self.tts_process: subprocess.Popen[bytes] | None = None
        self.service_lock = threading.Lock()
        self.busy = False
        self.template_buttons: list[ctk.CTkButton] = []
        self.template_button_map: dict[Path, ctk.CTkButton] = {}
        self.template_images: list[ctk.CTkImage] = []
        self.selected_template: VideoTemplate | None = None
        self.speech_chars_per_second = self._load_historical_speech_rate()

        self.selected_publish_video = None
        self.latest_result = None
        from ui.layout import build
        build(self)

    def _open_generate_publish(self):
        if self.busy:
            return
        dialog = getattr(self, "publish_dialog", None)
        if dialog is not None and dialog.winfo_exists():
            dialog.lift()
            return
        from ui.publish_dialog import PublishDialog
        self.publish_dialog = PublishDialog(self)

    def _switch_page(self, page):
        self.navigation.set(page)
        self.make_page.grid_remove()
        self.publish_page.grid_remove()
        (self.make_page if page == "制作视频" else self.publish_page).grid()
        if page == "制作视频":
            self.make_actions.grid()
        else:
            self.make_actions.grid_remove()

    def _toggle_options(self):
        if self.options_frame.winfo_manager():
            self.options_frame.grid_remove()
        else:
            self.options_frame.grid()

    def _toggle_templates(self):
        if self.template_tools.winfo_manager():
            self.template_tools.grid_remove()
        else:
            self.template_tools.grid()

    def _sync_context_actions(self):
        for button, column in ((self.resume_upload_button, 1), (self.cancel_upload_button, 2)):
            if button.cget("state") == "normal":
                button.grid(row=1, column=column, padx=(0, 20), pady=(0, 10))
            else:
                button.grid_remove()

    def _set_publish_video(self, path):
        path = Path(path).resolve()
        metadata = probe(path, self.video_maker.ffprobe_path)
        if not any(item.get("codec_type") == "video" for item in metadata.get("streams", [])):
            raise ValueError("请选择包含画面的成片视频。")
        self.selected_publish_video = path
        self.publish_video_var.set(f"{path.name}\n{float(metadata['format']['duration']):.2f} 秒 · 视频已就绪")

    def _choose_publish_video(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(title="选择成片", initialdir=str(self.output_dir),
            filetypes=[("视频", "*.mp4 *.mov *.mkv *.webm")])
        if path:
            try:
                self._set_publish_video(path)
            except Exception as exc:
                messagebox.showerror("视频不可用", str(exc))

    def _upload_selected(self):
        if self.busy:
            return
        try:
            fields = self._publish_fields()
            video = self.selected_publish_video
            if video is None or not video.is_file():
                raise ValueError("请先选择需要上传的成片。")
        except Exception as exc:
            messagebox.showinfo("还需要一步", str(exc))
            return
        self._run_worker(lambda: self._prepare_publish(video, "", fields), error_title="上传失败")

    def _show_result(self, output):
        self.latest_result = Path(output)
        self.result_var.set(f"生成完成\n{self.latest_result.name}")
        self.result_frame.grid()
        self._set_publish_video(output)

    def _open_result(self):
        if self.latest_result and self.latest_result.is_file():
            os.startfile(self.latest_result)

    def _publish_result(self):
        if self.latest_result and self.latest_result.is_file():
            self._set_publish_video(self.latest_result)
            self._switch_page("发布视频")

    def _load_historical_speech_rate(self) -> float:
        fallback = 4.5
        try:
            reference_text = (self.project_root / "参考文本.txt").read_text(encoding="utf-8-sig")
            character_count = count_speakable_characters(reference_text)
            reference_duration = duration_seconds(
                self.project_root / "音频.wav", self.video_maker.ffprobe_path
            )
            if character_count > 0 and reference_duration > 0:
                return character_count / reference_duration
        except Exception:
            pass
        return fallback

    def _discover_templates(self) -> list[VideoTemplate]:
        extensions = {".mp4", ".mov", ".mkv", ".webm"}
        paths = sorted(
            path for path in self.video_assets_dir.iterdir()
            if path.is_file() and path.suffix.lower() in extensions
        )
        if len(paths) > 5:
            self.after(0, lambda: messagebox.showinfo("模板数量提示", "模板超过5个，目前按文件名显示前5个有效模板。"))
        if not paths:
            fallback = self.project_root / "基础视频.mp4"
            if fallback.is_file():
                paths = [fallback]
        templates: list[VideoTemplate] = []
        for index, path in enumerate(paths, start=1):
            try:
                duration = duration_seconds(path, self.video_maker.ffprobe_path)
                if not any(s.get("codec_type") == "video" for s in probe(path, self.video_maker.ffprobe_path).get("streams", [])):
                    raise ValueError("文件没有视频画面")
            except Exception as exc:
                self.after(0, lambda name=path.name: messagebox.showwarning("跳过无效模板", f"无法读取模板：{name}，请检查文件是否损坏。"))
                continue
            templates.append(VideoTemplate(path.resolve(), path.stem or f"模板{index}", duration))
            if len(templates) == 5:
                break
        return templates

    def _load_saved_template_path(self) -> Path | None:
        try:
            data = json.loads(self.ui_state_file.read_text(encoding="utf-8"))
            value = str(data.get("selected_template", "")).strip()
            return Path(value).resolve() if value else None
        except (OSError, ValueError, TypeError):
            return None

    def _save_template_path(self, path: Path) -> None:
        try:
            self.ui_state_file.parent.mkdir(parents=True, exist_ok=True)
            self.ui_state_file.write_text(
                json.dumps({"selected_template": str(path.resolve())}, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _thumbnail_image(self, template: VideoTemplate) -> ctk.CTkImage | None:
        thumbnail_dir = self.temp_dir / "template_thumbnails"
        thumbnail_dir.mkdir(parents=True, exist_ok=True)
        safe_name = f"{template.path.stem}_{template.path.stat().st_mtime_ns}.jpg"
        thumbnail = thumbnail_dir / safe_name
        try:
            if not thumbnail.is_file():
                ffmpeg = resolve_executable("ffmpeg", self.video_maker.ffmpeg_path)
                run_checked(
                    [
                        str(ffmpeg), "-y", "-ss", "0.2", "-i", str(template.path),
                        "-frames:v", "1", "-vf",
                        "scale=132:160:force_original_aspect_ratio=decrease,"
                        "pad=132:160:(ow-iw)/2:(oh-ih)/2:color=black",
                        str(thumbnail),
                    ]
                )
            image = Image.open(thumbnail).convert("RGB")
            return ctk.CTkImage(light_image=image, dark_image=image, size=(106, 128))
        except Exception:
            return None

    def _build_template_picker(self) -> None:
        templates = self._discover_templates()
        if not templates:
            ctk.CTkLabel(
                self.template_frame,
                text="暂无视频模板，请展开“管理模板”选择本地视频。",
            ).pack(padx=18, pady=24)
            return
        for column, template in enumerate(templates):
            image = self._thumbnail_image(template)
            kwargs = {"image": image, "compound": "top"} if image is not None else {}
            button = ctk.CTkButton(
                self.template_frame,
                text=template.name,
                width=148,
                height=158 if image is not None else 55,
                fg_color="#FFFFFF", hover_color="#F0F5FF", text_color="#1D1D1F",
                border_color="#E5E5EA", border_width=1, corner_radius=12,
                command=lambda item=template: self._select_template(item),
                **kwargs,
            )
            button.grid(row=column // 3, column=column % 3, padx=6, pady=6, sticky="nsew")
            self.template_frame.grid_columnconfigure(column % 3, weight=1)
            self.template_buttons.append(button)
            self.template_button_map[template.path] = button
            if image is not None:
                self.template_images.append(image)
        saved_path = self._load_saved_template_path()
        initial = next(
            (template for template in templates if template.path == saved_path),
            templates[0],
        )
        self._select_template(initial, persist=False)

    def _refresh_templates(self) -> None:
        if self.busy:
            return
        for child in self.template_frame.winfo_children():
            child.destroy()
        for column in range(5):
            self.template_frame.grid_columnconfigure(column, weight=0)
        self.template_buttons.clear()
        self.template_button_map.clear()
        self.template_images.clear()
        self.selected_template = None
        self.video_var.set("")
        self.template_info_var.set("当前选择：暂无可用模板")
        self._build_template_picker()

    def _select_template(self, template: VideoTemplate, persist: bool = True) -> None:
        self.selected_template = template
        self.video_var.set(str(template.path))
        for path, button in self.template_button_map.items():
            selected = path == template.path
            button.configure(border_width=2 if selected else 1,
                border_color="#007AFF" if selected else "#E5E5EA",
                text=("✓  " if selected else "") + path.stem)
        if persist:
            self._save_template_path(template.path)
        if hasattr(self, "template_info_var"):
            self.template_info_var.set(
                f"当前选择：{template.name}    视频长度：{template.duration:.2f}秒"
            )

    def _update_duration_estimate(self, _event=None) -> None:
        text = self.textbox.get("1.0", "end-1c").strip()
        lower, upper = estimate_duration_range(text, self.speech_chars_per_second)
        if upper == 0:
            message = "预计配音时长：请输入文案"
        else:
            message = f"预计配音时长：约 {lower}～{upper} 秒"
            if upper > 15:
                message += "（预计可能超过15秒）"
        self.estimate_var.set(message)

    def _set_media_status(
        self,
        audio_duration: float,
        video_duration: float,
        template_name: str,
    ) -> None:
        state = "需要循环" if audio_duration > video_duration else "视频长度足够"
        message = (
            f"{template_name} · 配音 {audio_duration:.2f}秒 · 模板 {video_duration:.2f}秒 · {state}"
        )
        self.after(0, lambda: self.media_status_var.set(message))

    def _confirm_video_loop(self, audio_duration: float, video_duration: float) -> bool:
        completed = threading.Event()
        answer = {"continue": False}

        def show_dialog() -> None:
            dialog = ctk.CTkToplevel(self)
            dialog.title("视频模板长度不足")
            dialog.geometry("520x245")
            dialog.resizable(False, False)
            dialog.transient(self)
            dialog.grab_set()

            message = (
                f"当前配音时长为 {audio_duration:.2f} 秒，已超过当前视频模板 "
                f"{video_duration:.2f} 秒。\n\n继续生成将使用视频循环功能。"
            )
            ctk.CTkLabel(dialog, text=message, justify="left", wraplength=450).pack(
                padx=30, pady=(32, 24), fill="x"
            )
            buttons = ctk.CTkFrame(dialog, fg_color="transparent")
            buttons.pack(pady=5)

            def close(value: bool) -> None:
                answer["continue"] = value
                dialog.grab_release()
                dialog.destroy()
                completed.set()

            ctk.CTkButton(
                buttons, text="继续生成", width=150, command=lambda: close(True)
            ).pack(side="left", padx=10)
            ctk.CTkButton(
                buttons, text="返回修改文案", width=150, command=lambda: close(False)
            ).pack(side="left", padx=10)
            dialog.protocol("WM_DELETE_WINDOW", lambda: close(False))
            dialog.lift()
            dialog.focus_force()

        self.after(0, show_dialog)
        completed.wait()
        return answer["continue"]

    def _select_video(self) -> None:
        path = filedialog.askopenfilename(
            title="选择猫咪视频",
            filetypes=[("视频文件", "*.mp4 *.mov *.mkv *.webm"), ("所有文件", "*.*")],
        )
        if path:
            try:
                duration = duration_seconds(path, self.video_maker.ffprobe_path)
                self.video_var.set(path)
                self.selected_template = VideoTemplate(Path(path).resolve(), "自选视频", duration)
                for template_path, button in self.template_button_map.items():
                    button.configure(border_width=1, border_color="#E5E5EA", text=template_path.stem)
                self._save_template_path(Path(path))
                self.template_info_var.set(
                    f"当前选择：自选视频    视频长度：{duration:.2f}秒"
                )
            except Exception as exc:
                messagebox.showerror("视频不可用", str(exc))

    def _text(self) -> str:
        text = self.textbox.get("1.0", "end-1c").strip()
        if not text:
            raise ValueError("文案不能为空。")
        return text

    def _video(self) -> Path:
        path = Path(self.video_var.get().strip()).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"视频不存在：{path}")
        return path

    def _ensure_tts_service(self) -> None:
        with self.service_lock:
            if self.tts.is_service_ready():
                return
            self._set_status("正在启动胖猫音色模型，首次加载约需 30 秒…")
            self.tts_process = self.tts.start_service(wait_seconds=300)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.after(0, lambda: self.preview_button.configure(state=state))
        self.after(0, lambda: self.generate_button.configure(state=state))
        self.after(0, lambda: self.publish_button.configure(state=state))
        self.after(0, lambda: self.generate_publish_button.configure(state=state))
        self.after(0, lambda: self.regenerate_button.configure(state=state))
        self.after(0, lambda: self.publish_existing_button.configure(state=state))
        self.after(0, lambda: self.select_button.configure(state=state))
        self.after(0, lambda: self.refresh_templates_button.configure(state=state))
        self.after(0, lambda: self.subtitle_checkbox.configure(state=state))
        self.after(0, lambda: self.douyin_checkbox.configure(state=state))
        self.after(0, lambda: self.xiaohongshu_checkbox.configure(state=state))
        self.after(0, lambda: self.publish_title_entry.configure(state=state))
        self.after(0, lambda: self.publish_tags_entry.configure(state=state))
        for button in self.template_buttons:
            self.after(0, lambda item=button: item.configure(state=state))

    def _set_status(self, message: str) -> None:
        self.after(0, lambda: self.status_var.set(f"状态：{message}"))

    def _copy_to_clipboard(self, value: str) -> None:
        completed = threading.Event()

        def copy() -> None:
            self.clipboard_clear()
            self.clipboard_append(value)
            self.update_idletasks()
            completed.set()

        self.after(0, copy)
        completed.wait(timeout=5)

    def _publish_fields(self) -> tuple[str, str, list[str]]:
        title = self.publish_title_entry.get().strip()
        tags = self.publish_tags_entry.get().strip()
        platforms: list[str] = []
        if self.douyin_var.get():
            platforms.append("douyin")
        if self.xiaohongshu_var.get():
            platforms.append("xiaohongshu")
        if not title:
            raise ValueError("请先填写发布标题。")
        if not platforms:
            raise ValueError("请至少选择一个发布平台。")
        return title, tags, platforms

    def _prepare_publish(
        self,
        video: str | Path,
        stamp: str,
        publish_fields: tuple[str, str, list[str]],
    ) -> None:
        title, tags, platforms = publish_fields
        metadata = probe(video, self.video_maker.ffprobe_path)
        if not any(stream.get("codec_type") == "video" for stream in metadata.get("streams", [])):
            raise ValueError("请选择包含视频画面的成片文件。")
        self._set_status("正在准备视频，即将自动上传和填写…")
        self._copy_to_clipboard(compose_caption(title, tags))
        self.pending_upload = (Path(video), (title, tags, list(platforms)))
        control = UploadControl()
        self.upload_control = control
        self.after(0, lambda: self.cancel_upload_button.configure(state="normal"))
        self.after(0, self._sync_context_actions)
        try:
            results = self.browser_uploader.submit(
                Path(video).resolve(), title, tags, platforms,
                self._set_status, self._upload_handoff, control,
            ).result()
            detail = "\n\n".join(
                f"{'抖音' if item.platform == 'douyin' else '小红书'}：{item.message}"
                for item in results
            )
            ready = all(item.ready for item in results)
            failed = [item.platform for item in results if not item.ready]
            self.pending_upload = (Path(video), (title, tags, failed)) if failed else None
            self._set_status("上传和填写完成，等待你在网页确认发布" if ready else "部分步骤需要处理，请查看发布窗口")
            self.after(0, lambda: messagebox.showinfo("请检查发布页面", detail))
        except UploadCancelled as exc:
            self._set_status(str(exc))
        finally:
            self.upload_control = None
            self.after(0, lambda: self.cancel_upload_button.configure(state="disabled"))
            self.after(0, lambda: self.resume_upload_button.configure(
                text="重试失败平台" if self.pending_upload else "继续上传",
                state="normal" if self.pending_upload else "disabled"))
            self.after(0, self._sync_context_actions)

    def _upload_handoff(self, message: str | None) -> None:
        def update() -> None:
            self.resume_upload_button.configure(text="继续上传", state="normal" if message else "disabled")
            self._sync_context_actions()
            if message:
                self.status_var.set(f"状态：{message}")
        self.after(0, update)

    def _resume_upload(self) -> None:
        if self.upload_control is not None:
            self.upload_control.resume.set()
            self.resume_upload_button.configure(state="disabled")
            self._sync_context_actions()
        elif self.pending_upload is not None and not self.busy:
            video, fields = self.pending_upload
            self._run_worker(lambda: self._prepare_publish(video, "", fields), error_title="上传失败")

    def _cancel_upload(self) -> None:
        if self.upload_control is not None:
            self.upload_control.cancelled.set()
            self.cancel_upload_button.configure(state="disabled")
            self._set_status("正在停止自动操作，已上传内容保留在网页中…")

    def _publish_existing_video(self) -> None:
        if self.busy:
            return
        try:
            publish_fields = self._publish_fields()
        except Exception as exc:
            messagebox.showerror("无法准备发布", str(exc))
            return
        path = filedialog.askopenfilename(
            title="选择已经制作好的视频",
            initialdir=str(self.output_dir),
            filetypes=[
                ("视频文件", "*.mp4 *.mov *.mkv *.webm"),
                ("所有文件", "*.*"),
            ],
        )
        if not path:
            return

        def action() -> None:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._prepare_publish(path, stamp, publish_fields)

        self._run_worker(action, error_title="上传失败")

    def _run_worker(self, action, error_title: str = "生成失败") -> None:
        if self.busy:
            return
        self._set_busy(True)

        def worker() -> None:
            try:
                action()
            except Exception as exc:
                logging.exception("任务失败：%s", error_title)
                self._set_status(error_title)
                error_message = str(exc)[-700:] + "\n\n已成功生成的配音会保留；相同文案再次生成时自动复用。如提示语音内容不匹配，请先点“下次重新配音”。详细记录见 logs/app.log。"
                self.after(0, lambda: messagebox.showerror(error_title, error_message))
            finally:
                self._set_busy(False)

        threading.Thread(target=worker, daemon=True).start()

    def _preview(self) -> None:
        try:
            text = self._text()
        except Exception as exc:
            messagebox.showerror("无法试听", str(exc))
            return

        def action() -> None:
            self._ensure_tts_service()
            self._set_status("正在生成试听配音…")
            output = self.output_dir / "胖猫试听.wav"
            self.tts.generate(text, output)
            actual_duration = duration_seconds(output, self.video_maker.ffprobe_path)
            self._set_status(f"试听配音生成完成，实际时长 {actual_duration:.2f} 秒")
            os.startfile(output)

        self._run_worker(action)

    def _generate_video(self, publish_after: bool = False) -> None:
        try:
            text = self._text()
            video = self._video()
            template_name = (
                self.selected_template.name if self.selected_template is not None else video.stem
            )
            subtitles_enabled = bool(self.subtitle_var.get())
            output_title = self.publish_title_entry.get().strip()
            publish_fields = self._publish_fields() if publish_after else None
        except Exception as exc:
            messagebox.showerror("无法生成", str(exc))
            return

        def action() -> None:
            require_space(self.output_dir)
            if subtitles_enabled:
                self._set_status("正在检查文案读法与字幕兼容性…")
                self.subtitle_pipeline.aligner.preflight(text)
            generated_at = datetime.now()
            stamp = generated_at.strftime("%Y%m%d_%H%M%S")
            key = voice_cache_key(text, self.tts.config, self.project_root)
            audio = self.temp_dir / f"cached_voice_{key}.wav"
            output = self.output_dir / build_video_filename(
                text,
                output_title,
                generated_at,
            )
            reusable = False
            if audio.is_file() and not self.force_voice:
                try:
                    validate_wav(audio)
                    reusable = True
                except Exception:
                    pass
            if reusable:
                self._set_status("复用已完成配音，继续字幕和合成…")
            else:
                self._ensure_tts_service()
                self._set_status("正在生成胖猫配音…")
                self.tts.generate(text, audio)
                self.force_voice = False
            audio_duration = duration_seconds(audio, self.video_maker.ffprobe_path)
            video_duration = duration_seconds(video, self.video_maker.ffprobe_path)
            self._set_media_status(audio_duration, video_duration, template_name)
            if audio_duration > video_duration:
                self._set_status("配音超过模板长度，等待确认是否循环…")
                if not self._confirm_video_loop(audio_duration, video_duration):
                    self._set_status("已返回修改文案，本次未生成视频")
                    return
            subtitle = None
            if subtitles_enabled:
                subtitle_key = hashlib.sha256(str((key, audio.stat().st_mtime_ns,
                    str(video), video.stat().st_mtime_ns, video.stat().st_size)).encode()).hexdigest()[:24]
                subtitle = self.temp_dir / f"cached_subtitle_{subtitle_key}.ass"
                if subtitle.is_file():
                    self._set_status("复用已完成字幕，继续合成…")
                else:
                    self._set_status("正在按真实语音时间对齐字幕…")
                    partial_ass = subtitle.with_suffix(".partial.ass")
                    self.subtitle_pipeline.create(text, audio, video, partial_ass)
                    partial_ass.replace(subtitle)
            self._set_status("正在裁剪并合成视频…")
            result = self.video_maker.make(video, audio, output, subtitle_path=subtitle)
            detail = (
                f"基础视频 {result.video_duration:.2f}s，配音 {result.audio_duration:.2f}s，"
                f"循环 {result.loops} 次，成片 {result.output_duration:.2f}s"
            )
            self._set_status(f"生成完成｜本次生成文件：{output.name}｜{detail}")
            self.after(0, lambda: self._show_result(output))
            if publish_fields is not None:
                self._prepare_publish(output, stamp, publish_fields)
                return
            self.after(
                0,
                lambda: messagebox.showinfo("生成完成", f"{detail}\n\n视频已保存到：\n{output}"),
            )

        self._run_worker(action)

    def _force_next_voice(self) -> None:
        if not self.busy:
            self.force_voice = True
            self._set_status("下次生成将重新配音，不复用旧配音。")

    def _open_output(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(self.output_dir)

    def _on_close(self) -> None:
        if self.busy and self.upload_control is None:
            messagebox.showinfo("任务正在进行", "请等待当前生成完成后关闭，配音和成片不会丢失。")
            return
        if self.upload_control is not None and not messagebox.askyesno(
            "停止上传并退出？", "正在上传或等待登录。退出会停止自动操作并关闭专用发布窗口，确定退出？"
        ):
            return
        self.browser_uploader.close()
        self._set_status("正在关闭发布窗口并保存登录状态…")
        self._finish_close()

    def _finish_close(self) -> None:
        thread = self.browser_uploader._thread
        if thread is not None and thread.is_alive():
            self.after(100, self._finish_close)
            return
        process = self.tts_process
        if process is not None and process.poll() is None:
            process.terminate()
        self.destroy()
