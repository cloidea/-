"""Presentation only: two calm, task-focused pages over the existing workflow."""
import customtkinter as ctk

BLUE = "#007AFF"
INK = "#1D1D1F"
GRAY = "#86868B"
SURFACE = "#FFFFFF"
BG = "#F5F5F7"
LINE = "#E5E5EA"


def build(w):
    ctk.set_appearance_mode("light")
    w.title("猫咪短视频")
    w.geometry("1000x860")
    w.minsize(820, 700)
    w.configure(fg_color=BG)
    w.protocol("WM_DELETE_WINDOW", w._on_close)
    w.grid_columnconfigure(0, weight=1)
    w.grid_rowconfigure(1, weight=1)

    def label(parent, text, size=14, color=INK, bold=False, **kwargs):
        return ctk.CTkLabel(parent, text=text, text_color=color,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=size, weight="bold" if bold else "normal"), **kwargs)

    def button(parent, text, command, primary=False, **kwargs):
        return ctk.CTkButton(parent, text=text, command=command,
            fg_color=BLUE if primary else "#F0F0F3", hover_color="#0066DD" if primary else "#E5E5EA",
            text_color="white" if primary else INK, corner_radius=10,
            height=40, font=ctk.CTkFont(size=14), **kwargs)

    def card(parent):
        return ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=14, border_width=1, border_color=LINE)

    header = ctk.CTkFrame(w, fg_color=SURFACE, corner_radius=0)
    header.grid(row=0, column=0, sticky="ew")
    label(header, "猫咪短视频", 17, bold=True).pack(side="left", padx=32, pady=18)
    w.navigation = ctk.CTkSegmentedButton(header, values=["制作视频", "发布视频"],
        command=w._switch_page, selected_color=BLUE, selected_hover_color="#0066DD",
        unselected_color="#EFEFF2", unselected_hover_color=LINE, fg_color=LINE,
        text_color=INK, text_color_disabled=GRAY, height=36, corner_radius=10,
        font=ctk.CTkFont(size=14))
    w.navigation.pack(side="right", padx=32, pady=14)
    w.navigation.set("制作视频")
    container = ctk.CTkFrame(w, fg_color="transparent")
    container.grid(row=1, column=0, sticky="nsew", padx=24, pady=16)
    container.grid_rowconfigure(0, weight=1)
    container.grid_columnconfigure(0, weight=1)
    w.make_page = ctk.CTkScrollableFrame(container, fg_color=BG, corner_radius=0)
    w.publish_page = ctk.CTkScrollableFrame(container, fg_color=BG, corner_radius=0)
    for page in (w.make_page, w.publish_page):
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_columnconfigure(0, weight=1)
    w.publish_page.grid_remove()
    label(w.make_page, "制作视频", 30, bold=True).grid(row=0, column=0, sticky="w", pady=(0, 2))
    label(w.make_page, "选个模板，写句文案。", 15, GRAY).grid(row=1, column=0, sticky="w", pady=(0, 20))
    heading = ctk.CTkFrame(w.make_page, fg_color="transparent")
    heading.grid(row=2, column=0, sticky="ew")
    label(heading, "选择模板", 16, bold=True).pack(side="left")
    w.manage_button = button(heading, "管理模板", w._toggle_templates, width=100)
    w.manage_button.pack(side="right")
    w.template_tools = ctk.CTkFrame(w.make_page, fg_color="transparent")
    w.template_tools.grid(row=3, column=0, sticky="ew", pady=8)
    w.refresh_templates_button = button(w.template_tools, "刷新模板", w._refresh_templates, width=110)
    w.refresh_templates_button.pack(side="left", padx=(0, 8))
    w.select_button = button(w.template_tools, "选择本地视频", w._select_video, width=130)
    w.select_button.pack(side="left")
    button(w.template_tools, "打开模板文件夹", lambda: __import__("os").startfile(w.video_assets_dir), width=145).pack(side="right")
    w.template_tools.grid_remove()
    w.template_frame = ctk.CTkFrame(w.make_page, fg_color="transparent")
    w.template_frame.grid(row=4, column=0, sticky="ew", pady=(8, 0))
    w.video_var = ctk.StringVar(value="")
    w.template_info_var = ctk.StringVar(value="暂无可用模板")
    w.template_info_label = label(w.make_page, "", 12, GRAY, textvariable=w.template_info_var)
    w.template_info_label.grid(row=5, column=0, sticky="w", pady=(4, 18))
    w._build_template_picker()
    heading = ctk.CTkFrame(w.make_page, fg_color="transparent")
    heading.grid(row=6, column=0, sticky="ew", pady=(0, 8))
    label(heading, "配音文案", 16, bold=True).pack(side="left")
    w.preview_button = button(heading, "▷ 试听", w._preview, width=85)
    w.preview_button.pack(side="right")
    w.textbox = ctk.CTkTextbox(w.make_page, height=128, fg_color=SURFACE, text_color=INK,
        border_color=LINE, border_width=1, corner_radius=12, font=ctk.CTkFont(size=16))
    w.textbox.grid(row=7, column=0, sticky="ew")
    text_file = w.project_root / "文案.txt"
    if text_file.is_file():
        w.textbox.insert("1.0", text_file.read_text(encoding="utf-8-sig").strip())
    w.textbox.bind("<KeyRelease>", w._update_duration_estimate)
    w.textbox.bind("<<Paste>>", lambda _: w.after(10, w._update_duration_estimate))
    w.estimate_var = ctk.StringVar()
    w.estimate_label = label(w.make_page, "", 12, GRAY, textvariable=w.estimate_var)
    w.estimate_label.grid(row=8, column=0, sticky="w", pady=(6, 10))
    w._update_duration_estimate()
    w.more_button = button(w.make_page, "⌄  更多选项 · 胖猫音色 / 同步字幕", w._toggle_options)
    w.more_button.grid(row=9, column=0, sticky="ew")
    w.options_frame = card(w.make_page)
    w.options_frame.grid(row=10, column=0, sticky="ew", pady=8)
    w.subtitle_var = ctk.BooleanVar(value=True)
    w.subtitle_checkbox = ctk.CTkCheckBox(w.options_frame, text="自动添加同步字幕", variable=w.subtitle_var,
        fg_color=BLUE, text_color=INK)
    w.subtitle_checkbox.pack(side="left", padx=18, pady=18)
    w.regenerate_button = button(w.options_frame, "下次重新配音", w._force_next_voice, width=140)
    w.regenerate_button.pack(side="right", padx=18, pady=12)
    w.options_frame.grid_remove()
    w.make_actions = ctk.CTkFrame(container, fg_color=BG)
    w.make_actions.grid(row=1, column=0, sticky="ew", pady=(8, 0))
    w.generate_publish_button = button(w.make_actions, "生成并上传", w._open_generate_publish, True, width=205)
    w.generate_publish_button.pack(side="right", padx=16)
    w.generate_button = button(w.make_actions, "仅生成视频", w._generate_video, width=150)
    w.generate_button.pack(side="right")
    w.result_frame = card(w.make_page)
    w.result_frame.grid(row=12, column=0, sticky="ew", pady=8)
    w.result_var = ctk.StringVar()
    label(w.result_frame, "", 13, textvariable=w.result_var, wraplength=380).pack(side="left", padx=18, pady=18)
    button(w.result_frame, "去发布 →", w._publish_result, width=100).pack(side="right", padx=12)
    button(w.result_frame, "打开视频", w._open_result, width=95).pack(side="right")
    w.result_frame.grid_remove()

    label(w.publish_page, "发布视频", 30, bold=True).grid(row=0, column=0, sticky="w", pady=(0, 2))
    label(w.publish_page, "上传和填写交给工具，最后由你确认。", 15, GRAY).grid(row=1, column=0, sticky="w", pady=(0, 22))
    video_card = card(w.publish_page)
    video_card.grid(row=2, column=0, sticky="ew", pady=(0, 22))
    w.publish_video_var = ctk.StringVar(value="尚未选择成片\n可使用刚生成的视频，或选择本地视频。")
    label(video_card, "", 15, textvariable=w.publish_video_var, justify="left", wraplength=400).pack(side="left", padx=22, pady=24)
    w.publish_existing_button = button(video_card, "选择 / 更换视频", w._choose_publish_video, width=155)
    w.publish_existing_button.pack(side="right", padx=20)
    label(w.publish_page, "发布标题", 15, bold=True).grid(row=3, column=0, sticky="w", pady=(0, 8))
    w.publish_title_entry = ctk.CTkEntry(w.publish_page, placeholder_text="给视频起个标题", height=46,
        fg_color=SURFACE, border_color=LINE, corner_radius=10, text_color=INK, font=ctk.CTkFont(size=15))
    w.publish_title_entry.grid(row=4, column=0, sticky="ew", pady=(0, 18))
    label(w.publish_page, "话题标签", 15, bold=True).grid(row=5, column=0, sticky="w", pady=(0, 8))
    w.publish_tags_entry = ctk.CTkEntry(w.publish_page, placeholder_text="#猫咪 #搞笑 #晚安", height=46,
        fg_color=SURFACE, border_color=LINE, corner_radius=10, text_color=INK, font=ctk.CTkFont(size=15))
    w.publish_tags_entry.grid(row=6, column=0, sticky="ew", pady=(0, 18))
    label(w.publish_page, "发布到", 15, bold=True).grid(row=7, column=0, sticky="w", pady=(0, 8))
    platforms = ctk.CTkFrame(w.publish_page, fg_color="transparent")
    platforms.grid(row=8, column=0, sticky="ew")
    platforms.grid_columnconfigure((0, 1), weight=1)
    w.douyin_var, w.xiaohongshu_var = ctk.BooleanVar(value=True), ctk.BooleanVar(value=False)
    for index, (name, variable, attribute) in enumerate((
        ("抖音", w.douyin_var, "douyin_checkbox"), ("小红书", w.xiaohongshu_var, "xiaohongshu_checkbox"))):
        panel = card(platforms)
        panel.grid(row=0, column=index, sticky="ew", padx=(0, 8) if index == 0 else (8, 0))
        box = ctk.CTkCheckBox(panel, text=name, variable=variable, fg_color=BLUE,
            text_color=INK, font=ctk.CTkFont(size=17, weight="bold"), checkbox_width=24, checkbox_height=24)
        box.pack(anchor="w", padx=24, pady=22)
        setattr(w, attribute, box)
    label(w.publish_page, "首次使用需要登录；上传完成后，请在网页检查并确认发布。", 12, GRAY).grid(row=9, column=0, sticky="w", pady=(14, 20))
    w.publish_button = button(w.publish_page, "上传并填写", w._upload_selected, True, width=205)
    w.publish_button.grid(row=10, column=0, sticky="e")

    footer = ctk.CTkFrame(w, fg_color=SURFACE, corner_radius=0)
    footer.grid(row=2, column=0, sticky="ew")
    footer.grid_columnconfigure(0, weight=1)
    w.status_var = ctk.StringVar(value="准备就绪")
    w.status_label = label(footer, "", 12, GRAY, textvariable=w.status_var, wraplength=430, justify="left", anchor="w")
    w.status_label.grid(row=0, column=0, sticky="ew", padx=24, pady=(12, 4))
    w.media_status_var = ctk.StringVar(value="")
    w.media_status_label = label(footer, "", 11, GRAY, textvariable=w.media_status_var, anchor="w")
    w.media_status_label.grid(row=1, column=0, sticky="ew", padx=24, pady=(0, 10))
    w.open_button = button(footer, "输出文件夹", w._open_output, width=115)
    w.open_button.grid(row=0, column=1, padx=20, pady=8)
    w.resume_upload_button = button(footer, "继续上传", w._resume_upload, width=115)
    w.cancel_upload_button = button(footer, "停止上传", w._cancel_upload, width=115)
    w.resume_upload_button.configure(state="disabled")
    w.cancel_upload_button.configure(state="disabled")
    w.status_var.trace_add("write", lambda *_: w.after_idle(w._sync_context_actions))
