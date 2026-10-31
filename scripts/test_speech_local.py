"""Offline speech integration check; stop the Telegram service before running."""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_ai.engine import Engine, ROOT, RUNTIME
from local_ai.speech import Speech


async def main():
    engine = Engine()
    speech = Speech(engine)
    (RUNTIME / 'incoming').mkdir(exist_ok=True)
    results = {}
    try:
        started = time.monotonic()
        text = await speech.transcribe(RUNTIME / 'speech-test/official-zh.wav', 'official-zh')
        results['official_stt'] = {'text': text, 'seconds': time.monotonic() - started}
        print(json.dumps(results, ensure_ascii=False), flush=True)
        assert '交易' in text and '停滞' in text
        expected = '你好，我是你的本地助手。现在可以用中文语音和我聊天了。'
        started = time.monotonic()
        path = await speech.synthesize(expected, 'serena-demo')
        results['tts'] = {'text': expected, 'path': str(path), 'seconds': time.monotonic() - started}
        print(json.dumps(results['tts'], ensure_ascii=False), flush=True)
        started = time.monotonic()
        text = await speech.transcribe(path, 'roundtrip')
        results['roundtrip'] = {'text': text, 'seconds': time.monotonic() - started}
        assert '本地助手' in text and '中文语音' in text
        print(json.dumps(results['roundtrip'], ensure_ascii=False), flush=True)
    finally:
        await engine.stop()
        (RUNTIME / 'speech-test/report.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))


asyncio.run(main())
