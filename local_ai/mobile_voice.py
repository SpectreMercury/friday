"""Session-bound mobile PCM transport backed exclusively by durable harness jobs.

Authentication belongs to the HTTP API. This hub additionally checks ownership on
every operation; a voice ID is never a bearer credential. Model residency is a
lease, while ASR/TTS acquire the ordinary inference lock only while inferring.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import inspect
import json
import tempfile
import time
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path

from aiohttp import WSMsgType, web

from .voice_audio import TurnDetector
from .voice_language import response_language
from .voice_runtime import ResidentSpeech


async def _resolve(value):
    return await value if inspect.isawaitable(value) else value


class VoiceError(RuntimeError):
    def __init__(self, code, message, status=409):
        super().__init__(message)
        self.code, self.status = code, status


@dataclass
class VoiceLease:
    id: str
    account_id: str
    session_id: str
    detector: TurnDetector
    epoch: int = 0
    closed: bool = False
    ws: object = None
    authorize: object = None
    expiry: asyncio.Task | None = None
    speaker: asyncio.Task | None = None
    cleanup: asyncio.Task | None = None
    workers: set = field(default_factory=set)
    turns: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=4))
    outgoing: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=16))
    speech: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=8))
    pending: dict = field(default_factory=dict)


class VoiceHub:
    """One host-wide live call. Disconnecting never cancels a durable task.

    ``enqueue(account_id, session_id, text, idempotency_key)`` must durably admit
    the transcript and return its job dict. ``allowed(account_id, session_id)``
    must verify both account access and session ownership. Both may be async.
    Store.get_session_job is likewise ownership-scoped and returns final result.
    """

    def __init__(self, engine, store, enqueue, allowed, *, speech=None,
                 detector_factory=TurnDetector, connect_timeout=30,
                 poll_interval=.25, acknowledge_after=3):
        self.engine, self.store = engine, store
        self.enqueue, self.allowed = enqueue, allowed
        self.worker = speech or engine.speech_worker or ResidentSpeech()
        self.engine.speech_worker = self.worker
        self.detector_factory = detector_factory
        self.connect_timeout = connect_timeout
        self.poll_interval, self.acknowledge_after = poll_interval, acknowledge_after
        self._lease = None
        self._guard = asyncio.Lock()

    @property
    def active(self):
        return self._lease is not None and not self._lease.closed

    async def _authorize(self, account_id, session_id):
        if not await _resolve(self.allowed(account_id, session_id)):
            raise VoiceError('not_found', '会话不存在或无权访问。', 404)

    def _check_credentials(self, lease):
        """Recheck the live device immediately before each sensitive action.

        The HTTP layer supplies a synchronous lookup so there is no event-loop
        yield between checking revocation and a synchronous admission/send.
        Authentication failures are terminal, never recoverable ASR errors.
        """
        if lease.closed:
            raise PermissionError('语音通话已结束。')
        if lease.authorize is not None:
            lease.authorize()

    def _owned(self, account_id, voice_id):
        lease = self._lease
        if not lease or lease.closed or lease.id != voice_id or lease.account_id != str(account_id):
            raise VoiceError('not_found', '语音通话不存在。', 404)
        return lease

    def _description(self, lease):
        return {'id': lease.id, 'session_id': lease.session_id, 'epoch': lease.epoch,
                'sample_rate': 16000, 'stream_path': f'/v1/voice/{lease.id}/stream',
                'input_format': {'encoding': 'pcm_s16le', 'channels': 1,
                                 'sample_rate': 16000, 'max_chunk_bytes': 32000}}

    async def create(self, account_id, session_id):
        if self.store.evolution.draining():
            raise VoiceError("upgrade_pending", "服务正在准备升级，请稍后开启语音。", 503)
        await self._authorize(account_id, session_id)
        async with self._guard:
            if self._lease:
                raise VoiceError('voice_busy', '本机已有实时语音通话；你仍可发送文字或语音留言。')
            lease = VoiceLease(uuid.uuid4().hex, str(account_id), str(session_id), self.detector_factory())
            self._lease = lease
        try:
            async with asyncio.timeout(125):
                await self.engine.reserve_voice(lease.id)
                async with self.engine.lock:
                    await self.worker.start()
            if lease.closed:
                raise VoiceError('voice_closed', '语音通话已结束。')
            lease.expiry = asyncio.create_task(self._expire(lease))
            return self._description(lease)
        except BaseException:
            await self._end(lease)
            raise

    async def _expire(self, lease):
        await asyncio.sleep(self.connect_timeout)
        if lease.ws is None:
            await self._end(lease)

    async def end(self, account_id, voice_id):
        lease = self._owned(account_id, voice_id)
        await self._end(lease)
        return True

    async def close(self):
        if self._lease:
            await self._end(self._lease)

    async def _end(self, lease):
        if lease.cleanup is None:
            lease.closed = True
            lease.cleanup = asyncio.create_task(self._cleanup(lease, asyncio.current_task()))
        # HTTP disconnect/cancellation must not abort ownership release. Every
        # subsequent end/close waits for that same cleanup before reusing a slot.
        await asyncio.shield(lease.cleanup)

    async def _cleanup(self, lease, source):
        lease.closed = True
        lease.epoch += 1
        tasks = set(lease.workers)
        if lease.expiry:
            tasks.add(lease.expiry)
        if lease.speaker:
            tasks.add(lease.speaker)
        for task in tasks - {source}:
            task.cancel()
        # Owner and operation guards prevent one call from cancelling another
        # channel's work. Cancelling transport workers never reaches enqueue'd jobs.
        with contextlib.suppress(Exception):
            await self.worker.interrupt(owner=lease.id, op='tts')
        try:
            await asyncio.gather(*(tasks - {source}), return_exceptions=True)
            if lease.ws and not lease.ws.closed:
                with contextlib.suppress(Exception):
                    async with asyncio.timeout(5):
                        await lease.ws.close()
        finally:
            lease.detector.flush()
            await self.engine.release_voice(lease.id)
            async with self._guard:
                if self._lease is lease:
                    self._lease = None

    async def _emit(self, lease, event, *, epoch=None, audio=None):
        if lease.closed:
            return
        self._check_credentials(lease)
        event = {**event, 'session_id': lease.session_id,
                 'epoch': lease.epoch if epoch is None else epoch}
        try:
            await asyncio.wait_for(lease.outgoing.put((epoch, event, audio)), 2)
        except asyncio.TimeoutError:
            raise VoiceError('slow_client', '音频连接过慢，请重新开始通话。', 503) from None

    async def interrupt(self, lease):
        self._check_credentials(lease)
        lease.epoch += 1
        # Invalidate queued audio before enqueuing the clear marker. Durable task
        # results and transcripts remain visible in their original session.
        retained = []
        while not lease.outgoing.empty():
            item = lease.outgoing.get_nowait()
            if item[2] is None:
                retained.append(item)
        for item in retained:
            lease.outgoing.put_nowait(item)
        await self._emit(lease, {'type': 'clear_playback'})
        self._check_credentials(lease)
        if lease.speaker and not lease.speaker.done():
            lease.speaker.cancel()
        await self.worker.interrupt(owner=lease.id, op='tts')

    async def feed(self, lease, pcm):
        if lease.closed:
            return
        self._check_credentials(lease)
        if not isinstance(pcm, bytes) or not pcm or len(pcm) > 32000 or len(pcm) % 2:
            raise VoiceError('invalid_audio', '音频必须为不超过一秒的 16 kHz 单声道 PCM16。', 400)
        for event in lease.detector.feed(pcm):
            self._check_credentials(lease)
            if event['type'] == 'speech_start':
                await self.interrupt(lease)
            elif event['type'] == 'utterance':
                try:
                    lease.turns.put_nowait((lease.epoch, event['pcm16']))
                except asyncio.QueueFull:
                    raise VoiceError('audio_backpressure', '处理尚未跟上语音，请稍后重试。', 503) from None

    async def _sender(self, lease):
        while not lease.closed:
            epoch, event, audio = await lease.outgoing.get()
            self._check_credentials(lease)
            if epoch is not None and epoch != lease.epoch:
                continue
            async with asyncio.timeout(5):
                await lease.ws.send_json(event)
                if audio is not None and epoch == lease.epoch:
                    # send_json may yield while the device is revoked. The
                    # accompanying PCM must not inherit its old authorization.
                    self._check_credentials(lease)
                    await lease.ws.send_bytes(audio)

    async def _transcriber(self, lease):
        while not lease.closed:
            epoch, pcm = await lease.turns.get()
            try:
                self._check_credentials(lease)
                with tempfile.TemporaryDirectory(prefix='harness-voice-') as directory:
                    path = Path(directory) / 'utterance.wav'
                    with wave.open(str(path), 'wb') as wav:
                        wav.setnchannels(1)
                        wav.setsampwidth(2)
                        wav.setframerate(16000)
                        wav.writeframes(pcm)
                    async with self.engine.lock:
                        self._check_credentials(lease)
                        transcript = await self.worker.transcribe(path, owner=lease.id)
                if lease.closed:
                    return
                self._check_credentials(lease)
                text = str(transcript.get('text', '')).strip()
                if not text:
                    continue
                if len(text) > 6000:
                    raise VoiceError('transcript_too_long', '请缩短这段语音。', 400)
                await self._authorize(lease.account_id, lease.session_id)
                self._check_credentials(lease)
                language = response_language(transcript)['language'] or 'zh'
                await self._emit(lease, {'type': 'recognized', 'text': text, 'language': language}, epoch=None)
                # Queue backpressure in _emit can yield. Check again at the
                # actual admission boundary, not just when ASR finishes.
                self._check_credentials(lease)
                if len(lease.pending) >= 32:
                    raise VoiceError('too_many_tasks', '当前等待结果的任务较多，请稍后继续。')
                job = await _resolve(self.enqueue(lease.account_id, lease.session_id, text,
                                                f'voice:{lease.id}:{uuid.uuid4().hex}'))
                self._check_credentials(lease)
                lease.pending[job['id']] = {'epoch': epoch, 'language': language,
                                            'started': time.monotonic(), 'acknowledged': False}
                await self._emit(lease, {'type': 'task', 'job_id': job['id']})
                await self._emit(lease, {'type': 'state', 'state': 'thinking', 'job_id': job['id']})
            except asyncio.CancelledError:
                raise
            except PermissionError:
                raise
            except Exception as exc:
                await self._emit(lease, {'type': 'error', 'code': getattr(exc, 'code', 'transcription_failed'),
                                         'message': str(exc) if isinstance(exc, VoiceError) else '语音处理失败，请重试。'})

    async def _results(self, lease):
        while not lease.closed:
            self._check_credentials(lease)
            for job_id, pending in list(lease.pending.items()):
                self._check_credentials(lease)
                job = await _resolve(self.store.get_session_job(lease.account_id, lease.session_id, job_id))
                self._check_credentials(lease)
                if not job:
                    del lease.pending[job_id]
                    continue
                status = job['status']
                if status in ('succeeded', 'failed', 'cancelled'):
                    del lease.pending[job_id]
                    # Only the durable, terminal chat result is spoken. Never use
                    # model tokens, interim plans, tool output, or generated paths.
                    text = str(job.get('result') or '') if job.get('kind', 'chat') == 'chat' else ''
                    await self._emit(lease, {'type': 'result', 'job_id': job_id, 'status': status, 'text': text})
                    if status == 'succeeded' and text and pending['epoch'] == lease.epoch:
                        spoken = text if len(text) <= 600 else text[:560] + '。其余内容请查看文字回复。'
                        try:
                            lease.speech.put_nowait((pending['epoch'], spoken, pending['language']))
                        except asyncio.QueueFull:
                            await self._emit(lease, {'type': 'state', 'state': 'listening',
                                                     'message': '结果已保存在当前会话，请查看文字。'})
                elif not pending['acknowledged'] and time.monotonic() - pending['started'] >= self.acknowledge_after:
                    pending['acknowledged'] = True
                    await self._emit(lease, {'type': 'state', 'state': status, 'job_id': job_id,
                                             'message': '任务已接收，可以继续说话或结束通话。结果会保存在当前会话。'})
            await asyncio.sleep(self.poll_interval)

    async def _speak(self, lease, epoch, text, language):
        self._check_credentials(lease)
        await self._emit(lease, {'type': 'state', 'state': 'speaking'}, epoch=epoch)
        async with self.engine.lock:
            self._check_credentials(lease)
            if lease.closed or epoch != lease.epoch:
                return
            started, duration = time.monotonic(), 0
            async with contextlib.aclosing(self.worker.events('tts', owner=lease.id, text=text, language=language)) as events:
                async for event in events:
                    self._check_credentials(lease)
                    if lease.closed or epoch != lease.epoch:
                        return
                    if event['type'] == 'error':
                        raise RuntimeError('Speech synthesis failed')
                    if event['type'] != 'audio':
                        continue
                    rate = event['sample_rate']
                    if rate not in (16000, 22050, 24000, 44100, 48000):
                        raise ValueError('Unexpected sample rate')
                    pcm = base64.b64decode(event['pcm16'], validate=True)
                    if len(pcm) % 2 or len(pcm) > rate * 4:
                        raise ValueError('Invalid speech output chunk')
                    chunk_size = rate * 2 // 5  # 200 ms; pace to bound client buffering.
                    for offset in range(0, len(pcm), chunk_size):
                        part = pcm[offset:offset + chunk_size]
                        delay = started + duration - .3 - time.monotonic()
                        if delay > 0:
                            await asyncio.sleep(delay)
                        if lease.closed or epoch != lease.epoch:
                            return
                        await self._emit(lease, {'type': 'audio_format', 'sample_rate': rate,
                                                 'channels': 1, 'encoding': 'pcm_s16le'}, epoch=epoch, audio=part)
                        duration += len(part) / (rate * 2)
        await self._emit(lease, {'type': 'state', 'state': 'listening'}, epoch=epoch)

    async def _speaker(self, lease):
        while not lease.closed:
            epoch, text, language = await lease.speech.get()
            self._check_credentials(lease)
            if epoch != lease.epoch:
                continue
            lease.speaker = asyncio.create_task(self._speak(lease, epoch, text, language))
            try:
                await lease.speaker
            except asyncio.CancelledError:
                if lease.closed or asyncio.current_task().cancelling():
                    raise
            except PermissionError:
                raise
            except Exception:
                await self._emit(lease, {'type': 'error', 'code': 'synthesis_failed',
                                         'message': '语音播报失败，文字结果已保存在当前会话。'})
            finally:
                lease.speaker = None

    async def _run_worker(self, lease, operation):
        try:
            await operation(lease)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A stalled sender or failed result watcher must not leave a silent,
            # permanently reserved call. Its durable jobs continue independently.
            await self._end(lease)

    async def stream(self, account_id, voice_id, request):
        lease = self._owned(account_id, voice_id)
        await self._authorize(account_id, lease.session_id)
        lease = self._owned(account_id, voice_id)
        if lease.ws is not None:
            raise VoiceError('already_connected', '此语音通话已有连接。')
        ws = web.WebSocketResponse(max_msg_size=32768, heartbeat=20, receive_timeout=90)
        lease.ws = ws  # Claim before prepare() yields; never attach two sockets.
        lease.authorize = request.get('voice_authorize')
        try:
            self._check_credentials(lease)
            await ws.prepare(request)
            if lease.closed:
                await ws.close()
                return ws
            if lease.expiry:
                lease.expiry.cancel()
            self._check_credentials(lease)
            await ws.send_json({'type': 'ready', **self._description(lease)})
            for operation in (self._sender, self._transcriber, self._results, self._speaker):
                task = asyncio.create_task(self._run_worker(lease, operation))
                lease.workers.add(task)
            async for msg in ws:
                if lease.closed:
                    break
                self._check_credentials(lease)
                if msg.type == WSMsgType.BINARY:
                    await self.feed(lease, msg.data)
                elif msg.type == WSMsgType.TEXT:
                    if len(msg.data) > 2048:
                        raise VoiceError('invalid_control', '语音控制消息过长。', 400)
                    try:
                        control = json.loads(msg.data)
                    except ValueError:
                        raise VoiceError('invalid_control', '语音控制消息格式错误。', 400) from None
                    if not isinstance(control, dict):
                        raise VoiceError('invalid_control', '语音控制消息格式错误。', 400)
                    if control.get('type') == 'end':
                        break
                    if control.get('type') == 'interrupt':
                        if control.get('epoch', lease.epoch) == lease.epoch:
                            await self.interrupt(lease)
                    else:
                        raise VoiceError('invalid_control', '未知语音控制消息。', 400)
                elif msg.type == WSMsgType.ERROR:
                    break
        except PermissionError:
            if not ws.prepared:
                raise
            # The finally block closes transport and releases the live slot.
            # Do not emit any more application events to revoked credentials.
        except VoiceError as exc:
            if ws.prepared and not ws.closed:
                with contextlib.suppress(Exception):
                    self._check_credentials(lease)
                    await ws.send_json({'type': 'error', 'code': exc.code, 'message': str(exc),
                                        'session_id': lease.session_id, 'epoch': lease.epoch})
        finally:
            await self._end(lease)
        return ws
