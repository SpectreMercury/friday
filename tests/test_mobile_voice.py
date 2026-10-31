"""Exercise the live protocol without microphones, network services, or a GPU."""
import asyncio
import base64
import contextlib
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer

from local_ai.engine import Engine
from local_ai.mobile_voice import VoiceError, VoiceHub
from local_ai.mobile_auth import MobileAuth
from local_ai.store import Store
from local_ai.voice_audio import TurnDetector
from local_ai.voice_runtime import ResidentSpeech


class Vad:
    def is_speech(self, frame, rate):
        return frame[0] != 0


class Worker:
    def __init__(self):
        self.start = AsyncMock()
        self.interrupt = AsyncMock()
        self.transcribe = AsyncMock(return_value={'text': '你好', 'language': ['Chinese']})
        self.tts = []

    async def events(self, op, **args):
        self.tts.append((op, args))
        yield {'type': 'audio', 'pcm16': base64.b64encode(b'\x01\x00' * 240).decode(), 'sample_rate': 24000}
        yield {'type': 'done'}


class Jobs:
    def __init__(self):
        self.evolution = SimpleNamespace(draining=lambda: False)
        self.jobs = {}
        self.admissions = []

    def enqueue(self, account, session, text, key):
        jid = str(len(self.jobs) + 1)
        job = {'id': jid, 'account_id': account, 'session_id': session, 'kind': 'chat',
               'status': 'queued', 'result': None}
        self.jobs[jid] = job
        self.admissions.append((account, session, text, key))
        return job

    def get_session_job(self, account, session, jid):
        job = self.jobs[jid]
        assert job['account_id'] == account and job['session_id'] == session
        return job


def fixture_hub(**kwargs):
    engine = Engine()
    engine.start = AsyncMock()
    speech, store = Worker(), Jobs()
    hub = VoiceHub(engine, store, store.enqueue,
                   lambda account, session: str(account) == 'owner' and session in ('a', 'b'),
                   speech=speech, detector_factory=lambda: TurnDetector(vad=Vad(), silence_ms=100),
                   poll_interval=.005, acknowledge_after=.01, **kwargs)
    return hub, engine, speech, store


@contextlib.asynccontextmanager
async def connected(hub, description, authorize=None):
    async def route(request):
        try:
            if authorize is not None:
                request['voice_authorize'] = authorize
            return await hub.stream(request.headers.get('Authorization'), request.match_info['id'], request)
        except VoiceError as exc:
            return web.json_response({'code': exc.code}, status=exc.status)
    app = web.Application()
    app.router.add_get('/v1/voice/{id}/stream', route)
    async with TestClient(TestServer(app)) as client:
        socket = await client.ws_connect(description['stream_path'], headers={'Authorization': 'owner'})
        assert (await socket.receive_json())['type'] == 'ready'
        try:
            yield socket, client
        finally:
            await socket.close()
            await hub.close()


async def receive_type(ws, kind):
    async with asyncio.timeout(2):
        while True:
            message = await ws.receive()
            if message.type == WSMsgType.TEXT:
                import json
                event = json.loads(message.data)
                if event['type'] == kind:
                    return event


PCM = b'\x01\x00' * 320 * 3 + b'\0' * 640 * 5


@pytest.fixture
def revocable_device(tmp_path):
    """An external SQLite connection mimics mobile-admin/CLI revocation."""
    store = Store(tmp_path / 'auth.db')
    account = store.create_account('Voice owner')
    auth = MobileAuth(store)
    device = auth.pair(auth.invite(account['id']), 'Browser')
    external = sqlite3.connect(store.path)
    cli_auth = MobileAuth(SimpleNamespace(db=external))
    try:
        yield SimpleNamespace(
            check=lambda: auth.authenticate(device['token']),
            revoke=lambda: cli_auth.revoke(account['id'], device['device_id']),
        )
    finally:
        external.close()
        store.db.close()


async def receive_closed(ws):
    """Return data received before close, without ever spinning on closed WS."""
    received = []
    async with asyncio.timeout(2):
        while True:
            event = await ws.receive()
            if event.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                return received
            received.append(event)


