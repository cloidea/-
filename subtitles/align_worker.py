from __future__ import annotations

import json
import math
import sys
import unicodedata
import re
from pathlib import Path


IGNORED = set("，。！？；：、,.!?;:\"'“”‘’（）()【】[]《》<>…—- \t\r\n")
DIGITS = set("0123456789")
CHINESE_DIGITS = "零一二三四五六七八九"
NUMBER = re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d+(?:[.．]\d+)?[%％]?")


def _read_number(raw):
    value = unicodedata.normalize("NFKC", raw)
    if "-" in value or "/" in value:
        from datetime import date
        y, m, d = re.split(r"[-/]", value)
        date(int(y), int(m), int(d))
        return "".join(CHINESE_DIGITS[int(c)] for c in y) + "年" + _integer_to_chinese(m.lstrip("0") or "0") + "月" + _integer_to_chinese(d.lstrip("0") or "0") + "日"
    percent = value.endswith("%")
    value = value.rstrip("%")
    parts = value.split(".")
    speech = _integer_to_chinese(parts[0])
    if len(parts) == 2:
        speech += "点" + "".join(CHINESE_DIGITS[int(c)] for c in parts[1])
    return ("百分之" if percent else "") + speech


def spoken_text(text):
    return NUMBER.sub(lambda match: _read_number(match.group()), text)


def _integer_to_chinese(value: str) -> str:
    """Normalize short Arabic integers to the form normally spoken by Chinese TTS."""
    if not value or any(char not in DIGITS for char in value):
        return value
    if len(value) > 4 or (len(value) > 1 and value.startswith("0")):
        return "".join(CHINESE_DIGITS[int(char)] for char in value)
    number = int(value)
    if number == 0:
        return CHINESE_DIGITS[0]
    units = ((1000, "千"), (100, "百"), (10, "十"), (1, ""))
    output: list[str] = []
    pending_zero = False
    for divisor, unit in units:
        digit, number = divmod(number, divisor)
        if digit:
            if pending_zero and output:
                output.append("零")
            if not (divisor == 10 and digit == 1 and not output):
                output.append(CHINESE_DIGITS[digit])
            output.append(unit)
            pending_zero = False
        elif output and number:
            pending_zero = True
    return "".join(output)


def _alignment_units(text: str) -> list[tuple[str, str]]:
    """Return (subtitle text, acoustic alignment text) units."""
    units: list[tuple[str, str]] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char in IGNORED:
            index += 1
            continue
        number = NUMBER.match(text, index)
        if number:
            raw = number.group()
            units.append((raw, _read_number(raw)))
            index = number.end()
            continue
        units.append((char, unicodedata.normalize("NFKC", char).lower()))
        index += 1
    return units


def _acoustic_candidates(chars, tokenizer):
    """Use same-pronunciation characters only for out-of-vocabulary Hanzi.

    The display text is never changed. Unknown punctuation/symbols do not get
    arbitrary timestamps: unsupported pronunciations still produce an error.
    """
    ids = [tokenizer.convert_tokens_to_ids(char) for char in chars]
    missing = [i for i, token in enumerate(ids) if token is None or token == tokenizer.unk_token_id]
    result = [[token] for token in ids]
    if not missing:
        return result
    from pypinyin import Style, lazy_pinyin, pinyin

    pronunciations = lazy_pinyin("".join(chars), style=Style.TONE3, neutral_tone_with_five=True)
    if len(pronunciations) != len(chars):
        raise RuntimeError("文案含有无法按字解析的发音，请将特殊符号写为实际读音。")
    vocab_by_sound = {}
    for char, token in tokenizer.get_vocab().items():
        if len(char) != 1 or not ('\u3400' <= char <= '\u9fff'):
            continue
        if token in tokenizer.all_special_ids:
            continue
        sounds = pinyin(char, style=Style.TONE3, heteronym=True, neutral_tone_with_five=True)[0]
        for sound in sounds:
            vocab_by_sound.setdefault(sound, []).append(token)
    unsupported = []
    for index in missing:
        char = chars[index]
        candidates = vocab_by_sound.get(pronunciations[index], []) if '\u3400' <= char <= '\u9fff' else []
        if not candidates:
            unsupported.append(char)
        else:
            result[index] = sorted(set(candidates))
    if unsupported:
        raise RuntimeError("这些字符没有可用的发音映射，请改写为实际读音：" + "".join(dict.fromkeys(unsupported)))
    return result


def _extend_emission(emission, candidates):
    import torch
    columns = []
    synthetic = {}
    tokens = []
    for group in candidates:
        if len(group) == 1:
            tokens.append(group[0])
            continue
        key = tuple(group)
        if key not in synthetic:
            synthetic[key] = emission.shape[1] + len(columns)
            columns.append(torch.logsumexp(emission[:, group], dim=-1, keepdim=True))
        tokens.append(synthetic[key])
    return (torch.cat([emission, *columns], dim=-1) if columns else emission), tokens


