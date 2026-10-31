import asyncio
import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from local_ai.bot import LocalBot
from local_ai.store import Store


@pytest.mark.asyncio
async def test_external_wait_does_not_block_other_session_or_delivery(tmp_path):
    store = Store(tmp_path/'db')
    first = store.enqueue(1, 1, 'chat', 'slow external lookup')
    same_session = store.enqueue(1, 1, 'chat', 'follow-up')
    other_session = store.enqueue(2, 2, 'chat', 'quick action')
    started, release, follow_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    bot = LocalBot.__new__(LocalBot)
    bot.store = store
    bot.is_allowed = lambda _: True
    bot.last_work = 0
    bot.engine = SimpleNamespace(lock=asyncio.Lock(), stop=AsyncMock(), process=None)
    bot.speech = SimpleNamespace(worker=SimpleNamespace(proc=None))
    async def execute(job):
        if job['id'] == first:
            started.set()
            await release.wait()
        if job['id'] == same_session:
            follow_started.set()
        return job['prompt']
    bot.execute = execute
    api = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=42)))
    worker = asyncio.create_task(bot.worker(SimpleNamespace(bot=api)))
    try:
        async with asyncio.timeout(3):
            await started.wait()
            while not store.get(other_session)['delivered']:
                await asyncio.sleep(.01)
        assert not follow_started.is_set()
        assert store.get(first)['status'] == 'running'
        assert store.get(other_session)['delivery_state'] == 'delivered'
        release.set()
        async with asyncio.timeout(3):
            while not store.get(same_session)['delivered']:
                await asyncio.sleep(.01)
        # Same conversation remains ordered although other conversations advanced.
        assert [call.args[1] for call in api.send_message.await_args_list if call.args[0] == 1] == [
            'slow external lookup', 'follow-up']
    finally:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError): await worker
        store.db.close()


@pytest.mark.asyncio
async def test_service_shutdown_keeps_request_and_input_for_resume(tmp_path):
    store = Store(tmp_path/'db')
    audio = tmp_path/'audio.ogg'
    audio.write_bytes(b'retained input')
    jid = store.enqueue(1, 1, 'chat', 'long task', audio=str(audio))
    started = asyncio.Event()
    bot = LocalBot.__new__(LocalBot)
    bot.store, bot.is_allowed, bot.last_work = store, lambda _: True, 0
    bot.engine = SimpleNamespace(lock=asyncio.Lock(), stop=AsyncMock(), process=None)
    bot.speech = SimpleNamespace(worker=SimpleNamespace(proc=None))
    async def execute(job):
        store.save_checkpoint(jid, [{'role': 'user', 'content': job['prompt']}])
        started.set()
        await asyncio.Event().wait()
    bot.execute = execute
    worker = asyncio.create_task(bot.worker(SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))))
    try:
        await asyncio.wait_for(started.wait(), 3)
        worker.cancel()
        with pytest.raises(asyncio.CancelledError): await worker
        assert store.get(jid)['status'] == 'queued'
        assert store.checkpoint(jid)[0]['content'] == 'long task'
        assert audio.exists()
    finally:
        if not worker.done():
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError): await worker
        store.db.close()
