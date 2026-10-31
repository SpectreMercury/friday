"""Resident MLX speech shares one owned session with chat and yields memory to media rendering."""
import asyncio
import contextlib
import json
import base64
import wave
import os
import signal
import re
from pathlib import Path

from .engine import ROOT, RUNTIME
from .voice_runtime import ResidentSpeech

MAX_VOICE_SECONDS = 180


class Speech:
    def __init__(self, engine):
        self.engine = engine
        self.speaker = os.getenv('TTS_SPEAKER', 'Serena')
        self.worker = ResidentSpeech()
        self.engine.speech_worker = self.worker

    def ready(self):
        return all((ROOT / f'models/speech/{kind}/.verified.json').exists() for kind in ('stt', 'tts'))

    async def process(self, args, *, stdin=None, timeout=600):
        env = {'PATH':'/opt/homebrew/bin:/usr/bin:/bin','LANG':'en_US.UTF-8'}
        env.update(HF_HUB_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
        with (RUNTIME / 'speech.log').open('ab') as log:
            proc = await asyncio.create_subprocess_exec(*map(str, args), stdin=asyncio.subprocess.PIPE,
                stdout=log, stderr=log, env=env, cwd=ROOT, start_new_session=True)
            try:
                await asyncio.wait_for(proc.communicate(stdin), timeout)
                if proc.returncode:
                    raise RuntimeError('语音处理失败，请重试。')
            finally:
                if proc.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        await asyncio.wait_for(proc.wait(), 5)
                    except asyncio.TimeoutError:
                        with contextlib.suppress(ProcessLookupError):
                            os.killpg(proc.pid, signal.SIGKILL)
                        await proc.wait()

    async def infer(self, request):
        async with self.engine.lock:
            # Chat and speech fit in memory together. Image/video gets exclusive memory.
            if self.engine.kind not in (None,'chat'):
                await self.engine.stop()
            self.engine.claim()
            try:
                if request['mode']=='stt':
                    result=await self.worker.transcribe(request['audio'])
                    Path(request['result']).write_text(json.dumps(result,ensure_ascii=False))
                else:
                    with wave.open(str(request['audio']),'wb') as wav:
                        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(24000)
                        samples=0
                        for part in re.findall(r'.{1,100}(?:[。！？\n]|$)|.{1,100}',request['text']):
                            async with contextlib.aclosing(self.worker.events('tts',text=part,
                                    language=request.get('language','zh'),speaker=request.get('speaker'))) as events:
                                async for event in events:
                                    if event['type']=='error':raise RuntimeError(event['error'])
                                    if event['type']=='audio':
                                        if event['sample_rate']!=24000:raise RuntimeError('Unexpected speech sample rate')
                                        audio=base64.b64decode(event['pcm16']);wav.writeframes(audio);samples+=len(audio)
                        if not samples:raise RuntimeError('No speech generated')
                    Path(request['result']).write_text(json.dumps({'audio':request['audio']}))
            except BaseException:
                await self.engine.stop()
                raise

    async def transcribe(self, source, job_id):
        import wave
        wav = RUNTIME / 'incoming' / f'{job_id}.wav'
        result = wav.with_suffix('.json')
        try:
            await self.process(['/opt/homebrew/bin/ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', source,
                '-t', str(MAX_VOICE_SECONDS + 1), '-ac', '1', '-ar', '16000', wav], timeout=60)
            with wave.open(str(wav)) as audio:
                duration = audio.getnframes() / audio.getframerate()
            if duration > MAX_VOICE_SECONDS:
                raise RuntimeError('请将语音控制在 3 分钟以内。')
            await self.infer({'mode': 'stt', 'audio': str(wav), 'result': str(result)})
            text = json.loads(result.read_text())['text'].strip()
            if not text or len(text) > 6000:
                raise RuntimeError('没有听清楚，请换一段较短的语音重试。')
            return text
        finally:
            wav.unlink(missing_ok=True)
            result.unlink(missing_ok=True)

    async def synthesize(self, text, job_id, language='zh'):
        directory = RUNTIME / 'speech-output'
        directory.mkdir(exist_ok=True)
        wav = directory / f'{job_id}.wav'
        result = wav.with_suffix('.json')
        target = wav.with_suffix('.ogg')
        # Full reply is delivered as text; bound the spoken part to 600 characters.
        spoken = text if len(text) <= 600 else text[:560] + '。其余内容请查看文字回复。'
        try:
            await self.infer({'mode': 'tts', 'text': spoken, 'speaker': self.speaker if language=='zh' else None,
                              'audio': str(wav), 'result': str(result), 'language': language})
            await self.process(['/opt/homebrew/bin/ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', wav,
                '-c:a', 'libopus', '-b:a', '32k', '-ac', '1', target], timeout=60)
            return target
        finally:
            wav.unlink(missing_ok=True)
            result.unlink(missing_ok=True)
