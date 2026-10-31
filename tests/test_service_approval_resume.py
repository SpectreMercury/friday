"""Real service/Pi/journal approval suspension, with local model fixtures only.

Unlike direct approval-manager tests, these run the actual scheduler and Node
agent across a process teardown and checkpoint resume, and count physical writes.
"""
import asyncio
import contextlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web

from local_ai.computer_tools import ComputerTools
from local_ai.engine import ROOT
from local_ai.harness import HarnessService
from local_ai.pi_agent import PiAgent, UserTools
from local_ai.store import Store


def sse(delta, reason):
    def chunk(value, finish):
        return json.dumps({'id': 'fixture', 'object': 'chat.completion.chunk', 'model': 'qwen38',
            'choices': [{'index': 0, 'delta': value, 'finish_reason': finish}]})
    return web.Response(text=f'data: {chunk(delta, None)}\n\ndata: {chunk({}, reason)}\n\ndata: [DONE]\n\n',
                        content_type='text/event-stream')


async def settled(service, jid, status):
    async with asyncio.timeout(15):
        while True:
            job = service.store.get(jid)
            if job['status'] == 'failed' and status != 'failed':
                pytest.fail(f"Job failed before {status}: {job['error']} / {job['result']}")
            if job['status'] == status and jid not in service.active_runs:
                return job
            await asyncio.sleep(.01)


