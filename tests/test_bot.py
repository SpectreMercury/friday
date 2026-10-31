from telegram.error import RetryAfter
from local_ai.tool_errors import ToolFailure
import asyncio
import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import local_ai.bot as module


@pytest.fixture
def bot(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_ADMIN_USER_ID", "1")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "2,3")
    monkeypatch.setattr(module, "RUNTIME", tmp_path)
    instance = module.LocalBot()
    yield instance
    instance.store.db.close()


@pytest.mark.asyncio
async def test_revoked_user_cannot_receive_queued_result(bot):
    job_id = bot.store.enqueue(2, 2, "chat", "hello")
    bot.store.update(job_id, status="succeeded", result="private reply")
    bot.store.set_access(2, False)
    app = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=101))))
    await bot.deliver(app, bot.store.get(job_id))
    app.bot.send_message.assert_not_awaited()
    assert bot.store.get(job_id)["delivered"] == 1


@pytest.mark.asyncio
async def test_cancel_active_request_keeps_worker_alive_for_next_user(bot):
    first = bot.store.enqueue(2, 2, "chat", "slow")
    second = bot.store.enqueue(3, 3, "chat", "next")
    blocked = asyncio.Event()
    async def execute(job):
        if job["id"] == first:
            blocked.set()
            await asyncio.sleep(60)
        return "done"
    bot.execute = execute
    app = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=101))))
    worker = asyncio.create_task(bot.worker(app))
    try:
        await asyncio.wait_for(blocked.wait(), 2)
        await bot.cancel_job(first)
        for _ in range(100):
            if bot.store.get(second)["delivered"]:
                break
            await asyncio.sleep(.02)
        assert bot.store.get(first)["status"] == "cancelled"
        assert bot.store.get(second)["status"] == "succeeded"
        assert bot.store.get(second)["delivered"] == 1
        assert not worker.done()
        assert any(call.args == (3, "done") for call in app.bot.send_message.await_args_list)
    finally:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker


def test_token_redaction_covers_tracebacks():
    import logging
    token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_123456"
    try:
        raise ValueError(token)
    except ValueError:
        import sys
        record = logging.LogRecord("test", logging.ERROR, "", 1, "URL %s", (token,), sys.exc_info())
        text = module.RedactFormatter().format(record)
    assert token not in text
    assert "[REDACTED]" in text


@pytest.mark.asyncio
async def test_cancel_parent_cancels_its_media_children_only(bot):
    import json
    parent=bot.store.enqueue(2,2,'chat','draw')
    child=bot.store.enqueue(2,2,'image','a cat')
    other=bot.store.enqueue(3,3,'image','another cat')
    bot.store.db.execute('INSERT INTO pi_tools(job_id,user_id,name,args,result,created) VALUES (?,?,?,?,?,?)',
        (parent,2,'generate_image','{}',json.dumps({'job_id':child}),0))
    bot.store.db.commit()
    await bot.cancel_job(parent)
    assert bot.store.get(child)['status']=='cancelled'
    assert bot.store.get(other)['status']=='queued'


@pytest.mark.asyncio
async def test_message_reacts_and_queues_without_status_messages(bot, monkeypatch):
    monkeypatch.setattr(module, 'model_status', lambda kind: [])
    msg = SimpleNamespace(text='hi', photo=None, document=None,
                          set_reaction=AsyncMock(), reply_text=AsyncMock())
    api = SimpleNamespace(send_chat_action=AsyncMock())
    update = SimpleNamespace(effective_message=msg, effective_user=SimpleNamespace(id=2),
                             effective_chat=SimpleNamespace(id=2, type='private'),
                             update_id=123, get_bot=lambda: api)
    await bot.message(update, None)
    msg.set_reaction.assert_awaited_once()
    assert msg.set_reaction.await_args.args == ('👀',)
    msg.reply_text.assert_not_awaited()
    api.send_chat_action.assert_awaited_once_with(2, 'typing')
    assert bot.store.next()['prompt'] == 'hi'


