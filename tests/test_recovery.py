import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web

from local_ai.engine import ROOT
from local_ai.evidence import review_answer
from local_ai.pi_agent import UserTools
from local_ai.store import Store
from local_ai.task_context import recent_tasks, failure_reply, resumable_research


def test_failed_task_context_keeps_request_and_actual_error(tmp_path):
    store = Store(tmp_path/'db')
    try:
        old = store.enqueue(1, 1, 'chat', '帮我在大众点评找深圳餐厅')
        store.update(old, status='failed', error='模型没有返回正文')
        UserTools(store, store.get(old), lambda _: True).record(
            'web_search', '{"query":"深圳 分项评分"}', None, '搜索通道不可用')
        other = store.enqueue(2, 2, 'chat', 'private task')
        store.update(other, status='failed')
        follow = store.enqueue(1, 1, 'chat', '为什么没能完成')
        facts = recent_tasks(store, store.get(follow))
        assert len(facts) == 1
        assert facts[0]['request'] == '帮我在大众点评找深圳餐厅'
        assert facts[0]['error'] == '模型没有返回正文'
        assert facts[0]['attempts'][0]['error'] == '搜索通道不可用'
        assert resumable_research(store,store.get(follow))['id'] == old
        messages = store.messages(1,'为什么没能完成',chat_id=1,before=store.get(follow)['created'])
        assert [m['content'] for m in messages if m['role']=='user'] == ['帮我在大众点评找深圳餐厅','为什么没能完成']
        verdict = review_answer(store, store.get(old), '')
        assert not verdict['accepted'] and '搜索通道不可用' in verdict['answer']
        # Generic "sent" claims about a research report must not trigger media repair.
        verdict = review_answer(store, store.get(old), '已发送评分报告，评分还需要补充。')
        assert verdict['accepted'] and '媒体' not in verdict['answer']
        verdict = review_answer(store, store.get(follow), '图片已经发送成功了。')
        assert not verdict['accepted'] and 'list_media' not in verdict['repair_tools']
        assert verdict['evidence']['recent_tasks'][0]['id'] == old
        verdict = review_answer(store, store.get(follow), '搜索失败了。你想让我换个方向再试一次吗？')
        assert not verdict['accepted'] and verdict['repair_tools'] == ['web_search','web_read','task_status']
        assert verdict['evidence']['task']['id'] == old
        verdict = review_answer(store, store.get(follow), '需要我现在换关键词重新搜索吗？')
        assert not verdict['accepted']
        store.reset(1)
        assert recent_tasks(store, store.get(follow)) == []
        assert resumable_research(store,store.get(follow)) is None
        assert len(store.messages(1,'新话题',chat_id=1)) == 2
    finally:
        store.db.close()


@pytest.mark.asyncio
async def test_search_failure_changes_provider_and_stops_repeating(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('search_worker', ROOT/'scripts/search_web.py')
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    seen = []
    class Search:
        def __init__(self, **kwargs): pass
        def text(self, query, **kwargs):
            seen.append(kwargs['backend'])
            if kwargs['backend'] == 'bing': raise TimeoutError()
            return [{'title':'source', 'href':'https://example.com', 'body':'score'}]
    monkeypatch.setattr(worker, 'DDGS', Search)
    result = worker.search('test')
    assert seen == ['bing', 'duckduckgo']
    assert result['results'][0]['url'] == 'https://example.com'
    import local_ai.pi_agent as module
    search = AsyncMock(side_effect=ValueError('providers unavailable'))
    monkeypatch.setattr(module, 'search_web', search)
    store = Store(tmp_path/'db')
    try:
        jid = store.enqueue(1,1,'chat','research')
        tools = UserTools(store, store.get(jid), lambda _: True)
        for query in ('one', 'two', 'three'):
            with pytest.raises(ValueError): await tools.execute('web_search', {'query':query})
        assert search.await_count == 3
    finally: store.db.close()


@pytest.mark.asyncio
async def test_worker_failure_is_specific_and_remembered(tmp_path):
    from local_ai.bot import LocalBot
    store = Store(tmp_path/'db')
    jid = store.enqueue(1,1,'chat','查餐厅评分')
    bot = LocalBot.__new__(LocalBot)
    bot.store = store
    bot.is_allowed = lambda _: True
    bot.delivery_retry = {}
    bot.last_work = 0
    bot.engine = SimpleNamespace(stop=AsyncMock(),process=None)
    bot.speech = SimpleNamespace(worker=SimpleNamespace(proc=None))
    bot.execute = AsyncMock(side_effect=RuntimeError('推理服务启动失败'))
    api = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=42)))
    task = asyncio.create_task(bot.worker(SimpleNamespace(bot=api)))
    try:
        async with asyncio.timeout(3):
            while not store.get(jid)['delivered']: await asyncio.sleep(.01)
        text = api.send_message.await_args.args[1]
        assert '查餐厅评分' in text and '推理服务启动失败' in text
        assert '请稍后重试' not in text
        assert '查餐厅评分' in str(store.messages(1,'为什么'))
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        store.db.close()


