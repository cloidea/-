from __future__ import annotations

import os
import json
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image

from publish.assistant import PublishAssistant
from tts.gpt_sovits import GPTSoVITSProvider
from subtitles.pipeline import SubtitlePipeline
from ui.duration_estimator import count_speakable_characters, estimate_duration_range
from video.ffmpeg_utils import duration_seconds, resolve_executable, run_checked
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
        self.tts_process: subprocess.Popen[bytes] | None = None
        self.service_lock = threading.Lock()
        self.busy = False
        self.template_buttons: list[ctk.CTkButton] = []
        self.template_button_map: dict[Path, ctk.CTkButton] = {}
        self.template_images: list[ctk.CTkImage] = []
        self.selected_template: VideoTemplate | None = None
        self.speech_chars_per_second = self._load_historical_speech_rate()

        self.title("猫咪沙雕短视频生成器")
        self.geometry("980x900")
        self.minsize(820, 780)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        ctk.set_appearance_mode("system")

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(3, weight=1)

        title = ctk.CTkLabel(self, text="猫咪沙雕短视频生成器", font=ctk.CTkFont(size=25, weight="bold"))
        title.grid(row=0, column=0, columnspan=3, padx=28, pady=(24, 22))

        ctk.CTkLabel(self, text="视频模板：").grid(row=1, column=0, padx=(28, 10), pady=8, sticky="nw")
        self.template_frame = ctk.CTkFrame(self)
        self.template_frame.grid(row=1, column=1, columnspan=2, padx=(0, 28), pady=8, sticky="ew")
        self.video_var = ctk.StringVar(value="")
        self._build_template_picker()

        self.template_info_var = ctk.StringVar(value="当前选择：暂无可用模板")
        self.template_info_label = ctk.CTkLabel(self, textvariable=self.template_info_var, anchor="w")
        self.template_info_label.grid(row=2, column=1, padx=0, pady=(2, 8), sticky="w")
        if self.selected_template is not None:
            self.template_info_var.set(
                f"当前选择：{self.selected_template.name}    "
                f"视频长度：{self.selected_template.duration:.2f}秒"
            )
        self.select_button = ctk.CTkButton(self, text="选择其他本地视频", width=150, command=self._select_video)
        self.select_button.grid(row=2, column=2, padx=(10, 28), pady=(2, 8), sticky="e")

        ctk.CTkLabel(self, text="文案：").grid(row=3, column=0, padx=(28, 10), pady=8, sticky="nw")
        self.textbox = ctk.CTkTextbox(self, height=180)
        self.textbox.grid(row=3, column=1, columnspan=2, padx=(0, 28), pady=8, sticky="nsew")
        text_file = self.project_root / "文案.txt"
        if text_file.is_file():
            self.textbox.insert("1.0", text_file.read_text(encoding="utf-8-sig").strip())
        self.textbox.bind("<KeyRelease>", self._update_duration_estimate)
        self.textbox.bind("<<Paste>>", lambda _event: self.after(10, self._update_duration_estimate))

        self.estimate_var = ctk.StringVar(value="预计配音时长：请输入文案")
        self.estimate_label = ctk.CTkLabel(self, textvariable=self.estimate_var, anchor="w")
        self.estimate_label.grid(row=4, column=1, columnspan=2, padx=0, pady=(0, 5), sticky="w")
        self._update_duration_estimate()

        ctk.CTkLabel(self, text="发布标题：").grid(row=5, column=0, padx=(28, 10), pady=6, sticky="w")
        self.publish_title_entry = ctk.CTkEntry(self, placeholder_text="例如：猫咪冷知识，最后一句绷不住了")
        self.publish_title_entry.grid(row=5, column=1, columnspan=2, padx=(0, 28), pady=6, sticky="ew")

        ctk.CTkLabel(self, text="发布标签：").grid(row=6, column=0, padx=(28, 10), pady=6, sticky="w")
        self.publish_tags_entry = ctk.CTkEntry(self, placeholder_text="#猫咪 #搞笑 #沙雕配音")
        self.publish_tags_entry.grid(row=6, column=1, columnspan=2, padx=(0, 28), pady=6, sticky="ew")

        ctk.CTkLabel(self, text="发布平台：").grid(row=7, column=0, padx=(28, 10), pady=6, sticky="w")
        platform_frame = ctk.CTkFrame(self, fg_color="transparent")
        platform_frame.grid(row=7, column=1, columnspan=2, padx=0, pady=6, sticky="w")
        self.douyin_var = ctk.BooleanVar(value=True)
        self.xiaohongshu_var = ctk.BooleanVar(value=False)
        self.douyin_checkbox = ctk.CTkCheckBox(platform_frame, text="抖音", variable=self.douyin_var)
        self.douyin_checkbox.pack(side="left", padx=(0, 24))
        self.xiaohongshu_checkbox = ctk.CTkCheckBox(
            platform_frame, text="小红书", variable=self.xiaohongshu_var
        )
        self.xiaohongshu_checkbox.pack(side="left")

        ctk.CTkLabel(self, text="音色：").grid(row=8, column=0, padx=(28, 10), pady=8, sticky="w")
        ctk.CTkLabel(self, text="胖猫（固定）", anchor="w").grid(row=8, column=1, columnspan=2, padx=0, pady=8, sticky="w")

        self.subtitle_var = ctk.BooleanVar(value=True)
        self.subtitle_checkbox = ctk.CTkCheckBox(
            self, text="自动添加同步字幕", variable=self.subtitle_var
        )
        self.subtitle_checkbox.grid(row=9, column=1, columnspan=2, padx=0, pady=(8, 2), sticky="w")

        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.grid(row=10, column=0, columnspan=3, pady=(14, 12))
        self.preview_button = ctk.CTkButton(button_frame, text="试听配音", width=150, command=self._preview)
        self.preview_button.pack(side="left", padx=10)
        self.generate_button = ctk.CTkButton(button_frame, text="生成视频", width=150, command=self._generate_video)
        self.generate_button.pack(side="left", padx=10)
        self.publish_button = ctk.CTkButton(
            button_frame,
            text="生成并准备发布",
            width=170,
            command=lambda: self._generate_video(publish_after=True),
        )
        self.publish_button.pack(side="left", padx=10)

        self.media_status_var = ctk.StringVar(value="实际配音时长：--\n模板视频：--\n状态：等待生成")
        self.media_status_label = ctk.CTkLabel(self, textvariable=self.media_status_var, anchor="w", justify="left")
        self.media_status_label.grid(row=11, column=0, columnspan=3, padx=28, pady=(4, 4), sticky="ew")

        self.status_var = ctk.StringVar(value="状态：等待生成")
        self.status_label = ctk.CTkLabel(self, textvariable=self.status_var, anchor="w")
        self.status_label.grid(row=12, column=0, columnspan=3, padx=28, pady=(4, 10), sticky="ew")

        self.open_button = ctk.CTkButton(self, text="打开输出文件夹", command=self._open_output)
        self.open_button.grid(row=13, column=0, columnspan=3, pady=(0, 24))

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
        )[:5]
        if not paths:
            fallback = self.project_root / "基础视频.mp4"
            if fallback.is_file():
                paths = [fallback]
        templates: list[VideoTemplate] = []
        for index, path in enumerate(paths, start=1):
            try:
                duration = duration_seconds(path, self.video_maker.ffprobe_path)
            except Exception:
                continue
            templates.append(VideoTemplate(path.resolve(), path.stem or f"模板{index}", duration))
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
            return ctk.CTkImage(light_image=image, dark_image=image, size=(132, 160))
        except Exception:
            return None

    def _build_template_picker(self) -> None:
        templates = self._discover_templates()
        if not templates:
            ctk.CTkLabel(
                self.template_frame,
                text="assets/videos 中暂无视频，请点击下方按钮选择本地视频。",
            ).pack(padx=18, pady=24)
            return
        for column, template in enumerate(templates):
            image = self._thumbnail_image(template)
            kwargs = {"image": image, "compound": "top"} if image is not None else {}
            button = ctk.CTkButton(
                self.template_frame,
                text=template.name,
                width=148,
                height=190 if image is not None else 55,
                command=lambda item=template: self._select_template(item),
                **kwargs,
            )
            button.grid(row=0, column=column, padx=7, pady=9, sticky="nsew")
            self.template_frame.grid_columnconfigure(column, weight=1)
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

    def _select_template(self, template: VideoTemplate, persist: bool = True) -> None:
        self.selected_template = template
        self.video_var.set(str(template.path))
        for path, button in self.template_button_map.items():
            button.configure(border_width=3 if path == template.path else 0)
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
            f"当前选择：{template_name}\n"
            f"实际配音时长：{audio_duration:.2f}秒\n"
            f"模板视频：{video_duration:.2f}秒\n"
            f"状态：{state}"
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
            self.video_var.set(path)
            try:
                duration = duration_seconds(path, self.video_maker.ffprobe_path)
                self.selected_template = VideoTemplate(Path(path).resolve(), "自选视频", duration)
                for button in self.template_button_map.values():
                    button.configure(border_width=0)
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
        self.after(0, lambda: self.select_button.configure(state=state))
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

    def _run_worker(self, action) -> None:
        if self.busy:
            return
        self._set_busy(True)

        def worker() -> None:
            try:
                action()
            except Exception as exc:
                self._set_status("生成失败")
                error_message = str(exc)
                self.after(0, lambda: messagebox.showerror("生成失败", error_message))
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
            publish_fields = self._publish_fields() if publish_after else None
        except Exception as exc:
            messagebox.showerror("无法生成", str(exc))
            return

        def action() -> None:
            self._ensure_tts_service()
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            audio = self.temp_dir / f"voice_{stamp}.wav"
            output = self.output_dir / f"cat_{stamp}.mp4"
            self._set_status("正在生成胖猫配音…")
            self.tts.generate(text, audio)
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
                self._set_status("正在按真实语音时间对齐字幕…")
                subtitle = self.subtitle_pipeline.create(
                    text, audio, video, self.temp_dir / f"subtitle_{stamp}.ass"
                )
            self._set_status("正在裁剪并合成视频…")
            result = self.video_maker.make(video, audio, output, subtitle_path=subtitle)
            detail = (
                f"基础视频 {result.video_duration:.2f}s，配音 {result.audio_duration:.2f}s，"
                f"循环 {result.loops} 次，成片 {result.output_duration:.2f}s"
            )
            self._set_status(f"生成完成：{detail}")
            if publish_fields is not None:
                title, tags, platforms = publish_fields
                self._set_status("正在准备发布文件和打开上传页面…")
                package = self.publisher.prepare(
                    output,
                    self.output_dir,
                    stamp,
                    title,
                    tags,
                    platforms,
                )
                self._copy_to_clipboard(package.caption)
                self.publisher.reveal_video(package.video_path)
                self.publisher.open_upload_pages(platforms)
                self._set_status("发布页面已打开，标题和标签已复制")
                platform_names = "、".join(
                    "抖音" if item == "douyin" else "小红书" for item in platforms
                )
                self.after(
                    0,
                    lambda: messagebox.showinfo(
                        "发布助手已准备完成",
                        f"已打开：{platform_names}\n"
                        "已在资源管理器中选中视频，并复制标题和标签。\n\n"
                        "请把视频拖入上传页面，粘贴文案，检查后点击发布。",
                    ),
                )
                return
            self.after(
                0,
                lambda: messagebox.showinfo("生成完成", f"{detail}\n\n视频已保存到：\n{output}"),
            )

        self._run_worker(action)

    def _open_output(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(self.output_dir)

    def _on_close(self) -> None:
        process = self.tts_process
        if process is not None and process.poll() is None:
            process.terminate()
        self.destroy()