@pytest.mark.asyncio
async def test_typing_covers_waiting_users_and_stops_when_done(bot):
    one = bot.store.enqueue(2, 2, 'chat', 'hi')
    bot.store.enqueue(2, 2, 'image', 'cat')
    bot.store.enqueue(3, 3, 'chat', 'hi')
    bot.store.set_access(3, False)
    app = SimpleNamespace(bot=SimpleNamespace(send_chat_action=AsyncMock()))
    await bot.refresh_typing(app)
    assert app.bot.send_chat_action.await_count == 1
    assert app.bot.send_chat_action.await_args.args == (2, 'typing')
    bot.store.db.execute("UPDATE jobs SET status='cancelled'")
    bot.store.update(one, status='succeeded', result='hello')
    app.bot.send_chat_action.reset_mock()
    await bot.refresh_typing(app)
    assert app.bot.send_chat_action.await_count == 1
    bot.store.update(one, delivered=1)
    app.bot.send_chat_action.reset_mock()
    await bot.refresh_typing(app)
    app.bot.send_chat_action.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncertain_delivery_stops_typing_until_new_work_arrives(bot):
    jid = bot.store.enqueue(2, 2, 'chat', 'hello')
    bot.store.update(jid, status='succeeded', result='hello')
    api = SimpleNamespace(send_message=AsyncMock(side_effect=TimeoutError('no receipt')),
                          send_chat_action=AsyncMock())
    app = SimpleNamespace(bot=api)
    with pytest.raises(ToolFailure):
        await bot.deliver(app, bot.store.get(jid))
    assert bot.store.get(jid)['delivery_state'] == 'uncertain'
    assert not bot.store.pending_delivery()
    await bot.refresh_typing(app)
    api.send_chat_action.assert_not_awaited()
    bot.store.enqueue(2, 2, 'chat', 'next request')
    await bot.refresh_typing(app)
    api.send_chat_action.assert_awaited_once()
    api.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_voice_transcribes_before_pi_and_synthesizes_reply(bot, tmp_path):
    audio = tmp_path / 'incoming.ogg'
    audio.write_bytes(b'input')
    jid = bot.store.enqueue(2, 2, 'chat', '', audio=str(audio))
    bot.speech.transcribe = AsyncMock(return_value='你好，请介绍自己。')
    bot.speech.synthesize = AsyncMock(return_value=tmp_path / 'reply.ogg')
    async def answer(job):
        assert job['prompt'] == '你好，请介绍自己。'
        return '你好，我是你的助手。'
    bot.pi.run = answer
    result = await bot.execute(bot.store.get(jid))
    assert result == '你好，我是你的助手。'
    bot.speech.transcribe.assert_awaited_once_with(str(audio), jid)
    bot.speech.synthesize.assert_awaited_once_with(result, jid)
    assert bot.store.get(jid)['voice_result'] == str(tmp_path / 'reply.ogg')
    assert bot.store.messages(2, 'next')[-3]['content'] == '你好，请介绍自己。'
    assert len(bot.store.messages(3, 'next')) == 2


@pytest.mark.asyncio
async def test_voice_delivery_retry_does_not_repeat_text(bot, tmp_path):
    voice = tmp_path / 'reply.ogg'
    voice.write_bytes(b'audio')
    jid = bot.store.enqueue(2, 2, 'chat', 'hello', audio='input')
    bot.store.update(jid, status='succeeded', result='hello', voice_result=str(voice))
    app = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=101)), send_voice=AsyncMock(side_effect=[RetryAfter(1), SimpleNamespace(message_id=102)])))
    with pytest.raises(RetryAfter):
        await bot.deliver(app, bot.store.get(jid))
    await bot.deliver(app, bot.store.get(jid))
    app.bot.send_message.assert_awaited_once()
    assert app.bot.send_voice.await_count == 2
    assert bot.store.get(jid)['delivered'] == 1


@pytest.mark.asyncio
async def test_tts_failure_still_returns_text(bot):
    jid = bot.store.enqueue(2, 2, 'chat', '', audio='input')
    bot.speech.transcribe = AsyncMock(return_value='你好')
    bot.pi.run = AsyncMock(return_value='你好！')
    bot.speech.synthesize = AsyncMock(side_effect=RuntimeError('TTS failed'))
    assert await bot.execute(bot.store.get(jid)) == '你好！'
    assert bot.store.get(jid)['voice_result'] is None


@pytest.mark.asyncio
async def test_telegram_voice_is_queued_as_chat(bot, monkeypatch):
    monkeypatch.setattr(module, 'model_status', lambda kind: [])
    bot.speech.ready = lambda: True
    async def download(path):
        path.write_bytes(b'voice')
    media = SimpleNamespace(duration=10, file_size=100,
        get_file=AsyncMock(return_value=SimpleNamespace(download_to_drive=download)))
    msg = SimpleNamespace(voice=media, audio=None, set_reaction=AsyncMock(), reply_text=AsyncMock())
    api = SimpleNamespace(send_chat_action=AsyncMock())
    update = SimpleNamespace(effective_message=msg, effective_user=SimpleNamespace(id=2),
        effective_chat=SimpleNamespace(id=2, type='private'), update_id=124, get_bot=lambda: api)
    await bot.message(update, None)
    job = bot.store.next()
    from pathlib import Path
    source = Path(job['audio'])
    assert job['kind'] == 'chat' and job['user_id'] == 2
    assert source.read_bytes() == b'voice'
    msg.reply_text.assert_not_awaited()
    await bot.cancel_job(job['id'])
    assert not source.exists()
    media.duration = 181
    update.update_id = 125
    await bot.message(update, None)
    msg.reply_text.assert_awaited_once()
    assert bot.store.next() is None
