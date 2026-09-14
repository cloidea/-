from __future__ import annotations

from dataclasses import dataclass

from .base import AlignmentError, SubtitleCue, TimedToken


STRONG_END = set("。？！?!")
SOFT_END = set("，,；;")
IGNORED = set("，。！？；：、,.!?;:\"'“”‘’（）()【】[]《》<>…—- \t\r\n")


@dataclass(frozen=True)
class PaginationSettings:
    preferred_min_chars: int = 6
    preferred_max_chars: int = 12
    absolute_max_chars: int = 18
    pause_threshold: float = 0.30
    min_duration: float = 0.70
    max_duration: float = 2.50
    end_buffer: float = 0.08


@dataclass
class _Unit:
    text: str
    start: float
    end: float


class SubtitlePaginator:
    def __init__(self, settings: PaginationSettings | None = None) -> None:
        self.settings = settings or PaginationSettings()

    def _units(self, source_text: str, tokens: list[TimedToken]) -> list[_Unit]:
        spoken = [char for char in source_text if char not in IGNORED]
        aligned = [token.text for token in tokens]
        if spoken != aligned:
            raise AlignmentError("强制对齐结果与已知文案字符顺序不一致。")

        units: list[_Unit] = []
        token_index = 0
        leading = ""
        for char in source_text:
            if char.isspace():
                continue
            if char in IGNORED:
                if units:
                    units[-1].text += char
                else:
                    leading += char
                continue
            token = tokens[token_index]
            units.append(_Unit(leading + char, token.start, token.end))
            leading = ""
            token_index += 1
        if leading and units:
            units[-1].text += leading
        return units

    @staticmethod
    def _length(units: list[_Unit]) -> int:
        return len("".join(unit.text for unit in units).replace("\n", ""))

    def _initial_pages(self, units: list[_Unit]) -> list[list[_Unit]]:
        pages: list[list[_Unit]] = []
        current: list[_Unit] = []
        cfg = self.settings
        for index, unit in enumerate(units):
            current.append(unit)
            next_unit = units[index + 1] if index + 1 < len(units) else None
            length = self._length(current)
            duration = current[-1].end - current[0].start
            punctuation = current[-1].text[-1]
            pause = (next_unit.start - current[-1].end) if next_unit else 0.0

            should_cut = next_unit is None
            should_cut |= punctuation in STRONG_END
            should_cut |= pause >= cfg.pause_threshold and length >= cfg.preferred_min_chars
            should_cut |= punctuation in SOFT_END and (
                length >= cfg.preferred_min_chars or duration >= cfg.min_duration
            )
            should_cut |= length >= cfg.preferred_max_chars and duration >= cfg.min_duration
            should_cut |= length >= cfg.absolute_max_chars
            should_cut |= duration >= cfg.max_duration and length >= cfg.preferred_min_chars
            if should_cut:
                pages.append(current)
                current = []
        if current:
            pages.append(current)
        return pages

    def _merge_short(self, pages: list[list[_Unit]]) -> list[list[_Unit]]:
        cfg = self.settings
        merged: list[list[_Unit]] = []
        for page in pages:
            duration = page[-1].end - page[0].start
            if duration < cfg.min_duration and merged:
                combined_length = self._length(merged[-1]) + self._length(page)
                combined_duration = page[-1].end - merged[-1][0].start
                if combined_length <= cfg.absolute_max_chars and combined_duration <= cfg.max_duration:
                    merged[-1].extend(page)
                    continue
            merged.append(page)
        if len(merged) > 1:
            first = merged[0]
            if first[-1].end - first[0].start < cfg.min_duration:
                combined_length = self._length(first) + self._length(merged[1])
                if combined_length <= cfg.absolute_max_chars:
                    merged[1] = first + merged[1]
                    merged.pop(0)
        return merged

    def paginate(self, source_text: str, tokens: list[TimedToken]) -> list[SubtitleCue]:
        if not tokens:
            raise AlignmentError("强制对齐没有返回字词时间戳。")
        pages = self._merge_short(self._initial_pages(self._units(source_text, tokens)))
        cues: list[SubtitleCue] = []
        for index, page in enumerate(pages):
            start = page[0].start
            end = page[-1].end + self.settings.end_buffer
            if index + 1 < len(pages):
                end = min(end, pages[index + 1][0].start)
            end = max(end, page[-1].end)
            text = "".join(unit.text for unit in page).strip()
            if len(text) > self.settings.preferred_max_chars:
                split_at = min(self.settings.preferred_max_chars, len(text))
                for punctuation in "，,；;":
                    found = text.rfind(punctuation, 0, split_at + 1)
                    if found >= self.settings.preferred_min_chars - 1:
                        split_at = found + 1
                        break
                text = text[:split_at] + "\\N" + text[split_at:]
            cues.append(SubtitleCue(text, start, end))
        return cues
