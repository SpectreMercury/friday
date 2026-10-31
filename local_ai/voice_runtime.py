"""Resident local speech for a dedicated call session; no public listener or cloud API."""
import asyncio
import contextlib
import json
import os
import signal
import uuid
from .engine import ROOT, RUNTIME


class ResidentSpeech:
    def __init__(self, runtime=RUNTIME):
        self.runtime = runtime
        self.proc = None
        self.lock = asyncio.Lock()
        self.active_id = None
        self.active_owner = None
        self.active_op = None
        self.log = None
        self.discard_audio = False

    async def start(self):
        if self.proc and self.proc.returncode is None:
            return
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.log = (self.runtime/'resident-speech.log').open('ab')
        # Allowlist: no Telegram, telephony or user account credentials inherited.
        env = {'PATH':'/opt/homebrew/bin:/usr/bin:/bin', 'LANG':'en_US.UTF-8',
               'HF_HUB_OFFLINE':'1','HF_HUB_DISABLE_TELEMETRY':'1','PYTHONUNBUFFERED':'1'}
        self.proc = await asyncio.create_subprocess_exec(str(ROOT/'vendor/speech-venv/bin/python'),
            str(ROOT/'scripts/resident_speech_worker.py'), env=env, cwd=ROOT,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=self.log,
            limit=2**20, start_new_session=True)
        try:
            ready = json.loads(await asyncio.wait_for(self.proc.stdout.readline(),120))
            if ready.get('type') != 'ready': raise RuntimeError('Speech worker not ready')
            self.ready = ready
        except BaseException:
            await self.close()
            raise

    async def write(self, payload):
        self.proc.stdin.write((json.dumps(payload)+'\n').encode())
        await self.proc.stdin.drain()

    async def interrupt(self, *, owner=None, op=None):
        # The transport can clear its playback buffer immediately, without waiting for MLX.
        # A mobile call must never interrupt Telegram's speech work or another call's ASR.
        if owner is not None and owner != self.active_owner:
            return False
        if op is not None and op != self.active_op:
            return False
        self.discard_audio = True
        if self.active_id and self.proc and self.proc.returncode is None:
            await self.write({'id':self.active_id,'op':'cancel'})
        return True

    async def events(self, op, *, owner=None, **args):
        async with self.lock:
            await self.start()
            rid = uuid.uuid4().hex
            self.active_id = rid
            self.active_owner, self.active_op = owner, op
            self.discard_audio = False
            done = False
            try:
                await self.write({'id':rid,'op':op,**args})
                async with asyncio.timeout(120):
                    while True:
                        line = await self.proc.stdout.readline()
                        if not line: raise RuntimeError('Speech worker exited')
                        event = json.loads(line)
                        if event.get('id') != rid: raise RuntimeError('Speech response ID mismatch')
                        if event['type'] == 'done': done = True
                        if event['type'] != 'audio' or not self.discard_audio:
                            yield event
                        if done: break
            finally:
                if not done and self.proc and self.proc.returncode is None:
                    await self.interrupt()
                    try:
                        async with asyncio.timeout(15):
                            while line := await self.proc.stdout.readline():
                                event=json.loads(line)
                                if event.get('id') == rid and event.get('type') == 'done':
                                    done=True;break
                    except (TimeoutError, ValueError):
                        pass
                    if not done: await self.close()
                self.active_id = None
                self.active_owner = self.active_op = None

    async def transcribe(self, path, *, owner=None):
        result = None
        async with contextlib.aclosing(self.events('asr', owner=owner, audio=str(path))) as events:
            async for event in events:
                if event['type']=='error': raise RuntimeError(event['error'])
                if event['type']=='transcript': result=event
        if result is None: raise RuntimeError('No transcript')
        return result

    async def close(self):
        proc, self.proc = self.proc, None
        if proc and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError): os.killpg(proc.pid,signal.SIGTERM)
            try: await asyncio.wait_for(proc.wait(),5)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError): os.killpg(proc.pid,signal.SIGKILL)
                await proc.wait()
        if self.log:
            self.log.close();self.log=None


class LocalVoiceSession:
    """Reserve resident models without holding the per-inference lock during a call."""
    def __init__(self, engine, runtime=RUNTIME):
        self.engine=engine
        self.owner = 'local-' + uuid.uuid4().hex
        self.owns_runtime = engine.speech_worker is None
        self.speech=engine.speech_worker or ResidentSpeech(runtime)
        engine.speech_worker = self.speech

    async def __aenter__(self):
        await self.engine.reserve_voice(self.owner)
        try:
            async with self.engine.lock:
                await self.speech.start()
            return self.speech
        except BaseException:
            await self.engine.release_voice(self.owner)
            if self.owns_runtime:
                async with self.engine.lock:
                    await self.engine.stop()
            raise

    async def __aexit__(self, *_):
        await self.engine.release_voice(self.owner)
        if self.owns_runtime:
            async with self.engine.lock:
                await self.engine.stop()