async def test_single_slot_ownership_and_warmup_do_not_hold_inference_lock():
    hub, engine, speech, store = fixture_hub()
    desc = await hub.create('owner', 'a')
    try:
        assert desc['sample_rate'] == 16000
        assert engine.voice_reserved and not engine.lock.locked()
        # A harness inference can run during the call; this was previously deadlocked.
        async with asyncio.timeout(.1):
            async with engine.lock:
                pass
        with pytest.raises(VoiceError, match='已有'):
            await hub.create('owner', 'b')
        with pytest.raises(VoiceError) as denied:
            await hub.end('other', desc['id'])
        assert denied.value.status == 404 and hub.active
    finally:
        await hub.end('owner', desc['id'])
    assert not engine.voice_reserved and not hub.active


async def test_unconnected_lease_expires_and_failed_warmup_releases_slot():
    hub, engine, speech, store = fixture_hub(connect_timeout=.01)
    await hub.create('owner', 'a')
    await asyncio.sleep(.03)
    assert not hub.active and not engine.voice_reserved
    speech.start.side_effect = RuntimeError('failed warmup')
    with pytest.raises(RuntimeError):
        await hub.create('owner', 'a')
    assert not hub.active and not engine.voice_reserved


async def test_pcm_transcript_durable_result_and_format_precede_binary_audio():
    hub, engine, speech, store = fixture_hub()
    desc = await hub.create('owner', 'a')
    async with connected(hub, desc) as (ws, client):
        await ws.send_bytes(PCM)
        recognized = await receive_type(ws, 'recognized')
        assert recognized['text'] == '你好' and recognized['session_id'] == 'a'
        submitted = await receive_type(ws, 'task')
        assert store.admissions[0][:3] == ('owner', 'a', '你好')
        # Durable execution, including its terminal verification, is external to
        # the transport. No interim model text is sent to the speech worker.
        assert speech.tts == []
        job = store.jobs[submitted['job_id']]
        job.update(status='succeeded', result='任务已经完成。')
        result = await receive_type(ws, 'result')
        assert result['text'] == '任务已经完成。'
        audio_format = await receive_type(ws, 'audio_format')
        assert audio_format['sample_rate'] == 24000 and audio_format['epoch'] == recognized['epoch']
        binary = await ws.receive()
        assert binary.type == WSMsgType.BINARY and binary.data == b'\x01\x00' * 240
        assert speech.tts[0][1]['owner'] == desc['id']
        assert speech.tts[0][1]['text'] == '任务已经完成。'


async def test_barge_in_invalidates_audio_but_does_not_cancel_durable_job():
    hub, engine, speech, store = fixture_hub()
    desc = await hub.create('owner', 'a')
    async with connected(hub, desc) as (ws, client):
        await ws.send_bytes(PCM)
        submitted = await receive_type(ws, 'task')
        job = store.jobs[submitted['job_id']]
        await ws.send_json({'type': 'interrupt', 'epoch': 1})
        cleared = await receive_type(ws, 'clear_playback')
        assert cleared['epoch'] == 2 and job['status'] == 'queued'
        job.update(status='succeeded', result='稍早任务的结果。')
        assert (await receive_type(ws, 'result'))['text'] == '稍早任务的结果。'
        await asyncio.sleep(.03)
        assert speech.tts == []
        speech.interrupt.assert_awaited_with(owner=desc['id'], op='tts')
    assert job['status'] == 'succeeded'


async def test_disconnect_keeps_task_and_new_session_gets_no_late_audio():
    hub, engine, speech, store = fixture_hub()
    first = await hub.create('owner', 'a')
    async with connected(hub, first) as (ws, client):
        await ws.send_bytes(PCM)
        submitted = await receive_type(ws, 'task')
    job = store.jobs[submitted['job_id']]
    assert job['status'] == 'queued' and not hub.active
    second = await hub.create('owner', 'b')
    async with connected(hub, second) as (ws, client):
        job.update(status='succeeded', result='A 的结果')
        await asyncio.sleep(.03)
        assert speech.tts == [] and hub._lease.pending == {}
        assert job['session_id'] == 'a'


