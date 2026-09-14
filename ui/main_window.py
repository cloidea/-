from __future__ import annotations

import os
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from tts.gpt_sovits import GPTSoVITSProvider
from subtitles.pipeline import SubtitlePipeline
from video.video_maker import VideoMaker


class MainWindow(ctk.CTk):
    def __init__(self, project_root: str | Path) -> None:
        super().__init__()
        self.project_root = Path(project_root).resolve()
        self.output_dir = self.project_root / "output"
        self.temp_dir = self.project_root / "voice" / "temp"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        self.tts = GPTSoVITSProvider(self.project_root / "config.json")
        self.video_maker = VideoMaker(self.project_root / "config.json")
        self.subtitle_pipeline = SubtitlePipeline(self.project_root / "config.json")
        self.tts_process: subprocess.Popen[bytes] | None = None
        self.service_lock = threading.Lock()
        self.busy = False

        self.title("猫咪沙雕短视频生成器")
        self.geometry("720x540")
        self.minsize(640, 500)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        ctk.set_appearance_mode("system")

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=1)

        title = ctk.CTkLabel(self, text="猫咪沙雕短视频生成器", font=ctk.CTkFont(size=25, weight="bold"))
        title.grid(row=0, column=0, columnspan=3, padx=28, pady=(24, 22))

        ctk.CTkLabel(self, text="视频：").grid(row=1, column=0, padx=(28, 10), pady=8, sticky="w")
        self.video_var = ctk.StringVar(value=str(self.project_root / "基础视频.mp4"))
        self.video_entry = ctk.CTkEntry(self, textvariable=self.video_var)
        self.video_entry.grid(row=1, column=1, padx=0, pady=8, sticky="ew")
        self.select_button = ctk.CTkButton(self, text="选择视频", width=100, command=self._select_video)
        self.select_button.grid(row=1, column=2, padx=(10, 28), pady=8)

        ctk.CTkLabel(self, text="文案：").grid(row=2, column=0, padx=(28, 10), pady=8, sticky="nw")
        self.textbox = ctk.CTkTextbox(self, height=180)
        self.textbox.grid(row=2, column=1, columnspan=2, padx=(0, 28), pady=8, sticky="nsew")
        text_file = self.project_root / "文案.txt"
        if text_file.is_file():
            self.textbox.insert("1.0", text_file.read_text(encoding="utf-8-sig").strip())

        ctk.CTkLabel(self, text="音色：").grid(row=3, column=0, padx=(28, 10), pady=8, sticky="w")
        ctk.CTkLabel(self, text="胖猫（固定）", anchor="w").grid(row=3, column=1, columnspan=2, padx=0, pady=8, sticky="w")

        self.subtitle_var = ctk.BooleanVar(value=True)
        self.subtitle_checkbox = ctk.CTkCheckBox(
            self, text="自动添加同步字幕", variable=self.subtitle_var
        )
        self.subtitle_checkbox.grid(row=4, column=1, columnspan=2, padx=0, pady=(8, 2), sticky="w")

        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.grid(row=5, column=0, columnspan=3, pady=(14, 12))
        self.preview_button = ctk.CTkButton(button_frame, text="试听配音", width=150, command=self._preview)
        self.preview_button.pack(side="left", padx=10)
        self.generate_button = ctk.CTkButton(button_frame, text="生成视频", width=150, command=self._generate_video)
        self.generate_button.pack(side="left", padx=10)

        self.status_var = ctk.StringVar(value="状态：等待生成")
        self.status_label = ctk.CTkLabel(self, textvariable=self.status_var, anchor="w")
        self.status_label.grid(row=6, column=0, columnspan=3, padx=28, pady=(8, 12), sticky="ew")

        self.open_button = ctk.CTkButton(self, text="打开输出文件夹", command=self._open_output)
        self.open_button.grid(row=7, column=0, columnspan=3, pady=(0, 24))

    def _select_video(self) -> None:
        path = filedialog.askopenfilename(
            title="选择猫咪视频",
            filetypes=[("视频文件", "*.mp4 *.mov *.mkv *.webm"), ("所有文件", "*.*")],
        )
        if path:
            self.video_var.set(path)

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
        self.after(0, lambda: self.select_button.configure(state=state))
        self.after(0, lambda: self.subtitle_checkbox.configure(state=state))

    def _set_status(self, message: str) -> None:
        self.after(0, lambda: self.status_var.set(f"状态：{message}"))

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
            self._set_status("试听配音生成完成")
            os.startfile(output)

        self._run_worker(action)

    def _generate_video(self) -> None:
        try:
            text = self._text()
            video = self._video()
            subtitles_enabled = bool(self.subtitle_var.get())
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
            subtitle = None
            if subtitles_enabled:
                self._set_status("正在按真实语音时间对齐字幕…")
                subtitle = self.subtitle_pipeline.create(
                    text, audio, video, self.temp_dir / f"subtitle_{stamp}.ass"
                )
            self._set_status("正在循环、裁剪并合成视频…")
            result = self.video_maker.make(video, audio, output, subtitle_path=subtitle)
            detail = (
                f"基础视频 {result.video_duration:.2f}s，配音 {result.audio_duration:.2f}s，"
                f"循环 {result.loops} 次，成片 {result.output_duration:.2f}s"
            )
            self._set_status(f"生成完成：{detail}")
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
