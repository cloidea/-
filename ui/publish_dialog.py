"""Small confirmation sheet for the existing generate-then-upload workflow."""
import customtkinter as ctk
from ui.layout import BLUE, BG, INK, LINE


class PublishDialog(ctk.CTkToplevel):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.title("生成并上传")
        self.geometry("520x440")
        self.resizable(False, False)
        self.configure(fg_color=BG)
        self.transient(owner)
        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self, text="生成后，直接上传", text_color=INK,
                     font=ctk.CTkFont(size=23, weight="bold")).grid(row=0, padx=24, pady=(20, 8), sticky="w")
        ctk.CTkLabel(self, text="使用当前模板和文案，无需再次选择成片。", text_color=INK).grid(row=1, padx=24, sticky="w")
        ctk.CTkLabel(self, text="发布标题", text_color=INK).grid(row=2, padx=24, pady=(12, 0), sticky="w")
        self.title_entry = ctk.CTkEntry(self, height=40, fg_color="white", border_color=LINE)
        self.title_entry.grid(row=3, padx=24, sticky="ew")
        self.title_entry.insert(0, owner.publish_title_entry.get())
        ctk.CTkLabel(self, text="话题标签", text_color=INK).grid(row=4, padx=24, pady=(8, 0), sticky="w")
        self.tags_entry = ctk.CTkEntry(self, height=40, placeholder_text="#猫咪 #搞笑", fg_color="white", border_color=LINE)
        self.tags_entry.grid(row=5, padx=24, sticky="ew")
        self.tags_entry.insert(0, owner.publish_tags_entry.get())
        platforms = ctk.CTkFrame(self, fg_color="transparent")
        platforms.grid(row=6, padx=24, pady=16, sticky="ew")
        self.douyin = ctk.BooleanVar(value=owner.douyin_var.get())
        self.xiaohongshu = ctk.BooleanVar(value=owner.xiaohongshu_var.get())
        for name, variable in (("抖音", self.douyin), ("小红书", self.xiaohongshu)):
            ctk.CTkCheckBox(platforms, text=name, variable=variable, fg_color=BLUE, text_color=INK).pack(side="left", padx=(0, 24))
        self.error = ctk.StringVar(value="上传并填写完成后，仍需你在网页确认发布。")
        ctk.CTkLabel(self, textvariable=self.error, text_color="#666666", wraplength=470).grid(row=7, padx=24, sticky="w")
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=8, padx=24, pady=16, sticky="ew")
        ctk.CTkButton(actions, text="确认生成并上传", command=self.confirm, fg_color=BLUE, height=40).pack(side="right")
        ctk.CTkButton(actions, text="取消", command=self.close, fg_color="#E5E5EA", text_color=INK, width=90, height=40).pack(side="right", padx=12)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda _: self.close())
        self.after(100, self._activate)

    def _activate(self):
        if self.winfo_exists():
            self.grab_set()
            self.title_entry.focus_set()

    def close(self):
        self.grab_release()
        self.destroy()

    def confirm(self):
        if self.owner.busy:
            return
        title = self.title_entry.get().strip()
        if not title:
            self.error.set("请填写发布标题。")
            return
        if not (self.douyin.get() or self.xiaohongshu.get()):
            self.error.set("请至少选择一个发布平台。")
            return
        for entry, value in ((self.owner.publish_title_entry, title),
                             (self.owner.publish_tags_entry, self.tags_entry.get().strip())):
            entry.delete(0, "end")
            entry.insert(0, value)
        self.owner.douyin_var.set(self.douyin.get())
        self.owner.xiaohongshu_var.set(self.xiaohongshu.get())
        self.close()
        self.owner._generate_video(publish_after=True)