@pytest.mark.parametrize('decision', ['approved', 'cancelled'])
async def test_service_suspends_approval_then_resumes_once_or_stays_cancelled(tmp_path, monkeypatch, decision):
    store = Store(tmp_path / 'db')
    account = store.create_account('Owner', is_admin=True)
    managed = store.create_session(account['id'], 'Managed')
    managed = store.update_session(account['id'], managed['id'], managed['revision'], management=True)
    parallel = store.create_session(account['id'], 'Parallel')
    engine = SimpleNamespace(lock=asyncio.Lock(), start=AsyncMock(), stop=AsyncMock(),
        speech_worker=None, process=None, voice_reserved=False,
        chat=AsyncMock(return_value='{"job_id":null}'))
    service = HarnessService(tmp_path, store=store, engine=engine)
    host = tmp_path / 'host'
    host.mkdir()
    protected = host / 'approved.txt'
    protected.write_text('original content')
    # Test the real computer_write implementation in an isolated home directory.
    original_computer_init = ComputerTools.__init__
    def computer_init(self, job, admin, approve=None, home=None, guard=None):
        original_computer_init(self, job, admin, approve, home=host, guard=guard)
    monkeypatch.setattr(ComputerTools, '__init__', computer_init)
    calls, successful_writes, model_requests = [], [], []
    original_execute = UserTools.execute
    async def track_execute(self, name, args):
        assert not engine.lock.locked(), 'Approval and file work must release the model lease'
        calls.append((self.job['id'], name))
        result = await original_execute(self, name, args)
        if name in ('write_file', 'computer_write'):
            successful_writes.append((self.job['id'], name))
        return result
    monkeypatch.setattr(UserTools, 'execute', track_execute)

    async def respond(request):
        assert engine.lock.locked()
        payload = await request.json()
        model_requests.append(payload)
        if any(m['role'] == 'user' and 'FAST TASK' in str(m['content']) for m in payload['messages']):
            return sse({'role': 'assistant', 'content': 'Independent task completed.'}, 'stop')
        results = {m.get('tool_call_id') for m in payload['messages'] if m['role'] == 'tool'}
        if 'discover' not in results:
            cid, name, args = 'discover', 'capabilities', {}
        elif 'first-write' not in results:
            cid, name, args = 'first-write', 'write_file', {'name': 'before.txt', 'content': 'already committed'}
        elif 'approved-write' not in results:
            cid, name, args = 'approved-write', 'computer_write', {'path': str(protected), 'content': 'approved replacement'}
        elif 'verify-write' not in results:
            cid, name, args = 'verify-write', 'computer_read', {'path': str(protected)}
        else:
            return sse({'role': 'assistant', 'content': 'Both files are saved and the protected write is verified.'}, 'stop')
        return sse({'role': 'assistant', 'tool_calls': [{'index': 0, 'id': cid, 'type': 'function',
                    'function': {'name': name, 'arguments': json.dumps(args)}}]}, 'tool_calls')

    fake_model = web.Application()
    fake_model.router.add_post('/v1/chat/completions', respond)
    server = web.AppRunner(fake_model)
    await server.setup()
    site = web.TCPSite(server, '127.0.0.1', 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    (tmp_path / 'node_modules').symlink_to(ROOT / 'pi/node_modules')
    (tmp_path / 'context.mjs').write_text((ROOT / 'pi/context.mjs').read_text())
    (tmp_path / 'progress.mjs').write_text((ROOT / 'pi/progress.mjs').read_text())
    (tmp_path / 'tool-context.mjs').write_text((ROOT / 'pi/tool-context.mjs').read_text())
    runner = tmp_path / 'runner.mjs'
    runner.write_text((ROOT / 'pi/runner-v2.mjs').read_text().replace('127.0.0.1:8085', f'127.0.0.1:{port}'))
    async def review(job, answer):
        if job['session_id'] == managed['id']:
            assert protected.read_text() == 'approved replacement'
            assert store.operation(job['id'], 'verify-write')['status'] == 'succeeded'
        return {'accepted': True, 'outcome': 'completed', 'answer': answer}
    service.pi = PiAgent(engine, store, service.is_allowed, tmp_path,
        approve=service.approvals.request, runner=runner, reviewer=SimpleNamespace(review=review))
    original = service.enqueue_mobile(account['id'], managed['id'], 'Write both files, then verify.', 'original')
    jid = original['id']
    worker = asyncio.create_task(service.worker())
    try:
        waiting = await settled(service, jid, 'waiting_approval')
        assert waiting['attempt'] == 1 and not engine.lock.locked()
        before = store.workspace_file(account['id'], managed['id'], 'before.txt')
        assert before.read_text() == 'already committed'
        before_mtime = before.stat().st_mtime_ns
        assert protected.read_text() == 'original content'
        assert store.operation(jid, 'first-write')['status'] == 'succeeded'
        assert store.operation(jid, 'approved-write')['status'] == 'waiting_approval'
        checkpoint = store.checkpoint(jid)
        assert any(m.get('role') == 'assistant' and any(
            p.get('id') == 'approved-write' for p in m.get('content', []) if isinstance(p, dict)) for m in checkpoint)
        approvals = service.approvals.list_for_session(account['id'], managed['id'])
        assert len(approvals) == 1
        token = approvals[0]['token']
        # A pending approval must consume neither a run slot nor the inference
        # lock: an independently scheduled real Pi run completes before decision.
        other = service.enqueue_mobile(account['id'], parallel['id'], 'FAST TASK', 'parallel')
        await settled(service, other['id'], 'succeeded')
        assert store.get(jid)['status'] == 'waiting_approval' and not worker.done()
        if decision == 'approved':
            service.approvals.decide(account['id'], managed['id'], token, 'approved')
            done = await settled(service, jid, 'succeeded')
            assert done['attempt'] == 2 and done['outcome'] == 'completed'
            assert protected.read_text() == 'approved replacement'
            assert successful_writes.count((jid, 'write_file')) == 1
            assert successful_writes.count((jid, 'computer_write')) == 1
            assert calls.count((jid, 'write_file')) == 1
            assert before.stat().st_mtime_ns == before_mtime
            assert store.operation(jid, 'approved-write')['status'] == 'succeeded'
            assert store.db.execute('SELECT status FROM approvals WHERE token=?', (token,)).fetchone()[0] == 'consumed'
            with pytest.raises(ValueError):
                service.approvals.decide(account['id'], managed['id'], token, 'approved')
        else:
            await service.cancel_job(jid)
            with pytest.raises(ValueError):
                service.approvals.decide(account['id'], managed['id'], token, 'approved')
            assert service.approvals.list_for_session(account['id'], managed['id']) == []
            # Observe a subsequent scheduler pass with another completed task,
            # rather than only inspecting the state immediately after cancelling.
            again = service.enqueue_mobile(account['id'], parallel['id'], 'FAST TASK again', 'parallel-again')
            await settled(service, again['id'], 'succeeded')
            assert store.get(jid)['status'] == 'cancelled'
            assert store.get(jid)['attempt'] == 1 and jid not in service.active_runs
            assert protected.read_text() == 'original content'
            assert successful_writes == [(jid, 'write_file')]
            assert before.stat().st_mtime_ns == before_mtime
        assert not engine.lock.locked()
    finally:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
        await server.cleanup()
        store.db.close()


async def test_disabled_mac_management_stops_discovery_and_preserves_reason(tmp_path):
    store = Store(tmp_path/'db')
    owner = store.create_account('Owner', is_admin=True)
    session = store.create_session(owner['id'], 'Mac disabled')
    engine = SimpleNamespace(lock=asyncio.Lock(), start=AsyncMock(), stop=AsyncMock(),
        speech_worker=None, process=None, voice_reserved=False,
        chat=AsyncMock(return_value='{"job_id":null}'))
    service = HarnessService(tmp_path, store=store, engine=engine)
    discoveries = []
    async def respond(request):
        payload = await request.json()
        # A stubborn model would keep trying a different group unless the
        # runtime reserves a final response with tools disabled.
        if payload.get('tools'):
            group = ['computer','network','mac'][len(discoveries)%3]
            discoveries.append(group)
            return sse({'role':'assistant','tool_calls':[{'index':0,'id':'discover-'+str(len(discoveries)),
                'type':'function','function':{'name':'capabilities','arguments':json.dumps({'group':group})}}]}, 'tool_calls')
        return sse({'role':'assistant','content':'需要开启当前会话的 Mac 管理。'}, 'stop')
    app = web.Application()
    app.router.add_post('/v1/chat/completions', respond)
    server = web.AppRunner(app)
    await server.setup()
    site = web.TCPSite(server, '127.0.0.1', 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    (tmp_path/'node_modules').symlink_to(ROOT/'pi/node_modules')
    for name in ('context.mjs','progress.mjs','tool-context.mjs'):
        (tmp_path/name).write_text((ROOT/'pi'/name).read_text())
    runner = tmp_path/'runner.mjs'
    runner.write_text((ROOT/'pi/runner-v2.mjs').read_text().replace('127.0.0.1:8085', f'127.0.0.1:{port}'))
    async def review(job, answer):
        return {'accepted':False, 'outcome':'blocked', 'repair':False, 'answer':answer}
    service.pi = PiAgent(engine, store, service.is_allowed, tmp_path,
        approve=service.approvals.request, runner=runner, reviewer=SimpleNamespace(review=review))
    job = service.enqueue_mobile(owner['id'], session['id'], '打开计算器并截图', 'permission-gap')
    worker = asyncio.create_task(service.worker())
    try:
        result = await settled(service, job['id'], 'succeeded')
        assert discoveries == ['computer']
        assert result['outcome']=='needs_input' and 'Mac 管理' in result['result']
        assert '可靠答复' not in result['result']
        assert not store.require_session(owner['id'],session['id'])['management']
    finally:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
        await server.cleanup()
        store.db.close()
