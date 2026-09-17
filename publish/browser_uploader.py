"""Browser upload assistant. The final Publish button is never activated."""
from __future__ import annotations

import queue
import re
import threading
import time
import logging
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from publish.assistant import PLATFORM_URLS, normalize_hashtags


class UploadCancelled(RuntimeError):
    pass


def caption_matches(actual: str, body: str, hashtags: str) -> bool:
    """Compare the entire visible caption, including # and token boundaries."""
    expected = " ".join(filter(None, (body, normalize_hashtags(hashtags))))
    normalize = lambda value: " ".join(value.replace("\u200b", "").split())
    return normalize(actual) == normalize(expected)


@dataclass(frozen=True)
class UploadResult:
    platform: str
    ready: bool
    message: str


class UploadControl:
    def __init__(self) -> None:
        self.cancelled = threading.Event()
        self.resume = threading.Event()

    def check(self) -> None:
        if self.cancelled.is_set():
            raise UploadCancelled("已停止自动操作，网页中已上传的内容保留，请自行检查。")


class PortalUploader:
    """Small platform adapters; ambiguous page elements fail closed.

    Selectors use semantic placeholders and editor roles, not CSS hashes.
    A page redesign raises a handoff message rather than guessing a button.
    """

    def __init__(self, platform, page, control, status, handoff):
        self.platform = platform
        self.page = page
        self.control = control
        self.status = status
        self.handoff = handoff
        self.name = "抖音" if platform == "douyin" else "小红书"

    def _check(self):
        self.control.check()
        if self.page.is_closed():
            raise RuntimeError("发布窗口已关闭，请重新上传。")

    def _tick(self):
        self._check()
        self.page.wait_for_timeout(250)

    def _wait(self, predicate, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self._check()
            value = predicate()
            if value:
                return value
            self._tick()
        return None

    def _pause(self, reason):
        self.control.resume.clear()
        self.status(f"{self.name}：{reason}")
        self.handoff(f"{self.name}：{reason}；完成后点击软件的“继续上传”。")
        deadline = time.monotonic() + 900
        try:
            while not self.control.resume.is_set():
                self._tick()
                if time.monotonic() >= deadline:
                    raise RuntimeError("等待处理超过15分钟，已停止自动操作。")
        finally:
            self.handoff(None)

    def _unique_visible(self, locator):
        matches = [item for item in locator.all() if item.is_visible()]
        return matches[0] if len(matches) == 1 else None

    def _file_input(self):
        # A hidden native input is normal; choose a video-specific input first.
        expected_host = urlparse(PLATFORM_URLS[self.platform]).hostname
        if urlparse(self.page.url).hostname != expected_host:
            return None
        inputs = self.page.locator('input[type="file"]').all()
        videos = [item for item in inputs if re.search(
            r"video|mp4|mov|mkv|webm", item.get_attribute("accept") or "", re.I
        )]
        if len(videos) == 1:
            return videos[0]
        if len(inputs) == 1 and not inputs[0].get_attribute("accept"):
            return inputs[0]
        return None

    def _title(self):
        for selector in (
            'input[placeholder*="标题"], textarea[placeholder*="标题"]',
            '[contenteditable="true"][data-placeholder*="标题"], [contenteditable="true"][aria-label*="标题"]',
        ):
            field = self._unique_visible(self.page.locator(selector))
            if field is not None:
                return field
        return self._unique_visible(self.page.get_by_placeholder(re.compile("标题")))

    def _editor(self):
        # Both portals use rich editors; their hints can be data-placeholder
        # attributes instead of native input placeholders. The title can also
        # be contenteditable, so finding all rich editors together is ambiguous.
        for selector in (
            '[contenteditable="true"][data-placeholder*="正文"], [contenteditable="true"][data-placeholder*="简介"], [contenteditable="true"][data-placeholder*="描述"]',
            '[contenteditable="true"][aria-label*="正文"], [contenteditable="true"][aria-label*="简介"]',
            '.tiptap[contenteditable="true"], .ql-editor[contenteditable="true"], [contenteditable="true"][data-slate-editor="true"]',
        ):
            field = self._unique_visible(self.page.locator(selector))
            if field is not None:
                return field
        rich = self._unique_visible(self.page.locator('[contenteditable="true"]'))
        if rich is not None:
            return rich
        return self._unique_visible(self.page.get_by_placeholder(
            re.compile("添加作品简介|添加作品描述|填写.*描述|输入.*正文|填写.*正文|输入.*描述")
        ))

    def _failure(self):
        return self._unique_visible(self.page.get_by_text(
            re.compile(r"^(视频)?上传失败|^文件格式不支持|^视频转码失败")
        ))

    def _uploaded(self):
        if self._failure() is not None:
            raise RuntimeError("平台提示视频上传失败，请在网页检查原因。")
        progressing = self.page.get_by_text(re.compile(r"上传中|正在上传|上传进度"))
        if any(item.is_visible() for item in progressing.all()):
            return False
        # Positive evidence is required; an editable title alone is not success.
        # More than one completion hint may coexist (e.g. success + reupload).
        if any(item.is_visible() for item in self.page.get_by_text(
            re.compile(r"^(视频)?上传成功[！!。]?$|^上传完成[！!。]?$|^重新上传$")
        ).all()):
            return True
        return False

    def _topic_candidate(self, tag):
        # Only a real suggestion list can be clicked. Never click generic text
        # elsewhere in the page, which could be a navigation or publish action.
        options = self.page.locator(
            '[role="listbox"] [role="option"], '
            '[class*="topic"] [class*="item"], '
            '[class*="mention"] [class*="item"], '
            '[class*="suggest"] [class*="item"]'
        ).all()
        pattern = re.compile(r"^#?" + re.escape(tag) + r"(?:\s|$)")
        matches = [item for item in options if item.is_visible()
                   and pattern.search(item.inner_text().strip())]
        return matches[0] if len(matches) == 1 else None

    def _fill_topics(self, editor, hashtags):
        body = self._read_field(editor).strip()
        unmatched = []
        tokens = normalize_hashtags(hashtags).split()
        completed = []
        for token in tokens:
            self._check()
            # Pinning the element avoids re-resolving a vanished placeholder.
            # Insert a whole token so a suggestion popup cannot redirect the
            # remaining characters of #11 into the middle of #1.
            editor.click(timeout=3000)
            editor.press("Control+End")
            self.page.keyboard.insert_text(" " + token)
            candidate = self._wait(lambda: self._topic_candidate(token[1:]), 4)
            if candidate is not None:
                candidate.click(timeout=3000)
            else:
                unmatched.append(token)
            self.page.keyboard.press("Escape")
            completed.append(token)
            if not caption_matches(self._read_field(editor), body, " ".join(completed)):
                # Restore exact text as one transaction; never leave a mangled
                # partial caption or claim that plain text is a selected topic.
                self._write_field(editor, " ".join([body, *tokens]).strip())
                self.page.keyboard.press("Escape")
                return tokens
        return unmatched

    def run(self, video, title, hashtags):
        self._check()
        self.status(f"{self.name}：正在打开发布窗口…")
        source = str(Path(video).resolve())
        same_video = getattr(self.page, "_cat_uploaded_video", None) == source
        if same_video and self._editor() is not None:
            return self._finish_fields(title, hashtags)
        if self._editor() is not None:
            self._pause("该窗口还有上一条视频，请先发布或保存草稿；继续将进入新视频上传页")
        try:
            self.page.goto(PLATFORM_URLS[self.platform], wait_until="domcontentloaded", timeout=45000)
        except Exception:
            self._check()
            self._pause("网页加载较慢，请在浏览器完成加载或刷新")
        file_input = self._wait(self._file_input, 8)
        if file_input is None and self.platform == "xiaohongshu":
            tab = self._unique_visible(self.page.get_by_text("上传视频", exact=True))
            if tab is not None:
                tab.click(timeout=3000)
                file_input = self._wait(self._file_input, 5)
        if file_input is None:
            self._pause("请登录账号、处理验证码并进入视频上传页面")
            file_input = self._wait(self._file_input, 10)
        if file_input is None:
            raise RuntimeError("未找到唯一的视频上传框，页面可能已改版；没有上传文件。")

        self.status(f"{self.name}：正在上传 {Path(video).name}…")
        self._check()
        file_input.set_input_files(str(video), timeout=30000)
        self.page._cat_uploaded_video = source
        return self._finish_fields(title, hashtags)

    @staticmethod
    def _pin_field(field):
        # Locator selectors based on placeholders stop matching after typing.
        # Keep the actual node for this short edit transaction instead.
        handle = field.element_handle(timeout=5000)
        if handle is None:
            raise RuntimeError("输入框发生变化，请保留网页后重试。")
        return handle

    @staticmethod
    def _read_field(field):
        if field.get_attribute("contenteditable") == "true":
            return field.inner_text()
        return field.input_value()

    def _write_field(self, field, value):
        field.scroll_into_view_if_needed(timeout=5000)
        field.fill(value, timeout=5000)
        field.press("Tab")
        if self._read_field(field).strip() != value.strip():
            field.click(timeout=3000)
            field.press("Control+A")
            self.page.keyboard.insert_text(value)
            field.press("Tab")
        if self._read_field(field).strip() != value.strip():
            raise RuntimeError("输入框填写后核对不一致，请检查网页内容。")

    def _finish_fields(self, title, hashtags):
        # Fill as soon as the edit form is available. Some portals expose their
        # completion text later; that must not block title/body for ten minutes.
        self.status(f"{self.name}：等待编辑页并填写标题和正文…")
        title_field = self._wait(self._title, 60)
        if title_field is None:
            raise RuntimeError("未找到标题框，请保留当前页面用于检查。")
        title_field = self._pin_field(title_field)
        maximum = title_field.get_attribute("maxlength")
        if maximum and maximum.isdigit() and len(title) > int(maximum):
            raise RuntimeError(f"标题超过网页的{maximum}字限制；请修改标题。")
        self._write_field(title_field, title)
        editor = self._wait(self._editor, 30)
        if editor is None:
            raise RuntimeError("标题已填写，但未找到唯一的正文框，请保留当前页面。")
        editor = self._pin_field(editor)
        self._write_field(editor, title)
        unmatched = self._fill_topics(editor, hashtags)
        actual = self._read_field(editor)
        if not caption_matches(actual, title, hashtags):
            raise RuntimeError("正文或完整标签（包含#号）核对失败，请在网页检查。")
        self.status(f"{self.name}：标题和正文已填写，正在确认上传完成…")
        if not self._wait(self._uploaded, 600):
            self._pause("尚未确认上传完成，请检查网页的上传进度或验证码")
            if not self._wait(self._uploaded, 10):
                raise RuntimeError("无法确认上传完成，已保留网页，请手动检查。")

        if unmatched:
            return UploadResult(self.platform, False,
                "视频和文案已填写，以下标签仅作为文字写入，需在网页选择话题：" + "、".join(unmatched))
        return UploadResult(self.platform, True, "视频已上传，标题和话题已填写，请检查封面并手动点击发布。")


class BrowserUploadService:
    """One owning thread for Playwright, reusable contexts, no cookie extraction."""

    def __init__(self, profile_root: Path):
        self.profile_root = Path(profile_root).resolve()
        self._jobs = queue.Queue()
        self._thread = None
        self._guard = threading.Lock()
        self._active = None

    def submit(self, video, title, tags, platforms, status, handoff, control):
        future = Future()
        with self._guard:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._serve, daemon=True)
                self._thread.start()
        self._jobs.put((future, Path(video), title, tags, list(platforms), status, handoff, control))
        return future

    def close(self):
        if self._active is not None:
            self._active.cancelled.set()
        self._jobs.put(None)

    def join(self, timeout=2):
        if self._thread is not None:
            self._thread.join(timeout)

    def _serve(self):
        contexts = {}
        runtime = None
        try:
            while True:
                try:
                    job = self._jobs.get(timeout=0.25)
                except queue.Empty:
                    # Pump browser close events while idle so closed contexts
                    # aren't reused on the next job.
                    for context in list(contexts.values()):
                        try:
                            if context.pages:
                                context.pages[0].wait_for_timeout(10)
                        except Exception:
                            pass
                    continue
                if job is None:
                    return
                future, video, title, tags, platforms, status, handoff, control = job
                self._active = control
                try:
                    from playwright.sync_api import sync_playwright
                    if not video.is_file():
                        raise FileNotFoundError(f"视频不存在：{video}")
                    if not platforms or any(p not in PLATFORM_URLS for p in platforms):
                        raise ValueError("请选择有效的发布平台。")
                    if runtime is None:
                        runtime = sync_playwright().start()
                    results = []
                    for platform in platforms:
                        control.check()
                        try:
                            context = contexts.get(platform)
                            if context is None:
                                profile = self.profile_root / platform
                                profile.mkdir(parents=True, exist_ok=True)
                                context = runtime.chromium.launch_persistent_context(
                                    str(profile), channel="msedge", headless=False,
                                    no_viewport=True, chromium_sandbox=True,
                                    args=["--start-maximized"],
                                )
                                contexts[platform] = context
                                context.on("close", lambda *_, key=platform: contexts.pop(key, None))
                            host = urlparse(PLATFORM_URLS[platform]).hostname
                            pages = [p for p in context.pages if not p.is_closed()]
                            page = next((p for p in pages if urlparse(p.url).hostname == host), None)
                            if page is None:
                                page = next((p for p in pages if p.url == "about:blank"), None)
                            if page is None:
                                page = context.new_page()
                            page.bring_to_front()
                            page.set_default_timeout(3000)
                            result = PortalUploader(platform, page, control, status, handoff).run(video, title, tags)
                        except UploadCancelled:
                            raise
                        except Exception as exc:
                            logging.exception("发布步骤失败：%s", platform)
                            detail = str(exc)
                            if "Timeout" in detail or "Timeout" in type(exc).__name__:
                                detail = "网页控件未及时响应。请检查登录或验证码；保留发布页，点击重试失败平台。"
                            elif "closed" in detail.lower():
                                detail = "发布窗口已关闭。请点击重试失败平台重新打开。"
                            result = UploadResult(platform, False, detail)
                        results.append(result)
                    future.set_result(results)
                except Exception as exc:
                    future.set_exception(exc)
                finally:
                    self._active = None
        finally:
            for context in list(contexts.values()):
                try:
                    context.close()
                except Exception:
                    pass
            if runtime is not None:
                runtime.stop()
