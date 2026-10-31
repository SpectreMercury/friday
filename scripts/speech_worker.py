"""Isolated, offline MLX inference. JSON request on stdin; result in a file."""
import json
import os
import sys
from pathlib import Path

os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
ROOT = Path(__file__).resolve().parents[1]


def main():
    request = json.load(sys.stdin)
    import numpy as np
    import soundfile as sf
    if request['mode'] == 'stt':
        from mlx_audio.stt import load
        model = load(str(ROOT / 'models/speech/stt'))
        result = model.generate(request['audio'], max_tokens=2048, language=None,
                                chunk_duration=30, verbose=False)
        output = {'text': result.text.strip()}
    else:
        from mlx_audio.tts.utils import load_model
        model = load_model(str(ROOT / 'models/speech/tts'))
        # Short sentences bound decoding and avoid lengthy attention contexts.
        import re
        parts = re.findall(r'.{1,100}(?:[。！？\n]|$)|.{1,100}', request['text'])
        waves = []
        for part in parts:
            for result in model.generate_custom_voice(
                text=part, speaker=request['speaker'], language='Chinese',
                instruct='用自然、清晰的普通话交谈，语速适中。', max_tokens=1800):
                waves.append(np.asarray(result.audio).reshape(-1))
                rate = result.sample_rate
        if not waves:
            raise RuntimeError('No speech generated')
        sf.write(request['audio'], np.concatenate(waves), rate)
        output = {'audio': request['audio']}
    Path(request['result']).write_text(json.dumps(output, ensure_ascii=False))


if __name__ == '__main__':
    main()
