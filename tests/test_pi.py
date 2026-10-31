import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import local_ai.pi_agent as module
from local_ai.pi_agent import UserTools, PiAgent
from local_ai.store import Store
from local_ai.runtime_store import MAX_CHECKPOINT_BYTES, encode


@pytest.fixture
def setup(tmp_path, monkeypatch):
    store = Store(tmp_path/'db.sqlite3')
    jid = store.enqueue(11,11,'chat','test')
    store.update(jid,status='running')
    tools = UserTools(store,store.get(jid),lambda uid:uid==11,tmp_path/'users')
    monkeypatch.setattr(module,'model_status',lambda _:[])
    yield store,tools
    store.db.close()


@pytest.mark.asyncio
async def test_workspace_rejects_escape_symlink_and_other_users(setup,tmp_path):
    store,tools=setup
    await tools.execute('write_file',{'name':'memo.txt','content':'my private note'})
    assert (await tools.execute('read_file',{'name':'memo.txt'}))['content']=='my private note'
    for name in ('../secret.txt','/tmp/secret.txt','.env','../22/memo.txt'):
        with pytest.raises(ValueError): await tools.execute('read_file',{'name':name})
    secret=tmp_path/'secret.txt';secret.write_text('secret')
    (tools.directory()/'link.txt').symlink_to(secret)
    with pytest.raises(OSError): await tools.execute('write_file',{'name':'link.txt','content':'overwrite'})
    assert secret.read_text()=='secret'
    other_id=store.enqueue(22,22,'chat','other')
    other=UserTools(store,store.get(other_id),lambda _:True,tmp_path/'users')
    with pytest.raises(FileNotFoundError):await other.execute('read_file',{'name':'memo.txt'})
    with pytest.raises(ValueError):await tools.execute('task_status',{'job_id':other_id})


@pytest.mark.asyncio
async def test_media_submission_bound_to_owner_deduplicated_and_persistent(setup):
    store,tools=setup
    args={'prompt':'a cat','user_id':22,'chat_id':22}
    first=await tools.execute('generate_image',args)
    second=await tools.execute('generate_image',args)
    assert first==second
    child=store.get(first['job_id'])
    assert (child['user_id'],child['chat_id'],child['status'])==(11,11,'queued')
    video=await tools.execute('generate_video',{'prompt':'a boat','hd':True,'character_photos':'用户选择不用','environment_photos':'用户选择不用'})
    assert store.get(video['job_id'])['hd']==1
    with pytest.raises(ValueError):await tools.execute('generate_image',{'prompt':'another cat'})
    tools.allowed=lambda _:False
    with pytest.raises(ValueError):await tools.execute('list_files',{})


@pytest.mark.asyncio
async def test_cancel_pi_without_model_lease_reaps_child_without_stopping_other_inference(tmp_path,monkeypatch):
    real_node=module.ROOT/'vendor/node'
    (tmp_path/'vendor').mkdir();(tmp_path/'pi').mkdir()
    (tmp_path/'vendor/node').symlink_to(real_node.resolve())
    (tmp_path/'pi/runner.mjs').write_text("process.stdin.resume(); setInterval(()=>{},1000);")
    monkeypatch.setattr(module,'ROOT',tmp_path)
    store=Store(tmp_path/'db.sqlite3');jid=store.enqueue(1,1,'chat','hello')
    engine=SimpleNamespace(lock=asyncio.Lock(),start=AsyncMock(),stop=AsyncMock())
    original=asyncio.create_subprocess_exec
    children=[]
    async def capture(*args,**kwargs):
        proc=await original(*args,**kwargs);children.append(proc);return proc
    monkeypatch.setattr(asyncio,'create_subprocess_exec',capture)
    task=asyncio.create_task(PiAgent(engine,store,lambda _:True,tmp_path).run(store.get(jid)))
    for _ in range(100):
        if children:break
        await asyncio.sleep(.01)
    assert children
    task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert children[0].returncode is not None
    engine.stop.assert_not_awaited()
    store.db.close()


