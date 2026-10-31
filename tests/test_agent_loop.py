import asyncio
import json
import time

import pytest

import local_ai.pi_agent as pi_module
from local_ai.agent import loop as loop_module
from local_ai.agent.loop import Agent, MAX_TOOL_CALLS
from local_ai.agent.memory import Memory
from local_ai.agent.schedule import SILENT, Schedules, next_run
from local_ai.approvals import ApprovalPending
from local_ai.store import Store


class FakeEngine:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.started = []

    async def start(self, kind):
        self.started.append(kind)


def reply(content='', calls=()):
    return {'content': content, 'finish': 'stop',
            'calls': [{'id': f'call_{i}_{name}', 'name': name, 'raw': json.dumps(args, ensure_ascii=False)}
                      for i, (name, args) in enumerate(calls)]}


class Script:
    """Scripted model: each item is a reply, or a callable(request, tools) -> reply."""

    def __init__(self, *items):
        self.items, self.requests = list(items), []

    async def __call__(self, messages, tools=None, **kwargs):
        self.requests.append({'messages': messages, 'tools': [t['name'] for t in tools or []], **kwargs})
        item = self.items.pop(0)
        return item(messages, tools) if callable(item) else item


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(pi_module, 'model_status', lambda _: [])
    store = Store(tmp_path / 'db.sqlite3', skills_root=tmp_path / 'skills')
    account = store.create_account('owner', is_admin=True)
    approvals = []

    async def approve(job, description, scope=None, *, purpose='computer'):
        approvals.append((purpose, description))
        return purpose == 'schedule'

    agent = Agent(FakeEngine(), store, tmp_path, approve=approve)

    def ask(prompt, session=None):
        session = session or store.create_session(account['id'], 'test')
        jid = store.enqueue(account['user_id'], None, 'chat', prompt, session_id=session['id'], channel='app')
        store.update(jid, status='running')
        return store.get(jid), session

    yield store, agent, account, ask, approvals
    store.db.close()


def use(agent, monkeypatch, *items):
    script = Script(*items)
    monkeypatch.setattr(agent, 'complete', script)
    return script


@pytest.mark.asyncio
async def test_plain_answer_is_recorded_without_tools(env, monkeypatch):
    store, agent, account, ask, _ = env
    script = use(agent, monkeypatch, reply('你好！'))
    job, session = ask('hi')
    assert await agent.run(job) == '你好！'
    assert 'write_file' in script.requests[0]['tools'] and 'plan_task' not in script.requests[0]['tools']
    assert 'terminal' not in script.requests[0]['tools']  # closed toolsets stay hidden
    rows = store.db.execute('SELECT role,content FROM agent_messages WHERE session_id=? ORDER BY id', (session['id'],)).fetchall()
    assert [(r['role'], r['content']) for r in rows] == [('user', 'hi'), ('assistant', '你好！')]
    assert agent.engine.lock.locked() is False


@pytest.mark.asyncio
async def test_tool_calls_run_through_receipts_and_history_carries_tool_notes(env, monkeypatch):
    store, agent, account, ask, _ = env
    use(agent, monkeypatch,
        reply(calls=[('write_file', {'name': 'meeting.md', 'content': '明天下午三点开会'})]),
        reply(calls=[('read_file', {'name': 'meeting.md'})]),
        reply('已保存：明天下午三点开会。'))
    job, session = ask('把明天下午三点开会保存到 meeting.md，再读给我听。')
    assert '三点' in await agent.run(job)
    workspace = store.workspace_directory(account['id'], session['id'])
    assert (workspace / 'meeting.md').read_text() == '明天下午三点开会'
    ops = store.db.execute('SELECT name,status FROM tool_operations WHERE job_id=? ORDER BY started_at', (job['id'],)).fetchall()
    assert [(o['name'], o['status']) for o in ops] == [('write_file', 'succeeded'), ('read_file', 'succeeded')]
    script = use(agent, monkeypatch, reply('好的'))
    follow, _ = ask('改成四点', session)
    await agent.run(follow)
    history = script.requests[0]['messages']
    assert any(m['role'] == 'assistant' and 'write_file(meeting.md)' in (m['content'] or '') for m in history)


