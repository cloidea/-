from __future__ import annotations

import json
import sys
from pathlib import Path

from funasr import AutoModel


def main() -> None:
    package_root = Path(sys.argv[1]).resolve()
    audio_path = Path(sys.argv[2]).resolve()
    models = package_root / "tools" / "asr" / "models"
    model = AutoModel(
        model=str(models / "speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"),
        vad_model=str(models / "speech_fsmn_vad_zh-cn-16k-common-pytorch"),
        punc_model=str(models / "punc_ct-transformer_zh-cn-common-vocab272727-pytorch"),
        disable_update=True,
    )
    result = model.generate(input=str(audio_path))[0]
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
