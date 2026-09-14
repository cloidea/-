from __future__ import annotations

import json
import sys
from pathlib import Path

import torch
import torchaudio
from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor


def main() -> None:
    root = Path(sys.argv[1]).resolve()
    model_path = root / "models" / "alignment" / "model"
    processor = Wav2Vec2Processor.from_pretrained(model_path, local_files_only=True)
    model = Wav2Vec2ForCTC.from_pretrained(model_path, local_files_only=True).cuda().eval()
    results = {}
    for value in sys.argv[2:]:
        audio_path = Path(value).resolve()
        waveform, sample_rate = torchaudio.load(audio_path)
        waveform = waveform.mean(dim=0)
        if sample_rate != 16000:
            waveform = torchaudio.functional.resample(waveform, sample_rate, 16000)
        inputs = processor(waveform.numpy(), sampling_rate=16000, return_tensors="pt")
        with torch.inference_mode():
            logits = model(inputs.input_values.cuda()).logits
        results[str(audio_path)] = processor.batch_decode(logits.argmax(dim=-1))[0]
    print(json.dumps(results, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