@pytest.mark.asyncio
async def test_closed_toolset_is_refused_until_enabled(env, monkeypatch):
    store, agent, account, ask, _ = env
    script = use(agent, monkeypatch,
                 reply(calls=[('drama_list', {})]),
                 reply(calls=[('enable_toolset', {'name': 'drama'})]),
                 reply('好'))
    job, _ = ask('做短剧')
    store.db.execute("UPDATE jobs SET channel='telegram' WHERE id=?", (job['id'],))
    await agent.run(store.get(job['id']))
    tool_results = [m for m in script.requests[1]['messages'] if m['role'] == 'tool']
    assert 'enable_toolset' in tool_results[0]['content']
    assert 'drama_list' in script.requests[2]['tools'] and 'generate_video_project' not in script.requests[2]['tools']


@pytest.mark.asyncio
async def test_app_asks_before_drama_joins_and_invitation_opens_it(env, monkeypatch):
    from local_ai.agent import roster
    store, agent, account, ask, _ = env
    script = use(agent, monkeypatch,
                 reply(calls=[('enable_toolset', {'name': 'drama'})]),
                 reply('可以让短剧助手加入来做分镜。'))
    job, session = ask('把这个剧本做成短剧')
    await agent.run(job)
    result = [m for m in script.requests[1]['messages'] if m['role'] == 'tool'][0]['content']
    assert 'waiting_for_user' in result
    assert 'drama_list' not in script.requests[1]['tools']
    assert roster.options(store.db, job['id'])['suggested'] == 'drama'
    assert '需要用户同意' in script.requests[0]['messages'][0]['content']

    script = use(agent, monkeypatch, reply('好的，开始做分镜。'))
    invited, _ = ask('好，让短剧助手加入', session)
    roster.save_options(store.db, invited['id'], False, ['drama'])
    await agent.run(invited)
    assert 'drama_list' in script.requests[0]['tools']
    assert script.requests[0]['thinking'] is False
    assert '请来了「短剧助手」' in script.requests[0]['messages'][0]['content']


@pytest.mark.asyncio
async def test_live_state_streams_tools_and_session_gets_a_title(env, monkeypatch):
    store, agent, account, ask, _ = env
    events = []
    session = store.create_session(account['id'], None, auto_title=True)
    assert session['name'] == '新对话' and session['auto_title'] == 1
    queue = agent.live.subscribe(account['id'], session['id'])
    script = use(agent, monkeypatch,
                 reply(calls=[('calculate', {'expression': '2+3'})]),
                 reply('结果是 5。'),
                 reply('两数相加'))
    job, _ = ask('2+3 等于几', session)
    assert await agent.run(job) == '结果是 5。'
    while not queue.empty():
        events.append(queue.get_nowait())
    kinds = [e['type'] for e in events]
    assert kinds[0] == 'start' and kinds[-1] == 'done' and 'step' in kinds
    tools = [e['tool'] for e in events if e['type'] == 'tool']
    assert tools[0]['status'] == 'running' and tools[-1]['status'] == 'done' and tools[0]['label'] == '计算'
    assert all(r.get('live') == job['id'] for r in script.requests[:2])
    renamed = store.require_session(account['id'], session['id'])
    assert renamed['name'] == '两数相加' and renamed['auto_title'] == 0
    assert agent.live.snapshot(job['id']) is None


@pytest.mark.asyncio
async def test_memory_is_saved_and_shown_in_new_sessions(env, monkeypatch, tmp_path):
    store, agent, account, ask, _ = env
    use(agent, monkeypatch, reply(calls=[('memory', {'action': 'add', 'target': 'user', 'text': '视频默认竖屏，时长 10 秒'})]), reply('记住了'))
    job, _ = ask('记住：我做视频默认要竖屏，时长 10 秒。')
    await agent.run(job)
    assert Memory(tmp_path / 'memory', account['id']).entries('user') == ['视频默认竖屏，时长 10 秒']
    script = use(agent, monkeypatch, reply('竖屏 10 秒'))
    other, _ = ask('写一个视频 prompt')
    await agent.run(other)
    assert '视频默认竖屏' in script.requests[0]['messages'][0]['content']
    with pytest.raises(ValueError):
        Memory(tmp_path / 'memory', account['id']).apply('add', 'memory', '我的密码是 hunter2')