def _ctc_path(emission, token_ids: list[int], blank_id: int) -> list[int]:
    import torch

    states = [blank_id]
    for token in token_ids:
        states.extend((token, blank_id))
    state_count = len(states)
    frame_count = emission.shape[0]
    if frame_count < len(token_ids):
        raise RuntimeError("音频帧数不足，无法完成已知文案的强制对齐。")

    negative = -float("inf")
    previous = torch.full((state_count,), negative)
    previous[0] = 0.0
    back = torch.full((frame_count, state_count), -1, dtype=torch.int16)
    for frame in range(frame_count):
        current = torch.full((state_count,), negative)
        for state in range(state_count):
            choices = [(previous[state].item(), state)]
            if state > 0:
                choices.append((previous[state - 1].item(), state - 1))
            if (
                state > 1
                and state % 2 == 1
                and states[state] != states[state - 2]
            ):
                choices.append((previous[state - 2].item(), state - 2))
            score, source = max(choices, key=lambda item: item[0])
            if math.isfinite(score):
                current[state] = score + emission[frame, states[state]].item()
                back[frame, state] = source
        previous = current

    candidates = [(previous[state_count - 1].item(), state_count - 1)]
    if state_count > 1:
        candidates.append((previous[state_count - 2].item(), state_count - 2))
    score, state = max(candidates, key=lambda item: item[0])
    if not math.isfinite(score):
        raise RuntimeError("已知文案与音频不匹配，强制对齐没有找到有效路径。")

    path = [0] * frame_count
    for frame in range(frame_count - 1, -1, -1):
        path[frame] = state
        source = int(back[frame, state])
        if source >= 0:
            state = source
    return path


def align(request: dict) -> list[dict]:
    import torch
    import torchaudio
    from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

    device = request["device"] if torch.cuda.is_available() else "cpu"
    try:
        processor = Wav2Vec2Processor.from_pretrained(
            request["model"], cache_dir=request["model_dir"], local_files_only=True
        )
        model = Wav2Vec2ForCTC.from_pretrained(
            request["model"], cache_dir=request["model_dir"], local_files_only=True
        )
    except Exception as exc:
        raise RuntimeError(f"MODEL_NOT_FOUND: {exc}") from exc

    waveform, sample_rate = torchaudio.load(request["audio_path"])
    waveform = waveform.mean(dim=0)
    if sample_rate != 16000:
        waveform = torchaudio.functional.resample(waveform, sample_rate, 16000)
    duration = waveform.numel() / 16000.0

    tokenizer = processor.tokenizer
    units = _alignment_units(request["text"])
    if not units:
        raise RuntimeError("文案中没有可对齐的文字。")
    chars = [char for _, normalized in units for char in normalized]
    candidates = _acoustic_candidates(chars, tokenizer)

    inputs = processor(waveform.numpy(), sampling_rate=16000, return_tensors="pt")
    model = model.to(device).eval()
    with torch.inference_mode():
        logits = model(inputs.input_values.to(device)).logits[0]
        emission = logits.log_softmax(dim=-1).cpu()
    emission, token_ids = _extend_emission(emission, candidates)
    blank_id = tokenizer.pad_token_id
    path = _ctc_path(emission, token_ids, blank_id)
    frame_seconds = duration / emission.shape[0]

    aligned_tokens = []
    for index, char in enumerate(chars):
        state = index * 2 + 1
        frames = [i for i, value in enumerate(path) if value == state]
        if not frames:
            raise RuntimeError(f"字符未获得有效时间戳：{char}")
        aligned_tokens.append(
            {
                "text": char,
                "start": round(frames[0] * frame_seconds, 4),
                "end": round((frames[-1] + 1) * frame_seconds, 4),
                "confidence": round(
                    float(emission[frames, token_ids[index]].exp().mean().item()), 6
                ),
            }
        )

    result = []
    aligned_index = 0
    for original, normalized in units:
        group = aligned_tokens[aligned_index : aligned_index + len(normalized)]
        aligned_index += len(normalized)
        start = float(group[0]["start"])
        end = float(group[-1]["end"])
        confidence = sum(float(token["confidence"]) for token in group) / len(group)
        if len(original) == 1:
            result.append(
                {
                    "text": original,
                    "start": round(start, 4),
                    "end": round(end, 4),
                    "confidence": round(confidence, 6),
                }
            )
            continue
        step = max(0.0001, (end - start) / len(original))
        for position, char in enumerate(original):
            if char in IGNORED:
                continue
            result.append(
                {
                    "text": char,
                    "start": round(start + position * step, 4),
                    "end": round(start + (position + 1) * step, 4),
                    "confidence": round(confidence, 6),
                }
            )
    return result


def main() -> int:
    request_path = Path(sys.argv[1])
    response_path = Path(sys.argv[2])
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if request.get("preflight"):
        from transformers import Wav2Vec2CTCTokenizer
        tokenizer = Wav2Vec2CTCTokenizer.from_pretrained(request["model"], local_files_only=True)
        units = _alignment_units(request["text"])
        if not units:
            raise RuntimeError("文案只有标点或空格，请输入需要朗读的内容。")
        _acoustic_candidates([char for _, normalized in units for char in normalized], tokenizer)
        result = []
    else:
        result = align(request)
    response_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
