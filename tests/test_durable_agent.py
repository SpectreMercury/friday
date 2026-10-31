"""Actual Node Pi loop + Python host + SQLite + real file tool, fake model API.

The fake model isolates runtime correctness. It is not an intelligence benchmark.
"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web

from local_ai.engine import ROOT
from local_ai.pi_agent import PiAgent, UserTools
from local_ai.store import Store


@pytest.mark.asyncio
@pytest.mark.parametrize('crash_before_tool_result_checkpoint', [False, True])
async def test_actual_agent_restores_tool_result_without_repeating_write(tmp_path, monkeypatch, crash_before_tool_result_checkpoint):
    requests = []
    async def respond(request):
        assert engine.lock.locked(), 'Model request must own the inference resource'
        payload = await request.json()
        requests.append(payload)
        results = {m.get('tool_call_id') for m in payload['messages'] if m['role'] == 'tool'}
        if 'discover' not in results:
            name, cid, args = 'capabilities', 'discover', {}
        elif 'plan' not in results:
            name, cid, args = 'plan_task', 'plan', {'goal': '保存并读取笔记', 'mode': 'action', 'required_tools': ['write_file', 'read_file']}
        elif 'write' not in results:
            name, cid, args = 'write_file', 'write', {'name': 'note.txt', 'content': 'review-token-914'}
        elif 'read' not in results:
            name, cid, args = 'read_file', 'read', {'name': 'note.txt'}
        else:
            name = None
        delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': cid, 'type': 'function',
                 'function': {'name': name, 'arguments': json.dumps(args)}}]} if name else {
                    'role': 'assistant', 'content': '已保存 note.txt，并读回核对：review-token-914。'}
        def chunk(d, reason):
            return json.dumps({'id': 'response', 'object': 'chat.completion.chunk', 'model': 'qwen38',
                               'choices': [{'index': 0, 'delta': d, 'finish_reason': reason}]})
        return web.Response(text=f'data: {chunk(delta,None)}\n\ndata: {chunk({},"tool_calls" if name else "stop")}\n\ndata: [DONE]\n\n', content_type='text/event-stream')
    app = web.Application()
    app.router.add_post('/v1/chat/completions', respond)
    server = web.AppRunner(app)
    await server.setup()
    site = web.TCPSite(server, '127.0.0.1', 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    (tmp_path/'node_modules').symlink_to(ROOT/'pi/node_modules')
    (tmp_path/'context.mjs').write_text((ROOT/'pi/context.mjs').read_text())
    (tmp_path/'progress.mjs').write_text((ROOT/'pi/progress.mjs').read_text())
    (tmp_path/'tool-context.mjs').write_text((ROOT/'pi/tool-context.mjs').read_text())
    runner = tmp_path/'runner.mjs'
    runner.write_text((ROOT/'pi/runner-v2.mjs').read_text().replace('127.0.0.1:8085', f'127.0.0.1:{port}'))
    store = Store(tmp_path/'db')
    jid = store.enqueue(1, 1, 'chat', '把 review-token-914 保存到 note.txt，然后读回核对。')
    store.update(jid, status='running')
    engine = SimpleNamespace(lock=asyncio.Lock(), start=AsyncMock(), stop=AsyncMock())
    async def verify(job,answer):
        assert store.operation(jid,'read')['status']=='succeeded'
        return {'accepted':True,'outcome':'completed','answer':answer}
    agent = PiAgent(engine, store, lambda _: True, tmp_path, runner=runner,reviewer=SimpleNamespace(review=verify))
    original_execute, writes = UserTools.execute, []
    async def execute(self, name, args):
        assert not engine.lock.locked(), 'External tool work must not occupy the inference lease'
        if name == 'write_file':
            # The assistant's request must be durable before the write begins.
            saved = store.checkpoint(jid)
            assert any(p.get('name') == 'write_file' for p in saved[-1]['content'])
            writes.append(args)
        return await original_execute(self, name, args)
    monkeypatch.setattr(UserTools, 'execute', execute)
    save = store.save_checkpoint
    failed = False
    def checkpoint(job_id, messages):
        nonlocal failed
        if crash_before_tool_result_checkpoint and not failed and messages[-1].get('toolName') == 'write_file':
            failed = True
            raise OSError('injected persistence failure after successful tool receipt')
        return save(job_id, messages)
    monkeypatch.setattr(store, 'save_checkpoint', checkpoint)
    try:
        async with asyncio.timeout(20):
            if crash_before_tool_result_checkpoint:
                with pytest.raises(OSError):
                    await agent.run(store.get(jid))
                assert store.operation(jid, 'write')['status'] == 'succeeded'
                assert store.recover_interrupted() == [jid]
                store.update(jid, status='running')
            answer = await agent.run(store.get(jid))
        assert 'review-token-914' in answer
        assert (tmp_path/'workspaces/1/note.txt').read_text() == 'review-token-914'
        assert len(writes) == 1
        assert store.operation(jid, 'read')['status'] == 'succeeded'
        assert store.checkpoint(jid)[-1]['role'] == 'assistant'
        assert not engine.lock.locked()
        names = {t['function']['name'] for t in requests[0]['tools']}
        # Rare schemas appear only after successful host discovery.
        assert {'capabilities','plan_task'} <= names and not {'write_file','read_file'} & names
        assert {'plan_task','write_file','read_file'} <= {t['function']['name'] for t in requests[1]['tools']}
        assert 'computer_write' not in {t['function']['name'] for r in requests for t in r.get('tools', [])}
    finally:
        store.db.close()
        await server.cleanup()


@pytest.mark.asyncio
async def test_actual_pi_checks_steering_before_next_side_effect(tmp_path,monkeypatch):
    store=Store(tmp_path/'db');jid=store.enqueue(1,1,'chat','先算1+1，再保存笔记');store.update(jid,status='running')
    requested=[]
    async def respond(request):
        payload=await request.json();requested.append(payload)
        steered=any(m['role']=='user' and '不要写文件' in str(m['content']) for m in payload['messages'])
        delta={'role':'assistant','content':'1+1=2。已按你的补充停止写文件。'} if steered else {'role':'assistant','tool_calls':[
            {'index':0,'id':'calc','type':'function','function':{'name':'calculate','arguments':'{"expression":"1+1"}'}},
            {'index':1,'id':'write','type':'function','function':{'name':'write_file','arguments':'{"name":"must-not-exist.txt","content":"old plan"}'}}]}
        def chunk(d,reason):return json.dumps({'id':'s','object':'chat.completion.chunk','model':'qwen38','choices':[{'index':0,'delta':d,'finish_reason':reason}]})
        return web.Response(text=f'data: {chunk(delta,None)}\n\ndata: {chunk({},"stop" if steered else "tool_calls")}\n\ndata: [DONE]\n\n',content_type='text/event-stream')
    app=web.Application();app.router.add_post('/v1/chat/completions',respond);server=web.AppRunner(app);await server.setup()
    site=web.TCPSite(server,'127.0.0.1',0);await site.start();port=site._server.sockets[0].getsockname()[1]
    (tmp_path/'node_modules').symlink_to(ROOT/'pi/node_modules');runner=tmp_path/'runner.mjs'
    (tmp_path/'context.mjs').write_text((ROOT/'pi/context.mjs').read_text())
    (tmp_path/'progress.mjs').write_text((ROOT/'pi/progress.mjs').read_text())
    (tmp_path/'tool-context.mjs').write_text((ROOT/'pi/tool-context.mjs').read_text())
    runner.write_text((ROOT/'pi/runner-v2.mjs').read_text().replace('127.0.0.1:8085',f'127.0.0.1:{port}'))
    engine=SimpleNamespace(lock=asyncio.Lock(),start=AsyncMock(),stop=AsyncMock(),chat=AsyncMock(return_value='{"continue":true}'))
    async def review(job,answer):return {'accepted':True,'outcome':'completed','answer':answer}
    original=UserTools.execute;injected=[]
    async def execute(self,name,args):
        result=await original(self,name,args)
        if name=='calculate' and not injected:
            injected.append(store.enqueue(1,1,'chat','不要写文件，只告诉我算数结果'))
        return result
    monkeypatch.setattr(UserTools,'execute',execute)
    try:
        answer=await asyncio.wait_for(PiAgent(engine,store,lambda _:True,tmp_path,runner=runner,reviewer=SimpleNamespace(review=review)).run(store.get(jid)),15)
        assert '停止写文件' in answer
        assert not (tmp_path/'workspaces/1/must-not-exist.txt').exists()
        assert not store.operation(jid,'write')
        assert len(store.task_facts(store.get(jid))['user_requests'])==2
        assert store.pending_inputs(store.get(jid))==[]
        assert any(m['role']=='user' and '不要写文件' in str(m['content']) for m in store.checkpoint(jid))
    finally:await server.cleanup();store.db.close()


@pytest.mark.parametrize('truncated', [False, True])
@pytest.mark.parametrize('pending_work', [False, True])
async def test_real_host_execution_controls_are_independent_of_bounded_task_view(tmp_path, monkeypatch, truncated, pending_work):
    """Exercise the real host init AND Node consumer, with real view truncation."""
    store = Store(tmp_path / 'db')
    previous = store.enqueue(1, 1, 'chat', 'Prepare the multi-step work.')
    store.update(previous, status='succeeded', outcome='partial')
    jid = store.enqueue(1, 1, 'chat', 'Continue the current plan.')
    store.link_task(store.get(jid), previous)
    store.update(jid, status='running')
    items = [{'id': f'step-{index}', 'description': f'Check item {index}: ' + ('details ' * 100 if truncated else 'result'),
              'status': 'pending' if pending_work else 'completed'} for index in range(10 if truncated else 1)]
    store.revise_plan(store.get(jid), {'goal': 'Preserve the current plan', 'mode': 'action', 'work_items': items})
    payloads = []

    async def respond(request):
        payloads.append(await request.json())
        def chunk(delta, finish):
            return json.dumps({'id': 'fixture', 'object': 'chat.completion.chunk', 'model': 'qwen38',
                               'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]})
        text = 'The current plan and task association are preserved.'
        return web.Response(text=f'data: {chunk({"role":"assistant","content":text}, None)}\n\ndata: {chunk({}, "stop")}\n\ndata: [DONE]\n\n',
                            content_type='text/event-stream')

    app = web.Application()
    app.router.add_post('/v1/chat/completions', respond)
    server = web.AppRunner(app)
    await server.setup()
    site = web.TCPSite(server, '127.0.0.1', 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    (tmp_path / 'node_modules').symlink_to(ROOT / 'pi/node_modules')
    for name in ('context.mjs', 'progress.mjs', 'tool-context.mjs'):
        (tmp_path / name).write_text((ROOT / 'pi' / name).read_text())
    runner = tmp_path / 'runner.mjs'
    runner.write_text((ROOT / 'pi/runner-v2.mjs').read_text().replace('127.0.0.1:8085', f'127.0.0.1:{port}'))
    engine = SimpleNamespace(lock=asyncio.Lock(), start=AsyncMock(), stop=AsyncMock())
    async def review(job, answer):
        return {'accepted': True, 'outcome': 'completed', 'answer': answer}
    agent = PiAgent(engine, store, lambda _: True, tmp_path, runner=runner, reviewer=SimpleNamespace(review=review))
    expected_view = agent.model_task_index(store.get(jid))
    sent = []
    original_send = agent.send
    async def capture(proc, payload):
        if payload['type'] == 'init':
            sent.append(payload)
        await original_send(proc, payload)
    monkeypatch.setattr(agent, 'send', capture)
    try:
        await asyncio.wait_for(agent.run(store.get(jid)), 15)
        assert sent[0]['task'] == expected_view
        assert expected_view['complete'] is not truncated
        if truncated:
            assert expected_view['value']['truncated']
        else:
            assert expected_view['value']['already_linked']
        assert sent[0]['execution_state'] == {'task_id': previous, 'already_linked': True,
                                              'pending_plan_work': pending_work}
        assert 'continue_task' not in {tool['function']['name'] for tool in payloads[0]['tools']}
        assert store.checkpoint(jid)[-1]['harness_state']['pendingPlanWork'] is pending_work
    finally:
        await server.cleanup()
        store.db.close()


@pytest.mark.parametrize('pending_work,plan_status', [(True, 'pending'), (False, 'completed'), (False, 'pending')])
async def test_real_task_link_refreshes_plan_controls_during_execution(tmp_path, monkeypatch, pending_work, plan_status):
    """A task linked by a tool can acquire work that was absent at startup."""
    store = Store(tmp_path / 'db')
    previous = store.enqueue(1, 1, 'chat', 'Calculate the remaining total.')
    store.revise_plan(store.get(previous), {'goal': 'Calculate the total', 'mode': 'action',
        'work_items': [{'id': 'sum', 'description': 'Calculate 1+1',
                        'status': plan_status}]})
    store.update(previous, status='succeeded', outcome='partial' if pending_work else 'completed')
    jid = store.enqueue(1, 1, 'chat', 'Continue that task.')
    store.update(jid, status='running')
    requests = []

    async def respond(request):
        payload = await request.json()
        requests.append(payload)
        results = [message for message in payload['messages'] if message['role'] == 'tool']
        ids = {message['tool_call_id'] for message in results}
        status_count = sum(message['tool_call_id'].startswith('status-') for message in results)
        name = None
        if payload.get('tools'):
            if 'link' not in ids:
                cid, name, args = 'link', 'continue_task', {'job_id': previous}
            elif 'discover' not in ids:
                cid, name, args = 'discover', 'capabilities', {'group': 'tasks'}
            elif status_count < 2:
                cid, name, args = f'status-{status_count}', 'task_status', {}
            elif 'sum' not in ids:
                cid, name, args = 'sum', 'calculate', {'expression': '1+1'}
            elif 'complete-plan' not in ids:
                cid, name, args = 'complete-plan', 'plan_task', {'goal': 'Calculate the total', 'mode': 'action',
                    'work_items': [{'id': 'sum', 'description': 'Calculate 1+1', 'status': 'completed',
                                    'evidence_refs': [f'operation:{jid}:sum']}]}
        delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': cid, 'type': 'function',
            'function': {'name': name, 'arguments': json.dumps(args)}}]} if name else {
                'role': 'assistant', 'content': 'The remaining total is 2.' if pending_work else 'The prior plan is already complete.'}
        def chunk(value, finish):
            return json.dumps({'id': 'fixture', 'object': 'chat.completion.chunk', 'model': 'qwen38',
                               'choices': [{'index': 0, 'delta': value, 'finish_reason': finish}]})
        return web.Response(text=f'data: {chunk(delta, None)}\n\ndata: {chunk({}, "tool_calls" if name else "stop")}\n\ndata: [DONE]\n\n',
                            content_type='text/event-stream')

    app = web.Application()
    app.router.add_post('/v1/chat/completions', respond)
    server = web.AppRunner(app)
    await server.setup()
    site = web.TCPSite(server, '127.0.0.1', 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    (tmp_path / 'node_modules').symlink_to(ROOT / 'pi/node_modules')
    for name in ('context.mjs', 'progress.mjs', 'tool-context.mjs'):
        (tmp_path / name).write_text((ROOT / 'pi' / name).read_text())
    runner = tmp_path / 'runner.mjs'
    runner.write_text((ROOT / 'pi/runner-v2.mjs').read_text().replace('127.0.0.1:8085', f'127.0.0.1:{port}'))
    engine = SimpleNamespace(lock=asyncio.Lock(), start=AsyncMock(), stop=AsyncMock())
    async def review(job, answer):
        return {'accepted': True, 'outcome': 'completed', 'answer': answer}
    agent = PiAgent(engine, store, lambda _: True, tmp_path, runner=runner, reviewer=SimpleNamespace(review=review))
    # Exercise the tool-mediated transition rather than the optional classifier.
    monkeypatch.setattr(agent, 'resolve_task', AsyncMock())
    assert agent.execution_state(store.get(jid))['pending_plan_work'] is False
    try:
        await asyncio.wait_for(agent.run(store.get(jid)), 15)
        linked = json.loads(store.operation(jid, 'link')['result'])
        assert linked['execution_state'] == {'task_id': previous, 'already_linked': True,
                                             'pending_plan_work': pending_work}
        assert (store.operation(jid, 'sum') is not None) is pending_work
        state = store.checkpoint(jid)[-1]['harness_state']
        assert state['recoveryAttempts'] == int(pending_work)
        assert state['pendingPlanWork'] is False
        if pending_work:
            plan_result = json.loads(store.operation(jid, 'complete-plan')['result'])
            assert plan_result['work_items'][0]['status'] == 'completed', 'Plan receipts are raw host records, not bounded model views'
            assert plan_result['execution_state']['pending_plan_work'] is False
        else:
            assert not store.operation(jid, 'complete-plan')
    finally:
        await server.cleanup()
        store.db.close()
