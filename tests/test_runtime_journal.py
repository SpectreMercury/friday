import asyncio
import json

import pytest

from local_ai.capabilities import Capability, CapabilityRegistry, string
from local_ai.store import Store
from local_ai.tool_runtime import ToolRuntime
from local_ai.tool_errors import ToolFailure


@pytest.fixture
def run(tmp_path):
    store = Store(tmp_path/'db')
    jid = store.enqueue(1, 1, 'chat', '整理文档')
    store.update(jid, status='running')
    registry = CapabilityRegistry()
    runtime = ToolRuntime(store, store.get(jid), registry, lambda _: True)
    yield store, jid, registry, runtime
    store.db.close()


@pytest.mark.asyncio
async def test_completed_write_is_replayed_from_receipt_not_executed(run, tmp_path):
    store, jid, registry, runtime = run
    path = tmp_path/'output.txt'
    async def write(name, args):
        path.write_text(path.read_text()+'x' if path.exists() else 'x')
        return {'path': str(path)}
    registry.register(Capability('write', 'write', effect='local_write', replay='reconcile'), write)
    await runtime.call('call-1', 'write', {})
    reopened = Store(tmp_path/'db')
    try:
        resumed = ToolRuntime(reopened, reopened.get(jid), registry, lambda _: True)
        result = await resumed.call('call-1', 'write', {})
        assert result['cached'] and path.read_text() == 'x'
        assert reopened.get(jid)['status'] == 'running'
    finally:
        reopened.db.close()


@pytest.mark.asyncio
async def test_crash_after_mutation_before_receipt_cannot_replay_with_new_id(run, tmp_path):
    store, jid, registry, runtime = run
    path = tmp_path/'effect.txt'
    async def crash_after_write(name, args):
        path.write_text('created')
        raise asyncio.CancelledError()
    registry.register(Capability('write', 'write', effect='local_write', replay='reconcile'), crash_after_write)
    with pytest.raises(asyncio.CancelledError):
        await runtime.call('original', 'write', {})
    outcome = await runtime.call('original', 'write', {})
    assert outcome['failure']['code'] == 'effect_uncertain'
    with pytest.raises(ToolFailure, match='不能重复'):
        await runtime.call('different-id', 'write', {})
    assert path.read_text() == 'created'
    assert store.operation(jid, 'original')['status'] == 'uncertain'


@pytest.mark.asyncio
async def test_read_can_resume_and_tool_result_pair_is_restored(run):
    store, jid, registry, runtime = run
    calls = []
    async def read(name, args):
        calls.append(name)
        return {'contents': 'live document'}
    spec = Capability('read', 'read')
    registry.register(spec, read)
    store.begin_operation(jid, 'r1', spec, {})  # interrupted before obtaining result
    messages = [{'role': 'user', 'content': '读取文档'},
                {'role': 'assistant', 'content': [{'type': 'toolCall', 'id': 'r1', 'name': 'read', 'arguments': {}}]}]
    store.save_checkpoint(jid, messages)
    repaired = await runtime.repair_checkpoint(store.checkpoint(jid))
    assert repaired[-1]['role'] == 'toolResult' and repaired[-1]['toolCallId'] == 'r1'
    assert json.loads(repaired[-1]['content'][0]['text'])['contents'] == 'live document'
    store.save_checkpoint(jid, repaired)
    assert await runtime.repair_checkpoint(store.checkpoint(jid)) == repaired
    assert calls == ['read']


@pytest.mark.asyncio
async def test_registry_permissions_schema_availability_and_revocation(run):
    store, jid, registry, runtime = run
    async def handler(name, args):
        return args
    registry.register(Capability('admin', 'admin only', access='admin'), handler)
    registry.register(Capability('read', 'read', {'path': string()}, ('path',)), handler)
    registry.register(Capability('missing', 'missing'), handler, lambda: (False, 'missing_configuration'))
    assert [x['name'] for x in registry.definitions()] == ['read']
    assert registry.describe()[-1]['reason'] == 'missing_configuration'
    with pytest.raises(PermissionError):
        await runtime.call('a', 'admin', {})
    with pytest.raises(ValueError):
        await runtime.call('b', 'read', {'path': 'x', 'user_id': 2})
    assert not store.operation(jid, 'b')
    with pytest.raises(ToolFailure):
        await registry.execute('missing', {})
    await runtime.call('c', 'read', {'path': 'x'})
    runtime.allowed = lambda _: False
    with pytest.raises(PermissionError):
        await runtime.call('c', 'read', {'path': 'x'})


@pytest.mark.asyncio
async def test_changed_arguments_cannot_reuse_call_identity(run):
    store, jid, registry, runtime = run
    async def handler(name, args): return args
    registry.register(Capability('read', 'read', {'path': string()}, ('path',)), handler)
    await runtime.call('r', 'read', {'path': 'first'})
    with pytest.raises(ToolFailure, match='不能更改'):
        await runtime.call('r', 'read', {'path': 'second'})