@pytest.fixture
def checkpoint_protocol(tmp_path, monkeypatch):
    """Exercise the real child stdout pipe without loading a model."""
    real_node = module.ROOT/'vendor/node'
    (tmp_path/'vendor').mkdir()
    (tmp_path/'pi').mkdir()
    (tmp_path/'vendor/node').symlink_to(real_node.resolve())
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    store = Store(tmp_path/'db.sqlite3')
    jid = store.enqueue(1, 1, 'chat', 'preserve this request')
    store.update(jid, status='running')
    previous = [{'role': 'user', 'content': 'previous durable checkpoint'}]
    store.save_checkpoint(jid, previous)
    engine = SimpleNamespace(lock=asyncio.Lock(), start=AsyncMock(), stop=AsyncMock())

    def create(*, checkpoint_bytes, padding_bytes=0):
        # Count UTF-8 bytes, since Chinese text hits the pipe limit much sooner
        # than a same-length ASCII transcript.
        empty = [{'role': 'user', 'content': ''}]
        content_bytes = checkpoint_bytes-len(encode(empty).encode('utf-8'))
        repeats, remainder = divmod(content_bytes, len('界'.encode('utf-8')))
        messages = [{'role': 'user', 'content': '界'*repeats+'x'*remainder}]
        runner = tmp_path/'pi/runner.mjs'
        runner.write_text("""
import {createInterface} from 'node:readline';
const lines = createInterface({input: process.stdin});
lines.on('line', line => {
  const message = JSON.parse(line);
  if (message.type === 'init') {
    const event = {type: 'checkpoint', id: 'large-checkpoint', input_ids: [],
      messages: [{role: 'user', content: '界'.repeat(REPEATS)+'x'.repeat(REMAINDER)}],
      padding: 'x'.repeat(PADDING)};
    process.stdout.write(JSON.stringify(event)+'\\n');
  } else if (message.type === 'checkpoint_result') {
    process.stdout.write(JSON.stringify({type: 'result', protocol: 2,
      answer: 'checkpoint acknowledged', calls: 0, turns: 1, limited: false})+'\\n');
    lines.close();
  }
});
""".replace('REPEATS', str(repeats)).replace('REMAINDER', str(remainder)).replace('PADDING', str(padding_bytes)))
        reviewer = SimpleNamespace(review=AsyncMock(return_value={'accepted': True, 'outcome': 'completed'}))
        return PiAgent(engine, store, lambda _: True, tmp_path, runner=runner, reviewer=reviewer), messages

    yield store, jid, previous, create
    store.db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('checkpoint_bytes', [300_000, MAX_CHECKPOINT_BYTES])
async def test_pi_accepts_large_utf8_checkpoint_and_acknowledges(checkpoint_protocol, checkpoint_bytes):
    store, jid, _, create = checkpoint_protocol
    agent, messages = create(checkpoint_bytes=checkpoint_bytes)
    assert len(encode(messages).encode('utf-8')) == checkpoint_bytes
    assert await asyncio.wait_for(agent.run(store.get(jid)), 15) == 'checkpoint acknowledged'
    assert store.checkpoint(jid) == messages


@pytest.mark.asyncio
async def test_pi_oversize_checkpoint_preserves_last_saved_request(checkpoint_protocol):
    store, jid, previous, create = checkpoint_protocol
    agent, _ = create(checkpoint_bytes=MAX_CHECKPOINT_BYTES+1)
    with pytest.raises(RuntimeError, match='会话保存内容超过 4 MB 上限'):
        await asyncio.wait_for(agent.run(store.get(jid)), 15)
    assert store.checkpoint(jid) == previous


@pytest.mark.asyncio
async def test_pi_oversize_protocol_frame_reports_limit_and_preserves_checkpoint(checkpoint_protocol):
    store, jid, previous, create = checkpoint_protocol
    agent, _ = create(checkpoint_bytes=100, padding_bytes=module.MAX_PI_EVENT_BYTES)
    with pytest.raises(RuntimeError, match='Pi 通信消息超过') as raised:
        await asyncio.wait_for(agent.run(store.get(jid)), 15)
    assert isinstance(raised.value.__cause__, ValueError)
    assert store.checkpoint(jid) == previous