@pytest.mark.asyncio
async def test_session_search_finds_other_sessions_of_same_account_only(env, monkeypatch):
    store, agent, account, ask, _ = env
    use(agent, monkeypatch, reply('已记录'))
    job, _ = ask('周五上午十点和设计组评审首页改版')
    await agent.run(job)
    found = agent.transcript.search(account['id'], '设计组 评审')
    assert found['results'] and '十点' in found['results'][0]['snippet']
    stranger = store.create_account('someone else')
    assert agent.transcript.search(stranger['id'], '设计组')['results'] == []
    detail = agent.transcript.read(account['id'], found['results'][0]['message_id'])
    assert any('设计组' in m['content'] for m in detail['messages'])


@pytest.mark.asyncio
async def test_schedule_needs_confirmation_and_silent_reply_constant(env, monkeypatch):
    store, agent, account, ask, approvals = env
    use(agent, monkeypatch,
        reply(calls=[('schedule', {'action': 'create', 'title': '喝水', 'prompt': '提醒我喝水', 'kind': 'daily', 'time': '08:00'})]),
        reply('已设置每天 8:00 提醒'))
    job, session = ask('每天早上 8 点提醒我喝水。')
    await agent.run(job)
    assert approvals and approvals[0][0] == 'schedule' and '每天 08:00' in approvals[0][1]
    rows = agent.schedules.list(account['id'])
    assert len(rows) == 1 and rows[0]['when'] == '每天 08:00'
    row = dict(store.db.execute('SELECT * FROM scheduled_tasks').fetchone())
    row['next_run'] = time.time() - 5
    jid = agent.schedules.fire(row)
    queued = store.get(jid)
    assert queued['session_id'] == session['id'] and '喝水' in queued['prompt']
    assert agent.schedules.list(account['id'])[0]['next_run'] is not None


def test_next_run_rules():
    base = time.mktime(time.strptime('2026-10-20 09:30', '%Y-%m-%d %H:%M'))  # a Tuesday
    fmt = lambda t: time.strftime('%Y-%m-%d %H:%M', time.localtime(t))  # noqa: E731
    assert fmt(next_run('daily', {'time': '08:00'}, base)) == '2026-10-21 08:00'
    assert fmt(next_run('daily', {'time': '10:00'}, base)) == '2026-10-20 10:00'
    assert fmt(next_run('hourly', {'minute': 0}, base)) == '2026-10-20 10:00'
    assert fmt(next_run('weekly', {'weekday': 1, 'time': '09:00'}, base)) == '2026-10-26 09:00'
    assert fmt(next_run('weekly', {'weekday': 2, 'time': '10:00'}, base)) == '2026-10-20 10:00'
    assert next_run('interval', {'minutes': 30}, base) == base + 1800


@pytest.mark.asyncio
async def test_budget_exhaustion_pauses_with_summary(env, monkeypatch):
    store, agent, account, ask, _ = env
    monkeypatch.setattr(loop_module, 'MAX_TOOL_CALLS', 2)
    script = use(agent, monkeypatch,
                 reply(calls=[('calculate', {'expression': '1+1'})]),
                 reply(calls=[('calculate', {'expression': '2+2'})]),
                 reply('做到第二步，说继续接着做。'))
    job, _ = ask('一直算')
    assert '继续' in await agent.run(job)
    assert script.requests[-1]['tools'] == []
    assert store.get(job['id'])['outcome'] == 'paused'


@pytest.mark.asyncio
async def test_announcement_with_open_todo_is_nudged_once_more(env, monkeypatch):
    store, agent, account, ask, _ = env
    script = use(agent, monkeypatch,
                 reply(calls=[('todo', {'items': [{'content': '查资料', 'status': 'in_progress'}]})]),
                 reply('好的，我来处理。'),
                 reply('查完了：结论是 X。'))
    job, _ = ask('查一下')
    assert await agent.run(job) == '查完了：结论是 X。'
    assert '未完成' in script.requests[2]['messages'][-1]['content']


@pytest.mark.asyncio
async def test_repeated_identical_results_get_a_warning(env, monkeypatch):
    store, agent, account, ask, _ = env
    same = ('calculate', {'expression': '1+1'})
    script = use(agent, monkeypatch, reply(calls=[same]), reply(calls=[same]), reply(calls=[same]), reply('2'))
    job, _ = ask('算')
    await agent.run(job)
    last_tool = [m for m in script.requests[3]['messages'] if m['role'] == 'tool'][-1]
    assert '3 次相同结果' in last_tool['content']


