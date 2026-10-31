import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from local_ai.voice_audio import TurnDetector,PlaybackBuffer
from local_ai.voice_runtime import LocalVoiceSession,ResidentSpeech


class FakeVad:
    def is_speech(self,frame,rate):return frame[0]!=0


def test_turn_detection_fragmentation_endpoint_and_forced_split():
    detector=TurnDetector(vad=FakeVad(),silence_ms=100,max_seconds=1)
    voiced=b'\x01\x00'*320;quiet=b'\0'*640
    assert detector.feed(voiced[:211])==[]
    assert detector.feed(voiced[211:])==[]
    events=detector.feed(voiced*2)
    assert events==[{'type':'speech_start'}]
    events=detector.feed(quiet*5)
    assert events[0]['type']=='utterance' and not events[0]['forced_split']
    assert events[0]['pcm16']==voiced*3+quiet*5
    assert detector.feed(quiet*5)==[]
    detector.feed(voiced*3)
    events=[]
    for _ in range(50):events.extend(detector.feed(voiced))
    assert any(e.get('forced_split') for e in events)


def test_barge_in_discards_buffered_and_late_packets():
    playback=PlaybackBuffer();old=playback.epoch
    assert playback.append(old,b'\x01\x00'*2400,24000)
    assert len(playback.read_frame())==960
    new=playback.clear()
    assert playback.read_frame()==b''
    assert not playback.append(old,b'\x01\x00'*2400,24000)
    assert playback.append(new,b'\x02\x00'*240,24000)
    assert playback.finish()==b'\x02\x00'*240


@pytest.mark.asyncio
async def test_voice_session_keeps_chat_loaded_and_always_releases_lock(monkeypatch):
    from local_ai.engine import Engine
    engine=Engine();engine.start=AsyncMock();engine.stop=AsyncMock()
    speech=SimpleNamespace(start=AsyncMock(),close=AsyncMock())
    session=LocalVoiceSession(engine);session.speech=speech
    with pytest.raises(RuntimeError):
        async with session as active:
            assert not engine.lock.locked() and engine.voice_reserved and active is speech
            engine.start.assert_awaited_once_with('chat')
            engine.stop.assert_not_awaited()
            raise RuntimeError('transport disconnected')
    assert not engine.lock.locked()
    assert not engine.voice_reserved
    engine.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_warmup_releases_resources():
    from local_ai.engine import Engine
    engine=Engine();engine.start=AsyncMock();engine.stop=AsyncMock()
    session=LocalVoiceSession(engine)
    session.speech=SimpleNamespace(start=AsyncMock(side_effect=RuntimeError('load failed')),close=AsyncMock())
    with pytest.raises(RuntimeError):await session.__aenter__()
    assert not engine.lock.locked()
    assert not engine.voice_reserved
    engine.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_interrupt_immediately_invalidates_audio_and_signals_request():
    speech=ResidentSpeech();speech.active_id='a';speech.proc=SimpleNamespace(returncode=None)
    speech.write=AsyncMock()
    await speech.interrupt()
    assert speech.discard_audio
    speech.write.assert_awaited_once_with({'id':'a','op':'cancel'})


@pytest.mark.parametrize('transcript,expected,basis',[
    ({'text':'How many guests?','language':['English']},'en','asr_language'),
    ({'text':'何名様ですか。','language':['Japanese']},'ja','asr_language'),
    ({'text':'请问几位？','language':['Chinese']},'zh','asr_language'),
    ({'text':'請問幾多位？','language':['Chinese']},'yue','cantonese_text_hint'),
    ({'text':'你好','language':['Cantonese']},'yue','asr_language'),
    ({'text':'hello','language':['Thai']},None,'unsupported_or_unknown'),
])
def test_language_selection_follows_transcript(transcript,expected,basis):
    from local_ai.voice_language import response_language
    result=response_language(transcript)
    assert result['language']==expected and result['basis']==basis


@pytest.mark.asyncio
async def test_complete_pcm_turn_reaches_speech_and_barge_in_clears_it():
    import base64
    from local_ai.voice_loop import LocalVoiceLoop
    async def events(*_,**kwargs):
        assert kwargs['language']=='en'
        yield {'type':'audio','pcm16':base64.b64encode(b'\x01\x00'*2400).decode(),'sample_rate':24000}
        yield {'type':'done'}
    speech=SimpleNamespace(transcribe=AsyncMock(return_value={'text':'How many guests?','language':['English']}),
                           events=events,interrupt=AsyncMock())
    reply=AsyncMock(return_value='A table for two, please.')
    notifications=AsyncMock()
    loop=LocalVoiceLoop(speech,reply,notifications,TurnDetector(vad=FakeVad(),silence_ms=100))
    await loop.feed(b'\x01\x00'*320*3+b'\0'*640*5)
    await loop.task
    reply.assert_awaited_once()
    assert loop.playback.read_frame()
    await loop.feed(b'\x01\x00'*320*3)
    assert loop.playback.read_frame()==b''
    await loop.close()


@pytest.mark.asyncio
async def test_worker_protocol_cancellation_drains_before_next_turn(tmp_path,monkeypatch):
    import json,sys
    from pathlib import Path
    import local_ai.voice_runtime as module
    (tmp_path/'vendor/speech-venv/bin').mkdir(parents=True)
    (tmp_path/'vendor/speech-venv/bin/python').symlink_to(sys.executable)
    (tmp_path/'scripts').mkdir()
    (tmp_path/'scripts/resident_speech_worker.py').write_text('''
import sys,json
print(json.dumps({'type':'ready'}),flush=True)
for line in sys.stdin:
 r=json.loads(line)
 if r['op']=='tts':print(json.dumps({'id':r['id'],'type':'audio','pcm16':'AAA=','sample_rate':24000}),flush=True)
 elif r['op']=='cancel':print(json.dumps({'id':r['id'],'type':'done','cancelled':True}),flush=True)
 elif r['op']=='asr':
  print(json.dumps({'id':r['id'],'type':'transcript','text':'hello','language':['English']}),flush=True)
  print(json.dumps({'id':r['id'],'type':'done'}),flush=True)
''')
    monkeypatch.setattr(module,'ROOT',tmp_path)
    speech=ResidentSpeech(tmp_path)
    try:
        events=speech.events('tts',text='hello',language='en')
        assert (await anext(events))['type']=='audio'
        pid=speech.proc.pid
        await events.aclose()
        assert not speech.lock.locked()
        assert (await speech.transcribe('fixture.wav'))['text']=='hello'
        assert speech.proc.pid==pid
    finally:await speech.close()
    assert speech.proc is None


@pytest.mark.asyncio
async def test_engine_preserves_speech_and_gpu_lock_only_for_chat(tmp_path,monkeypatch):
    import local_ai.engine as module
    monkeypatch.setattr(module,'RUNTIME',tmp_path)
    engine=module.Engine();engine.speech_worker=SimpleNamespace(close=AsyncMock())
    engine.claim();lock=engine.process_lock
    await engine.stop(keep_speech=True)
    assert engine.process_lock is lock and not lock.closed
    engine.speech_worker.close.assert_not_awaited()
    competitor=module.Engine()
    with pytest.raises(RuntimeError):competitor.claim()
    await engine.stop()
    assert lock.closed and engine.process_lock is None
    engine.speech_worker.close.assert_awaited_once()
    competitor.claim();await competitor.stop()