@pytest.mark.asyncio
async def test_repeated_search_results_require_reading_sources(tmp_path, monkeypatch):
    import local_ai.pi_agent as module
    search = AsyncMock(return_value={'results':[{'url':'https://example.com/shop'}]})
    monkeypatch.setattr(module,'search_web',search)
    store = Store(tmp_path/'db')
    try:
        jid = store.enqueue(1,1,'chat','查餐厅')
        tools = UserTools(store,store.get(jid),lambda _:True)
        for query in ('first','second','third'):
            await tools.execute('web_search',{'query':query})
        result=await tools.execute('web_search',{'query':'fourth'})
        assert '没有新增来源' in result['hint']
        assert search.await_count == 4
        read = AsyncMock(return_value={'text':'actual scores'})
        monkeypatch.setattr(module,'read_web',read)
        assert (await tools.execute('web_read',{'url':'https://example.com/shop'}))['text']=='actual scores'
    finally: store.db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['budget', 'provider_error', 'empty', 'recovery'])
async def test_real_pi_runner_recovers_without_replaying_tools(tmp_path, mode):
    """Use the actual Pi loop against a deterministic local streaming model."""
    requests = []
    planned = False
    async def respond(request):
        nonlocal planned
        payload = await request.json()
        planning = not planned
        if planning: planned = True
        else: requests.append(payload)
        n = len(requests)
        if mode == 'provider_error' and n == 2:
            return web.json_response({'error': {'message': 'temporary provider failure'}}, status=400)
        if planning:
            delta = {'role':'assistant','tool_calls':[{'index':0,'id':'plan','type':'function',
                     'function':{'name':'plan_task','arguments':json.dumps({'goal':'找餐厅评分','mode':'research','required_tools':['web_search']})}}]}
            finish = 'tool_calls'
        elif (mode == 'budget' and n <= 11) or (mode in ('provider_error','recovery') and n == 1):
            delta = {'role':'assistant', 'tool_calls':[{'index':0, 'id':f'call-{n}', 'type':'function',
                     'function':{'name':'web_search','arguments':json.dumps({'query':f'query-{n}'})}}]}
            finish = 'tool_calls'
        else:
            answer = '' if mode == 'empty' and n == 1 else '无法查询，请稍后重试。' if mode == 'recovery' and n == 2 else '已根据实际记录整理结果：查询失败的具体原因已保留。'
            delta = {'role':'assistant','content':answer}
            finish = 'stop'
        chunk = lambda d, f: json.dumps({'id':f'reply-{n}', 'object':'chat.completion.chunk',
                           'model':'qwen38','choices':[{'index':0,'delta':d,'finish_reason':f}]})
        return web.Response(text=f'data: {chunk(delta, None)}\n\ndata: {chunk({}, finish)}\n\ndata: [DONE]\n\n',
                            content_type='text/event-stream')
    app = web.Application()
    app.router.add_post('/v1/chat/completions', respond)
    server = web.AppRunner(app)
    await server.setup()
    site = web.TCPSite(server, '127.0.0.1', 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    (tmp_path/'node_modules').symlink_to(ROOT/'pi/node_modules')
    runner = tmp_path/'runner.mjs'
    runner.write_text((ROOT/'pi/runner.mjs').read_text().replace('127.0.0.1:8085',f'127.0.0.1:{port}'))
    proc = await asyncio.create_subprocess_exec(str(ROOT/'vendor/node'), str(runner),
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    async def send(value):
        proc.stdin.write((json.dumps(value)+'\n').encode()); await proc.stdin.drain()
    calls = []
    try:
        await send({'type':'init', 'messages':[{'role':'user','content':'找餐厅评分'}]})
        async with asyncio.timeout(15):
            while line := await proc.stdout.readline():
                event = json.loads(line)
                if event['type'] == 'tool_call':
                    if event['name']=='plan_task':
                        await send({'type':'tool_result','id':event['id'],'result':event['args']})
                        continue
                    calls.append(event)
                    await send({'type':'tool_result', 'id':event['id'], 'result':{'results':[]},
                                **({'error':'搜索通道不可用，请读取已有来源'} if mode == 'recovery' else {})})
                elif event['type'] == 'final_check':
                    assert event['answer']
                    await send({'type':'review_result','id':event['id'], 'result':{'accepted':True,'answer':event['answer']}})
                elif event['type'] == 'result':
                    assert event['answer']
                    break
                elif event['type'] == 'error': pytest.fail(str(event))
            else: pytest.fail((await proc.stderr.read()).decode())
        assert len(requests) >= 2
        if mode == 'budget':
            assert len(calls) == 11 and len(requests) == 12
            assert not requests[-1].get('tools') and requests[-1]['tool_choice'] == 'none'
        if mode == 'provider_error': assert len(calls) == 1
        if mode == 'recovery': assert len(requests) == 3
    finally:
        if proc.returncode is None: proc.terminate()
        await proc.wait()
        await server.cleanup()