@pytest.mark.asyncio
async def test_approval_wait_resumes_from_checkpoint_without_repeating_work(env, monkeypatch):
    store, agent, account, ask, _ = env
    gate = {'open': False}

    async def schedule_gate(job, description, scope=None, *, purpose='computer'):
        if not gate['open']:
            raise ApprovalPending()
        return True

    agent.approve = schedule_gate
    script = use(agent, monkeypatch,
                 reply(calls=[('write_file', {'name': 'a.md', 'content': 'x'}),
                              ('schedule', {'action': 'create', 'title': 't', 'prompt': 'p', 'kind': 'interval', 'minutes': 60})]))
    job, _ = ask('写文件并定时')
    with pytest.raises(ApprovalPending):
        await agent.run(job)
    assert agent.engine.lock.locked() is False
    gate['open'] = True
    script.items.append(reply('都好了'))
    assert await agent.run(store.get(job['id'])) == '都好了'
    ops = store.db.execute("SELECT name FROM tool_operations WHERE job_id=? AND status='succeeded'", (job['id'],)).fetchall()
    assert sorted(o['name'] for o in ops) == ['schedule', 'write_file']
    assert len(script.requests) == 2  # the model was not asked again for the paused step
    assert agent.transcript.checkpoint(job['id']) == (None, None)


@pytest.mark.asyncio
async def test_cancelled_job_stops_loop(env, monkeypatch):
    store, agent, account, ask, _ = env

    def cancel(messages, tools):
        store.update(job['id'], status='cancelled')
        return reply(calls=[('calculate', {'expression': '1'})])

    use(agent, monkeypatch, cancel)
    job, _ = ask('x')
    with pytest.raises(asyncio.CancelledError):
        await agent.run(job)
    assert agent.engine.lock.locked() is False


def test_silent_marker_matches_scheduler_instruction():
    assert SILENT == '【静默】'
    assert MAX_TOOL_CALLS == 40
    assert Schedules  # imported for schema creation in other tests


@pytest.mark.asyncio
async def test_message_sent_during_work_joins_the_running_task(env, monkeypatch):
    store, agent, account, ask, _ = env
    holder = {}

    def first(messages, tools):
        later = store.enqueue(account['user_id'], None, 'chat', '顺便把结果也存成 a.md', session_id=holder['session']['id'], channel='app')
        holder['later'] = later
        return reply(calls=[('calculate', {'expression': '6*7'})])

    script = use(agent, monkeypatch, first, reply('42，已存。'))
    job, session = ask('算 6*7')
    holder['session'] = session
    await agent.run(job)
    assert any(m['role'] == 'user' and '顺便' in m['content'] for m in script.requests[1]['messages'])
    # The absorbed message is part of this turn, not replayed as older history.
    assert script.requests[1]['messages'][1] == script.requests[0]['messages'][1]
    later = store.get(holder['later'])
    assert later['status'] == 'succeeded' and later['outcome'] == 'continued' and later['delivery_state'] == 'suppressed'
    roles = [r['role'] for r in store.db.execute('SELECT role FROM agent_messages WHERE session_id=? ORDER BY id', (session['id'],))]
    assert roles == ['user', 'user', 'assistant']
    nxt = use(agent, monkeypatch, reply('ok'))
    follow, _ = ask('下一个问题', session)
    await agent.run(follow)
    history = [m['content'] for m in nxt.requests[0]['messages'][1:-1]]
    sent = [m['content'] for m in script.requests[1]['messages'] if m['role'] == 'user']
    # Earlier user messages replay exactly as the model first saw them (cache prefix).
    assert history == [*sent, '42，已存。\n\n（这一轮用过的工具：calculate(6*7)）']
    assert sent[0].startswith('算 6*7\n\n（发送时间：') and sent[1].startswith('（用户在你工作时补充）顺便')


