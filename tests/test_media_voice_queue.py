"""Exercise voice/media admission without model processes or network requests."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from local_ai import engine as engine_module


@pytest.fixture
def rig(tmp_path, monkeypatch):
    output = tmp_path / 'comfy' / 'output'
    output.mkdir(parents=True)
    artifact = output / 'result.png'
    artifact.write_bytes(b'fixture')
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    monkeypatch.setattr(engine_module, 'COMFY', output.parent)
    monkeypatch.setattr(engine_module, 'RUNTIME', runtime)
    submitted = []

    class Response:
        status = 200

        def __init__(self, value):
            self.value = value

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def json(self):
            return self.value

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def post(self, url, **kwargs):
            if url.endswith('/v1/chat/completions'):
                return Response({'choices': [{'message': {'content': 'still speaking'}}]})
            assert url == engine_module.COMFY_URL + '/prompt'
            submitted.append(kwargs['json'])
            return Response({'prompt_id': 'p1'})

        def get(self, url):
            assert url == engine_module.COMFY_URL + '/history/p1'
            return Response({'p1': {'status': {'completed': True}, 'outputs': {
                'save': {'images': [{'filename': artifact.name}]}}}})

    monkeypatch.setattr(engine_module.aiohttp, 'ClientSession', lambda **_: Client())
    engine = engine_module.Engine()
    engine.process = SimpleNamespace(returncode=None)

    async def start(kind):
        engine.kind = kind

    engine.start = AsyncMock(side_effect=start)
    engine.stop = AsyncMock()
    return SimpleNamespace(engine=engine, artifact=artifact, submitted=submitted)


def render(engine, kind='image'):
    return asyncio.create_task(engine.render(kind, 'brief', 'job', workflow={'frozen': True}))


@pytest.mark.parametrize('kind', ['image', 'video'])
async def test_media_waits_for_voice_without_blocking_chat_and_ignores_stale_release(rig, kind):
    engine = rig.engine
    await engine.reserve_voice('call')
    task = render(engine, kind)
    await asyncio.sleep(0)
    assert not task.done() and not engine.lock.locked() and not engine._media_running
    assert rig.submitted == []
    assert await asyncio.wait_for(engine.chat([{'role': 'user', 'content': 'hello'}]), 1) == 'still speaking'
    await engine.release_voice('another-call')
    await asyncio.sleep(0)
    assert engine.voice_reserved and not task.done()
    await engine.release_voice('call')
    assert await asyncio.wait_for(task, 1) == rig.artifact
    assert rig.submitted == [{'prompt': {'frozen': True}, 'client_id': 'job'}]
    assert not engine.lock.locked() and not engine._media_running
    engine.stop.assert_not_awaited()


@pytest.mark.parametrize('kind', ['image', 'video'])
async def test_voice_winning_during_media_lock_wait_is_rechecked_and_queued(rig, kind):
    engine = rig.engine
    # Reproduce chat rewrite holding inference while render and voice queue.
    engine.kind = 'chat'
    await engine.lock.acquire()
    task = render(engine, kind)
    await asyncio.sleep(0)
    voice = asyncio.create_task(engine.reserve_voice('call'))
    await asyncio.sleep(0)
    assert engine.voice_reserved and not voice.done() and not task.done()
    engine.lock.release()
    await asyncio.wait_for(voice, 1)
    assert not task.done() and not engine.lock.locked() and not engine._media_running
    assert rig.submitted == []
    engine.start.assert_awaited_once_with('chat')
    await engine.release_voice('call')
    assert await asyncio.wait_for(task, 1) == rig.artifact
    engine.stop.assert_not_awaited()


async def test_cancelled_media_wait_does_not_stop_active_voice(rig):
    engine = rig.engine
    await engine.reserve_voice('call')
    task = render(engine)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert engine.voice_reserved and engine.kind == 'chat'
    assert not engine.lock.locked() and not engine._media_running
    assert await engine.chat([{'role': 'user', 'content': 'continue'}]) == 'still speaking'
    engine.stop.assert_not_awaited()
    assert rig.submitted == []
    await engine.release_voice('call')


async def test_cancelled_media_lock_wait_does_not_stop_pending_voice(rig):
    engine = rig.engine
    engine.kind = 'chat'
    await engine.lock.acquire()
    task = render(engine)
    await asyncio.sleep(0)
    voice = asyncio.create_task(engine.reserve_voice('call'))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert engine.voice_reserved and engine.lock.locked()
    engine.stop.assert_not_awaited()
    engine.lock.release()
    await asyncio.wait_for(voice, 1)
    await engine.release_voice('call')
    assert rig.submitted == []


async def test_cancelled_voice_warmup_wakes_queued_media(rig):
    engine = rig.engine
    entered = asyncio.Event()

    async def start(kind):
        engine.kind = kind
        if kind == 'chat':
            entered.set()
            await asyncio.Future()

    engine.start.side_effect = start
    voice = asyncio.create_task(engine.reserve_voice('call'))
    await entered.wait()
    task = render(engine)
    await asyncio.sleep(0)
    assert not task.done()
    voice.cancel()
    with pytest.raises(asyncio.CancelledError):
        await voice
    assert await asyncio.wait_for(task, 1) == rig.artifact
    assert not engine.voice_reserved and not engine.lock.locked()
    engine.stop.assert_not_awaited()


async def test_admitted_media_blocks_new_voice_and_cancel_releases_slot(rig):
    engine = rig.engine
    entered = asyncio.Event()

    async def start(_):
        entered.set()
        await asyncio.Future()

    engine.start.side_effect = start
    task = render(engine)
    await entered.wait()
    with pytest.raises(RuntimeError, match='媒体任务'):
        await engine.reserve_voice('too-late')
    assert not engine.voice_reserved and engine._media_running
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not engine.lock.locked() and not engine._media_running
    engine.stop.assert_awaited_once()