async def test_input_validation_and_authentication_are_fail_closed():
    hub, engine, speech, store = fixture_hub()
    with pytest.raises(VoiceError):
        await hub.create('other', 'a')
    desc = await hub.create('owner', 'a')
    async with connected(hub, desc) as (ws, client):
        response = await client.get(desc['stream_path'], headers={'Authorization': 'other'})
        assert response.status == 404
        await ws.send_bytes(b'\0')
        error = await receive_type(ws, 'error')
        assert error['code'] == 'invalid_audio'
    assert store.jobs == {} and not engine.voice_reserved


async def test_shared_speech_interrupt_cannot_cancel_other_owner_or_asr():
    worker = ResidentSpeech()
    worker.active_id, worker.active_owner, worker.active_op = 'request', 'telegram', 'tts'
    worker.proc = SimpleNamespace(returncode=None)
    worker.write = AsyncMock()
    assert not await worker.interrupt(owner='mobile', op='tts')
    worker.active_owner, worker.active_op = 'mobile', 'asr'
    assert not await worker.interrupt(owner='mobile', op='tts')
    worker.write.assert_not_awaited()
    worker.active_op = 'tts'
    assert await worker.interrupt(owner='mobile', op='tts')
    worker.write.assert_awaited_once_with({'id': 'request', 'op': 'cancel'})


async def test_media_is_rejected_while_reserved_and_reservation_owner_controls_release():
    engine = Engine()
    engine._voice_owner = 'first'
    with pytest.raises(RuntimeError, match='排队'):
        await engine.start('image')
    await engine.release_voice('stale')
    assert engine.voice_reserved
    await engine.release_voice('first')
    assert not engine.voice_reserved


async def test_media_warmup_is_not_preempted_by_new_voice_reservation():
    engine = Engine()
    engine.start = AsyncMock()
    engine._media_running = True
    with pytest.raises(RuntimeError, match='媒体任务'):
        await engine.reserve_voice('call')
    assert not engine.voice_reserved
    engine.start.assert_not_awaited()


async def test_late_asr_cannot_enqueue_after_ending_call():
    hub, engine, speech, store = fixture_hub()
    entered = asyncio.Event()
    async def slow_asr(*_, **__):
        entered.set()
        await asyncio.sleep(30)
    speech.transcribe.side_effect = slow_asr
    desc = await hub.create('owner', 'a')
    async with connected(hub, desc) as (ws, client):
        await ws.send_bytes(PCM)
        await asyncio.wait_for(entered.wait(), 1)
        await hub.end('owner', desc['id'])
    assert store.jobs == {} and not engine.voice_reserved


async def test_utterance_backpressure_is_bounded_and_explicit():
    hub, engine, speech, store = fixture_hub()
    desc = await hub.create('owner', 'a')
    try:
        lease = hub._lease
        for _ in range(4):
            await hub.feed(lease, PCM)
        with pytest.raises(VoiceError) as error:
            await hub.feed(lease, PCM)
        assert error.value.code == 'audio_backpressure'
        assert lease.turns.qsize() == 4
    finally:
        await hub.end('owner', desc['id'])


@pytest.mark.parametrize('control', [False, True])
async def test_cli_revocation_rejects_the_next_audio_or_control_packet(revocable_device, control):
    hub, engine, speech, store = fixture_hub()
    hub.poll_interval = 30  # No periodic reaper is involved in this boundary check.
    desc = await hub.create('owner', 'a')
    async with connected(hub, desc, revocable_device.check) as (ws, _):
        revocable_device.revoke()
        if control:
            await ws.send_json({'type': 'interrupt', 'epoch': 0})
        else:
            await ws.send_bytes(PCM)
        assert await receive_closed(ws) == []
    assert store.admissions == []
    speech.transcribe.assert_not_awaited()
    assert not hub.active and not engine.voice_reserved