@pytest.mark.asyncio
async def test_delegate_runs_in_a_clean_context_and_returns_only_a_report(env, monkeypatch):
    store, agent, account, ask, _ = env
    script = use(agent, monkeypatch,
                 reply(calls=[('delegate', {'task': '算 6*7 并报告', 'toolsets': ['web']})]),
                 reply(calls=[('calculate', {'expression': '6*7'})]),
                 reply('结果是 42。'),
                 reply('子任务说是 42。'))
    job, _ = ask('委托算一下')
    assert await agent.run(job) == '子任务说是 42。'
    child_request = script.requests[1]
    assert child_request['messages'][1]['content'] == '算 6*7 并报告' and len(child_request['messages']) == 2
    assert 'delegate' not in child_request['tools'] and 'memory' not in child_request['tools']
    parent_tool = [m for m in script.requests[3]['messages'] if m['role'] == 'tool'][-1]
    assert json.loads(parent_tool['content'])['report'] == '结果是 42。'
    assert store.db.execute("SELECT count(*) FROM tool_operations WHERE job_id=? AND name='calculate'", (job['id'],)).fetchone()[0] == 1


@pytest.mark.asyncio
async def test_empty_reply_cut_by_length_is_rewritten_without_thinking(env, monkeypatch):
    store, agent, account, ask, _ = env
    cut = {'content': '', 'calls': [], 'finish': 'length'}
    script = use(agent, monkeypatch, cut, reply('这是结论。'))
    job, _ = ask('长任务')
    assert await agent.run(job) == '这是结论。'
    assert script.requests[1]['thinking'] is False and script.requests[1]['tools'] == []


@pytest.mark.asyncio
async def test_streamed_reply_splits_thinking_text_and_tool_calls(env, monkeypatch):
    from aiohttp import web
    from aiohttp.test_utils import TestServer
    store, agent, account, ask, _ = env
    chunks = [{'choices': [{'delta': {'reasoning_content': '先想'}}]},
              {'choices': [{'delta': {'reasoning_content': '一下'}}]},
              {'choices': [{'delta': {'content': '<think>内联</think>你'}}]},
              {'choices': [{'delta': {'content': '好'}}]},
              {'choices': [{'delta': {'tool_calls': [{'index': 0, 'function': {'name': 'calc', 'arguments': '{"a"'}}]}}]},
              {'choices': [{'delta': {'tool_calls': [{'index': 0, 'function': {'arguments': ':1}'}}]}, 'finish_reason': 'tool_calls'}]}]
    seen = {}

    async def completions(request):
        seen.update(await request.json())
        response = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
        await response.prepare(request)
        for chunk in chunks:
            await response.write(b'data: ' + json.dumps(chunk, ensure_ascii=False).encode() + b'\n\n')
        await response.write(b'data: [DONE]\n\n')
        return response

    app = web.Application()
    app.router.add_post('/v1/chat/completions', completions)
    async with TestServer(app) as server:
        monkeypatch.setattr(loop_module, 'LLAMA_URL', str(server.make_url('')).rstrip('/'))
        job, _ = ask('hi')
        agent.live.start(job)
        result = await agent.complete([{'role': 'user', 'content': 'hi'}], thinking=False, live=job['id'])
    assert seen['stream'] is True and seen['chat_template_kwargs'] == {'enable_thinking': False}
    assert result['content'] == '你好' and result['finish'] == 'tool_calls'
    assert [(c['name'], c['raw']) for c in result['calls']] == [('calc', '{"a":1}')]
    state = agent.live.snapshot(job['id'])
    assert state['thinking'] == '先想一下内联' and state['text'] == '你好'


@pytest.mark.asyncio
async def test_malformed_tool_call_is_repaired_instead_of_failing_the_job(env, monkeypatch):
    store, agent, account, ask, _ = env
    bad = {'content': '', 'calls': [], 'finish': 'bad_tool_call'}
    cut = {'content': '', 'finish': 'length', 'calls': [{'id': 'call_x', 'name': 'calc', 'raw': '{"expression": "1+'}]}
    script = use(agent, monkeypatch, bad, cut, reply('好了'))
    job, _ = ask('长' * 2000)
    assert await agent.run(job) == '好了'
    first = script.requests[0]['messages'][-1]['content']
    assert 'source_message_id=' in first
    nudges = [m['content'] for m in script.requests[2]['messages'] if m['role'] == 'user' and '不是完整的 JSON' in m['content']]
    assert len(nudges) == 2
    assert not store.db.execute("SELECT 1 FROM tool_operations WHERE job_id=?", (job['id'],)).fetchone()


