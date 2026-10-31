import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

import local_ai.speech as module


@pytest.mark.asyncio
async def test_cancel_reaps_speech_process(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'RUNTIME', tmp_path)
    speech = module.Speech(SimpleNamespace())
    marker = tmp_path / 'pid'
    task = asyncio.create_task(speech.process([sys.executable, '-c',
        'import os,time,pathlib;pathlib.Path(' + repr(str(marker)) + ').write_text(str(os.getpid()));time.sleep(60)']))
    try:
        for _ in range(100):
            if marker.exists():
                break
            await asyncio.sleep(.02)
        assert marker.exists()
        pid = int(marker.read_text())
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.asyncio
async def test_resident_synthesis_splits_long_text_without_unloading_chat(tmp_path):
    import base64,wave
    from unittest.mock import AsyncMock
    engine=SimpleNamespace(lock=asyncio.Lock(),kind='chat',claim=lambda:None,stop=AsyncMock())
    speech=module.Speech(engine)
    parts=[]
    async def events(op,**args):
        parts.append(args['text'])
        yield {'type':'audio','sample_rate':24000,'pcm16':base64.b64encode(b'\0'*480).decode()}
        yield {'type':'done'}
    speech.worker=SimpleNamespace(events=events)
    text='这是用于检查长段落分段朗读的文字。'*30
    await speech.infer({'mode':'tts','text':text,'speaker':'Serena','audio':str(tmp_path/'out.wav'),'result':str(tmp_path/'out.json')})
    assert ''.join(parts)==text and all(len(part)<=101 for part in parts)
    engine.stop.assert_not_awaited()
    with wave.open(str(tmp_path/'out.wav')) as wav:assert wav.getnframes()==len(parts)*240