async def test_cli_revocation_during_asr_cannot_emit_transcript_or_admit_task(revocable_device):
    hub, engine, speech, store = fixture_hub()
    hub.poll_interval = 30
    entered, resume = asyncio.Event(), asyncio.Event()
    async def slow_asr(*_, **__):
        entered.set()
        await resume.wait()
        return {'text': 'revoked private transcript', 'language': ['Chinese']}
    speech.transcribe.side_effect = slow_asr
    desc = await hub.create('owner', 'a')
    async with connected(hub, desc, revocable_device.check) as (ws, _):
        await ws.send_bytes(PCM)
        await receive_type(ws, 'clear_playback')
        await asyncio.wait_for(entered.wait(), 1)
        revocable_device.revoke(); resume.set()
        assert await receive_closed(ws) == []
    assert store.admissions == [] and not engine.voice_reserved


async def test_revocation_during_recognized_queue_wait_is_rechecked_before_admission(revocable_device):
    hub, engine, speech, store = fixture_hub()
    hub.poll_interval = 30
    entered, resume = asyncio.Event(), asyncio.Event()
    emit = hub._emit
    async def delayed_emit(lease, event, **kwargs):
        if event['type'] == 'recognized':
            # Simulate outgoing queue backpressure after its initial auth check.
            hub._check_credentials(lease)
            entered.set()
            await resume.wait()
            return
        return await emit(lease, event, **kwargs)
    hub._emit = delayed_emit
    desc = await hub.create('owner', 'a')
    async with connected(hub, desc, revocable_device.check) as (ws, _):
        await ws.send_bytes(PCM)
        await receive_type(ws, 'clear_playback')
        await asyncio.wait_for(entered.wait(), 1)
        revocable_device.revoke(); resume.set()
        assert await receive_closed(ws) == []
    assert store.admissions == [] and not engine.voice_reserved


async def test_revoked_sender_drops_queued_event_and_keeps_admitted_job(revocable_device):
    hub, engine, speech, store = fixture_hub()
    await hub.create('owner', 'a')
    lease = hub._lease
    lease.authorize = revocable_device.check
    lease.ws = SimpleNamespace(closed=False, send_json=AsyncMock(), send_bytes=AsyncMock(), close=AsyncMock())
    durable = store.enqueue('owner', 'a', 'previously admitted', 'allowed')
    lease.outgoing.put_nowait((None, {'type': 'result', 'text': 'private result'}, None))
    revocable_device.revoke()
    await hub._run_worker(lease, hub._sender)
    lease.ws.send_json.assert_not_awaited()
    lease.ws.send_bytes.assert_not_awaited()
    assert durable['status'] == 'queued' and not engine.voice_reserved


async def test_revocation_while_sending_format_prevents_associated_pcm(revocable_device):
    hub, engine, speech, store = fixture_hub()
    await hub.create('owner', 'a')
    lease = hub._lease
    lease.authorize = revocable_device.check
    async def send_format(_event):
        # Simulate a revocation committed while send_json yields to transport.
        await asyncio.sleep(0)
        revocable_device.revoke()
    lease.ws = SimpleNamespace(closed=False, send_json=AsyncMock(side_effect=send_format), send_bytes=AsyncMock(), close=AsyncMock())
    await hub._emit(lease, {'type': 'audio_format', 'sample_rate': 24000}, epoch=0, audio=b'\x01\0' * 240)
    await hub._run_worker(lease, hub._sender)
    lease.ws.send_json.assert_awaited_once()
    lease.ws.send_bytes.assert_not_awaited()
    assert not hub.active and not engine.voice_reserved


@pytest.mark.asyncio
async def test_upgrade_drain_prevents_new_live_voice_without_loading_model():
    hub,engine,speech,store=fixture_hub()
    store.evolution=SimpleNamespace(draining=lambda:True)
    with pytest.raises(VoiceError,match='升级'):
        await hub.create('owner','a')
    assert hub._lease is None
    engine.start.assert_not_awaited()