@pytest.mark.asyncio
async def test_repeated_malformed_tool_calls_end_with_an_honest_reply(env, monkeypatch):
    store, agent, account, ask, _ = env
    bad = {'content': '', 'calls': [], 'finish': 'bad_tool_call'}
    use(agent, monkeypatch, bad, bad, bad)
    job, _ = ask('做成短剧')
    assert '没有执行任何操作' in await agent.run(job)


@pytest.mark.asyncio
async def test_server_tool_call_parse_error_becomes_a_repairable_reply(env, monkeypatch):
    from aiohttp import web
    from aiohttp.test_utils import TestServer
    store, agent, account, ask, _ = env

    async def completions(request):
        return web.json_response({'error': {'code': 500, 'message': 'Failed to parse tool call arguments as JSON: parse error'}}, status=500)

    app = web.Application()
    app.router.add_post('/v1/chat/completions', completions)
    async with TestServer(app) as server:
        monkeypatch.setattr(loop_module, 'LLAMA_URL', str(server.make_url('')).rstrip('/'))
        result = await agent.complete([{'role': 'user', 'content': 'hi'}])
    assert result == {'content': '', 'calls': [], 'finish': 'bad_tool_call'}


def test_history_points_long_user_messages_at_their_id(env):
    store, agent, account, ask, _ = env
    job, session = ask('剧' * 1600)
    message_id = agent.transcript.append(job, 'user', job['prompt'])
    agent.transcript.append(job, 'assistant', '收到')
    later, _ = ask('做成短剧', session)
    history = agent.history_messages(later)
    assert f'source_message_id={message_id}' in history[0]['content']
    assert 'source_message_id' not in history[1]['content']


@pytest.mark.asyncio
async def test_invented_task_number_is_sent_back_once_then_flagged(env, monkeypatch):
    store, agent, account, ask, _ = env
    fake = reply('图片已提交排队，任务号 `7920250264`。\n\n（这一轮用过的工具：generate_image(女孩)）')
    script = use(agent, monkeypatch, fake, fake)
    job, _ = ask('给我一张图')
    answer = await agent.run(job)
    check = script.requests[1]['messages'][-1]['content']
    assert '7920250264' in check and '不存在' in check
    assert '这一轮用过的工具' not in answer and '系统核对' in answer
    stored = store.db.execute("SELECT content,tools FROM agent_messages WHERE job_id=? AND role='assistant'", (job['id'],)).fetchone()
    assert stored['tools'] is None and '这一轮用过的工具' not in stored['content']


@pytest.mark.asyncio
async def test_claiming_queued_without_any_tool_is_checked_but_real_lookups_are_not(env, monkeypatch):
    store, agent, account, ask, _ = env
    script = use(agent, monkeypatch, reply('已提交，后台生成中。'), reply('抱歉，刚才没有真正提交。'))
    job, session = ask('画一张猫')
    assert await agent.run(job) == '抱歉，刚才没有真正提交。'
    assert '没有调用任何工具' in script.requests[1]['messages'][-1]['content']

    real, _ = ask('画一张狗', session)
    store.update(real['id'], status='queued')
    script = use(agent, monkeypatch, reply(calls=[('task_status', {})]), reply(f"任务号 `{real['id']}` 排队中。"))
    job, _ = ask('进度？', session)
    assert await agent.run(job) == f"任务号 `{real['id']}` 排队中。"
    assert len(script.requests) == 2


@pytest.mark.asyncio
async def test_claiming_drawn_or_sent_without_any_tool_is_checked(env, monkeypatch):
    # Echoing an earlier "正在生成，完成后发给你" without calling anything.
    store, agent, account, ask, _ = env
    for claim in ('这张按你的描述生成好了，正在渲染，完成后自动发给你。', '这张已经画完发给你了。', '这张图已经重新推送给你了。'):
        script = use(agent, monkeypatch, reply(claim), reply('抱歉，刚才没有真正生成。'))
        job, _ = ask('图呢')
        assert await agent.run(job) == '抱歉，刚才没有真正生成。'
        assert '没有调用任何工具' in script.requests[1]['messages'][-1]['content']
        store.update(job['id'], status='succeeded')

    script = use(agent, monkeypatch, reply('要不要我把图发给你？'))
    job, _ = ask('聊聊')
    assert await agent.run(job) == '要不要我把图发给你？' and len(script.requests) == 1


