"""Local PCM conversation loop. Transport and reservation decisions are injected."""
import asyncio
import base64
import contextlib
import tempfile
import wave
from pathlib import Path
from .voice_audio import TurnDetector,PlaybackBuffer
from .voice_language import response_language


class LocalVoiceLoop:
    def __init__(self,speech,reply,on_event=None,detector=None):
        self.speech,self.reply,self.on_event=speech,reply,on_event
        self.detector=detector or TurnDetector()
        self.playback=PlaybackBuffer()
        self.task=None

    async def notify(self,event):
        if self.on_event:await self.on_event(event)

    async def interrupt(self):
        self.playback.clear()
        await self.notify({'type':'clear_playback'})
        await self.speech.interrupt()
        if self.task and not self.task.done():
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):await self.task
        self.task=None

    async def feed(self,pcm16):
        for event in self.detector.feed(pcm16):
            if event['type']=='speech_start':
                await self.interrupt()
            elif event['type']=='utterance':
                # No overlapping inference or stale answer after a subsequent caller turn.
                await self.interrupt()
                self.task=asyncio.create_task(self.respond(event['pcm16']))

    async def respond(self,pcm16):
        epoch=self.playback.epoch
        try:
            with tempfile.TemporaryDirectory(prefix='local-voice-turn-') as directory:
                path=Path(directory)/'caller.wav'
                with wave.open(str(path),'wb') as wav:
                    wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(pcm16)
                transcript=await self.speech.transcribe(path)
            if not transcript.get('text','').strip():return
            language=response_language(transcript)
            await self.notify({'type':'recognized','transcript':transcript,'selection':language})
            if language['language'] is None:
                await self.notify({'type':'unsupported_language'})
                return
            text=await self.reply(transcript,language)
            if not isinstance(text,str) or not text.strip() or len(text)>600:
                raise ValueError('Voice reply must contain 1–600 characters')
            async with contextlib.aclosing(self.speech.events('tts',text=text,language=language['language'])) as events:
                async for event in events:
                    if event['type']=='error':raise RuntimeError(event['error'])
                    if event['type']=='audio':
                        self.playback.append(epoch,base64.b64decode(event['pcm16']),event['sample_rate'])
            await self.notify({'type':'reply_ready','language':language['language'],'text':text})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.notify({'type':'voice_error','error':type(exc).__name__})

    async def close(self):
        await self.interrupt()
        self.detector.flush()