def test_history_drops_tool_notes_the_model_wrote_itself(env):
    store, agent, account, ask, _ = env
    job, session = ask('画图')
    agent.transcript.append(job, 'user', '画图')
    agent.transcript.append(job, 'assistant', '好了\n\n（这一轮用过的工具：generate_image(x)）')
    later, _ = ask('呢', session)
    assert agent.history_messages(later)[1]['content'] == '好了'


@pytest.mark.asyncio
async def test_prompt_prefix_stays_identical_across_turns(env, monkeypatch, tmp_path):
    """The server reuses its cache only for an unchanged prefix: no clock in the system
    prompt, toolsets opened earlier stay open, and old turns replay verbatim."""
    store, agent, account, ask, _ = env
    first = use(agent, monkeypatch, reply(calls=[('enable_toolset', {'name': 'admin'})]), reply('好了'))
    job, session = ask('看看系统状态')
    await agent.run(job)
    monkeypatch.setattr(loop_module, 'stamp', lambda when=None: '（发送时间：之后）')
    second = use(agent, monkeypatch, reply('还在'))
    later, _ = ask('还在吗', session)
    await agent.run(later)
    before, after = first.requests[-1], second.requests[0]
    assert before['messages'][0] == after['messages'][0]
    assert '（发送时间：' not in after['messages'][0]['content']
    assert before['tools'] == after['tools'] and 'system_status' in after['tools']
    assert after['messages'][1] == before['messages'][1]
    assert after['messages'][-1]['content'] == '还在吗\n\n（发送时间：之后）'


@pytest.mark.asyncio
async def test_waiting_row_says_when_the_model_is_loading(env, monkeypatch):
    store, agent, account, ask, _ = env
    agent.engine.process, agent.engine.running = None, lambda kind: False
    seen = []
    monkeypatch.setattr(agent.live, 'status', lambda job_id, text: seen.append(text))
    use(agent, monkeypatch, reply('好'))
    job, _ = ask('hi')
    await agent.run(job)
    assert seen == ['正在唤醒本地模型，大约需要 20 秒…']


def test_looping_cuts_a_repeated_tail_to_one_copy():
    from local_ai.agent.loop import looping
    head = '开头一行内容\n第二行不同内容\n'
    pair = '第一句重复的内容很长\n第二句重复的内容很长\n'
    assert looping(head + pair * 9) == head + pair.rstrip()
    assert looping(head + '\n\n重复一行足够长\n' * 3) == head + '\n\n重复一行足够长'
    assert looping(head + pair * 2) is None
    assert looping('- 一\n- 二\n- 三') is None and looping('---\n---\n---') is None
    refrain = '开头一行内容\n' + ''.join(f'新的第{i}句不一样\n反复出现的那一句\n' for i in range(4))
    assert looping(refrain.rstrip()) == refrain.rstrip().rsplit('\n', 1)[0]
    assert looping('开头一行内容\n' + ''.join(f'新的第{i}句不一样\n反复出现的那一句\n' for i in range(3))) is None


@pytest.mark.asyncio
async def test_stream_stops_when_the_reply_loops(env, monkeypatch):
    from aiohttp import web
    from aiohttp.test_utils import TestServer
    store, agent, account, ask, _ = env
    sent = []

    async def completions(request):
        payload = await request.json()
        sent.append(payload)
        response = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
        await response.prepare(request)
        for piece in ['正常的开头内容\n'] + ['一直重复的这一句话\n'] * 50:
            chunk = {'choices': [{'delta': {'content': piece}}]}
            await response.write(b'data: ' + json.dumps(chunk, ensure_ascii=False).encode() + b'\n\n')
        return response

    app = web.Application()
    app.router.add_post('/v1/chat/completions', completions)
    async with TestServer(app) as server:
        monkeypatch.setattr(loop_module, 'LLAMA_URL', str(server.make_url('')).rstrip('/'))
        job, _ = ask('hi')
        agent.live.start(job)
        result = await agent.complete([{'role': 'user', 'content': 'hi'}], thinking=False, live=job['id'])
    assert result == {'content': '正常的开头内容\n一直重复的这一句话', 'calls': [], 'finish': 'repetition'}
    assert sent[0]['presence_penalty'] > 0 and sent[0]['top_k'] == 20
