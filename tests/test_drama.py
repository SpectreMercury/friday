import json

import pytest

import local_ai.pi_agent as pi_module
from local_ai.drama.service import WINDOW, Conflict
from local_ai.drama import planner
from local_ai.store import Store

SCRIPT = """第1场 内景·咖啡馆·夜
林夏推门进店，环顾四周。
店员：快打烊了。
林夏（低声）：我等一个人。

第2场 外景·街道·夜
雨越下越大，林夏撑伞离开。
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(pi_module, 'model_status', lambda _: [])
    store = Store(tmp_path / 'db.sqlite3', skills_root=tmp_path / 'skills')
    account = store.create_account('owner', is_admin=True)
    yield store, store.dramas, account
    store.db.close()


def storyboard_rows(dramas, pid):
    """Insert two shots per scene without a model."""
    scenes = dramas.view_scenes = [dict(r) for r in dramas.db.execute('SELECT * FROM drama_scenes WHERE project_id=? ORDER BY ordinal', (pid,))]
    chars = {r['name']: r['id'] for r in dramas.db.execute('SELECT * FROM drama_characters WHERE project_id=?', (pid,))}
    with dramas.db:
        for scene in scenes:
            for i in range(2):
                dramas.db.execute('INSERT INTO drama_shots(id,project_id,scene_id,ordinal,duration,action,visual,camera,character_ids,dialogue) VALUES (?,?,?,?,?,?,?,?,?,?)',
                                  (f"{scene['id'][:6]}s{i}", pid, scene['id'], scene['ordinal'] * 1000 + i + 1, 6, f'动作{i}',
                                   f'Lin Xia does thing {i}.', 'medium shot', json.dumps([chars['林夏']]),
                                   json.dumps([{'name': '店员', 'line': '快打烊了。'}] if i == 0 else [], ensure_ascii=False)))


def finish(store, jid, ok=True, result='/tmp/x.mp4'):
    store.update(jid, status='succeeded' if ok else 'failed', result=result if ok else None, error=None if ok else '显存不足')
    store.dramas.settle(store.get(jid))


def test_import_parses_scenes_and_characters_into_its_own_session(env):
    store, dramas, account = env
    view = dramas.create(account['id'], '雨夜咖啡馆', SCRIPT)
    assert [s['heading'] for s in view['scenes']] == ['第1场 内景·咖啡馆·夜', '第2场 外景·街道·夜']
    assert sorted(c['name'] for c in view['characters']) == ['店员', '林夏']
    assert view['stage'] == 'setup' and view['project']['session_id']
    assert store.require_session(account['id'], view['project']['session_id'])['name'].startswith('短剧')
    with pytest.raises(ValueError):
        dramas.create(account['id'], 'x', 'a' * 30001)
    other = store.create_account('other')
    with pytest.raises(PermissionError):
        dramas.view(other['id'], view['project']['id'])


def test_edits_need_current_revision_and_reset_affected_shots(env):
    store, dramas, account = env
    view = dramas.create(account['id'], 't', SCRIPT)
    pid, rev = view['project']['id'], view['project']['revision']
    with pytest.raises(ValueError):
        dramas.update_project(account['id'], pid, None, {'style': 'x'})
    view = dramas.update_project(account['id'], pid, rev, {'style': 'Rainy neo-noir, teal and amber'})
    with pytest.raises(Conflict):
        dramas.update_project(account['id'], pid, rev, {'style': 'stale'})
    storyboard_rows(dramas, pid)
    shot = dramas.view(account['id'], pid)['shots'][0]
    prompt = dramas.prompt(pid, shot)
    assert prompt.startswith('Rainy neo-noir') and 'Lin Xia does thing 0.' in prompt and '店员 says: "快打烊了。"' in prompt
    lin = next(c for c in view['characters'] if c['name'] == '林夏')
    view = dramas.update_character(account['id'], pid, view['project']['revision'], lin['id'], {'appearance': 'a woman in her late 20s, short black hair, beige trench coat'})
    assert 'short black hair' in dramas.prompt(pid, view['shots'][0])
    # editing the Chinese action drops the stale English visual
    view = dramas.update_shot(account['id'], pid, view['project']['revision'], shot['id'], {'action': '林夏停下脚步'})
    edited = next(s for s in view['shots'] if s['id'] == shot['id'])
    assert edited['visual'] == '' and '林夏停下脚步' in dramas.prompt(pid, edited)


def test_draft_batch_confirm_window_settle_and_approval(env):
    store, dramas, account = env
    view = dramas.create(account['id'], 't', SCRIPT)
    pid = view['project']['id']
    storyboard_rows(dramas, pid)
    preview = dramas.submit(account['id'], pid, 'draft', 'all_pending')
    assert preview['confirm_required'] and preview['shots'] == [1, 2, 3, 4]
    assert not store.db.execute('SELECT 1 FROM drama_takes').fetchone()
    dramas.submit(account['id'], pid, 'draft', 'all_pending', confirm=True)
    queued = [r['job_id'] for r in store.db.execute('SELECT job_id FROM drama_takes WHERE job_id IS NOT NULL ORDER BY created')]
    assert len(queued) == WINDOW
    snapshot = json.loads(store.db.execute('SELECT snapshot FROM media_prompts WHERE job_id=?', (queued[0],)).fetchone()[0])
    assert snapshot['status'] == 'planned' and snapshot['workflow']['scheduler']['inputs']['steps'] == 4
    assert snapshot['workflow']['text']['inputs']['width'] == 256 and snapshot['rewritten_prompt'] == store.get(queued[0])['prompt']
    statuses = [s['status'] for s in dramas.view(account['id'], pid)['shots']]
    assert statuses == ['drafting'] * 4
    finish(store, queued[0])
    assert len([r for r in store.db.execute("SELECT 1 FROM drama_takes t JOIN jobs j ON j.id=t.job_id WHERE j.status='queued'")]) == WINDOW
    shots = dramas.view(account['id'], pid)['shots']
    assert shots[0]['status'] == 'draft_review' and shots[0]['draft_take_id']
    with pytest.raises(ValueError):
        dramas.submit(account['id'], pid, 'final', [shots[0]['id']], confirm=True)  # not approved yet
    view = dramas.update_shot(account['id'], pid, dramas.view(account['id'], pid)['project']['revision'], shots[0]['id'], {'approved': True})
    assert view['shots'][0]['status'] == 'approved'
    with pytest.raises(ValueError):  # approving without any draft needs an explicit skip
        dramas.update_shot(account['id'], pid, view['project']['revision'], shots[3]['id'], {'approved': True})
    dramas.submit(account['id'], pid, 'final', [shots[0]['id']], confirm=True)
    final = store.db.execute("SELECT * FROM drama_takes WHERE kind='final'").fetchone()
    draft = store.db.execute('SELECT * FROM drama_takes WHERE id=?', (shots[0]['draft_take_id'],)).fetchone()
    assert json.loads(final['params'])['seed'] == json.loads(draft['params'])['seed']


def test_failed_take_keeps_other_shots_going_and_reports_batch(env):
    store, dramas, account = env
    pid = dramas.create(account['id'], 't', SCRIPT)['project']['id']
    storyboard_rows(dramas, pid)
    dramas.submit(account['id'], pid, 'draft', 'all_pending', confirm=True)
    for _ in range(4):
        jid = store.db.execute("SELECT t.job_id FROM drama_takes t JOIN jobs j ON j.id=t.job_id WHERE j.status='queued' ORDER BY t.created").fetchone()[0]
        finish(store, jid, ok=(_ != 1))
    shots = dramas.view(account['id'], pid)['shots']
    assert [s['status'] for s in shots] == ['draft_review', 'planned', 'draft_review', 'draft_review']
    assert shots[1]['last_error'] == '显存不足'
    event = store.db.execute("SELECT payload FROM session_events WHERE kind='drama_batch_done'").fetchone()
    assert json.loads(event[0])['failed'] == 1
    # a retry of just that shot is allowed
    assert dramas.submit(account['id'], pid, 'draft', [shots[1]['id']])['shots'] == [2]


def test_export_needs_a_version_for_every_shot(env):
    store, dramas, account = env
    pid = dramas.create(account['id'], 't', SCRIPT)['project']['id']
    storyboard_rows(dramas, pid)
    with pytest.raises(ValueError):
        dramas.request_export(account['id'], pid, 'draft')
    dramas.submit(account['id'], pid, 'draft', 'all_pending', confirm=True)
    for _ in range(4):
        jid = store.db.execute("SELECT t.job_id FROM drama_takes t JOIN jobs j ON j.id=t.job_id WHERE j.status='queued' ORDER BY t.created").fetchone()[0]
        finish(store, jid)
    result = dramas.request_export(account['id'], pid, 'draft')
    job = store.get(result['job_id'])
    assert job['kind'] == 'drama' and json.loads(job['prompt'])['op'] == 'export'
    with pytest.raises(ValueError):
        dramas.request_export(account['id'], pid, 'final')


class FakeEngine:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    async def chat(self, messages, schema=None, max_tokens=0):
        self.calls.append(json.loads(messages[1]['content']))
        return json.dumps(self.answers.pop(0), ensure_ascii=False)


@pytest.mark.asyncio
async def test_planner_fills_settings_without_overwriting_and_storyboards_per_scene(env):
    store, dramas, account = env
    view = dramas.create(account['id'], 't', SCRIPT)
    pid = view['project']['id']
    lin = next(c for c in view['characters'] if c['name'] == '林夏')
    dramas.update_character(account['id'], pid, view['project']['revision'], lin['id'], {'appearance': 'hand written look'})
    project = dict(store.db.execute('SELECT * FROM drama_projects WHERE id=?', (pid,)).fetchone())
    engine = FakeEngine(
        {'style': 'Rainy noir', 'characters': [{'name': '林夏', 'description': '主角', 'appearance': 'model look'},
                                               {'name': '店员', 'description': '店员', 'appearance': 'barista in apron'}],
         'scenes': [{'index': 0, 'setting': 'A small cafe at night'}, {'index': 1, 'setting': 'A wet street'}]},
        {'shots': [{'action': '林夏推门', 'visual': 'Lin Xia pushes the door open.', 'camera': 'wide', 'characters': ['林夏'], 'dialogue': []},
                   {'action': '店员提醒', 'visual': 'The barista looks up.', 'camera': 'medium', 'characters': ['店员', '路人'],
                    'dialogue': [{'name': '店员', 'line': '快打烊了。'}], 'duration': 4}]},
        {'shots': [{'action': '林夏离开', 'visual': 'Lin Xia walks into the rain.', 'camera': 'wide', 'characters': ['林夏'], 'dialogue': []}]})
    await planner.analyze(dramas, project, engine)
    view = dramas.view(account['id'], pid)
    assert next(c for c in view['characters'] if c['name'] == '林夏')['appearance'] == 'hand written look'
    assert next(c for c in view['characters'] if c['name'] == '店员')['appearance'] == 'barista in apron'
    assert view['project']['style'] == 'Rainy noir' and view['scenes'][1]['setting'] == 'A wet street'
    await planner.storyboard(dramas, dict(store.db.execute('SELECT * FROM drama_projects WHERE id=?', (pid,)).fetchone()), engine)
    shots = dramas.view(account['id'], pid)['shots']
    assert [s['action'] for s in shots] == ['林夏推门', '店员提醒', '林夏离开']
    assert len(shots[1]['character_ids']) == 1  # unknown name dropped
    assert engine.calls[2]['previous_shots'] == ['林夏推门', '店员提醒']


def test_titles_and_status_words_are_human_and_drama_sessions_are_hidden(env):
    from local_ai.job_presentation import job_title, status_text, hidden_sessions_sql
    store, dramas, account = env
    pid = dramas.create(account['id'], '雨夜', SCRIPT)['project']['id']
    plan = dramas.request_plan(account['id'], pid, 'storyboard')
    assert job_title(store, store.get(plan['job_id'])) == '短剧「雨夜」· 生成分镜'
    chat = store.create_session(account['id'], 'chat')
    jid = store.enqueue(account['user_id'], None, 'chat', '[定时任务「喝水」，每天 08:00] 提醒我喝水', session_id=chat['id'], channel='app')
    assert job_title(store, store.get(jid)) == '定时 · 喝水'
    child = store.enqueue(account['user_id'], None, 'video', 'integrated_multimodal_description: ' + 'x' * 900, parent_job_id=jid)
    assert job_title(store, store.get(child)).startswith('生成视频 · [定时任务')
    assert status_text({'status': 'succeeded', 'outcome': 'paused'}) == '已暂停'
    assert status_text({'status': 'succeeded', 'outcome': 'continued'}) == '已并入上一条'
    assert status_text({'status': 'succeeded', 'outcome': 'drama'}) == '已完成'
    visible = [r[0] for r in store.db.execute(f"SELECT id FROM jobs WHERE account_id=? AND {hidden_sessions_sql()}", (account['id'],))]
    assert plan['job_id'] not in visible and jid in visible


def test_prose_without_blank_lines_splits_into_pieces_that_cover_the_text():
    from local_ai.drama import parser
    prose = '\n'.join('她走到海边，看了很久。浪退下去又涌上来，' * 3 for _ in range(60))
    for text in (prose, prose.replace('\n', ''), prose.replace('\n', '\r\n')):
        result = parser.parse(text)
        spans = [(s['source_start'], s['source_end']) for s in result['scenes']]
        assert result['format'] == 'plain' and len(spans) > 1
        assert max(len(s['text']) for s in result['scenes']) < 1400
        assert spans[0][0] == 0 and spans[-1][1] == len(text)
        assert all(a[1] == b[0] for a, b in zip(spans, spans[1:]))


@pytest.mark.asyncio
async def test_import_takes_a_long_script_by_message_reference(env):
    from local_ai.agent.transcript import Transcript
    from local_ai.drama.service import register
    from local_ai.capabilities import CapabilityRegistry as Registry
    store, dramas, account = env
    session = store.create_session(account['id'], 'chat')
    other = store.create_session(account['id'], 'other')
    jid = store.enqueue(account['user_id'], None, 'chat', SCRIPT, session_id=session['id'], channel='app')
    job = store.get(jid)
    message_id = Transcript(store.db).append(job, 'user', SCRIPT)
    registry = Registry()
    register(dramas, registry, job, None)
    handler = registry.entries['drama_import'][1]
    result = await handler('drama_import', {'title': 't', 'source_message_id': message_id})
    assert [s['heading'] for s in result['scenes']] == ['第1场 内景·咖啡馆·夜', '第2场 外景·街道·夜']
    with pytest.raises(ValueError):
        await handler('drama_import', {'title': 't', 'source_message_id': message_id, 'script': 'x'})
    foreign = Transcript(store.db).append({**job, 'session_id': other['id']}, 'user', SCRIPT)
    with pytest.raises(PermissionError):
        await handler('drama_import', {'title': 't', 'source_message_id': foreign})


@pytest.mark.asyncio
async def test_planner_retries_a_cut_off_json_answer_once_with_more_room(env):
    from local_ai.engine import OutputTruncated
    budgets = []

    class Truncating:
        async def chat(self, messages, schema=None, max_tokens=0):
            budgets.append(max_tokens)
            if len(budgets) == 1:
                raise OutputTruncated('cut')
            return json.dumps({'shots': []})

    assert await planner.ask(Truncating(), 's', {}, {'type': 'object'}, 100) == {'shots': []}
    assert budgets == [100, 200]

    class Broken:
        async def chat(self, messages, schema=None, max_tokens=0):
            return '{"shots": ['

    with pytest.raises(RuntimeError, match='结构化结果'):
        await planner.ask(Broken(), 's', {}, {'type': 'object'}, 100)


def test_deleted_project_leaves_the_list_and_purge_removes_its_rows(env):
    store, dramas, account = env
    view = dramas.create(account['id'], '雨夜咖啡馆', SCRIPT)
    pid, sid = view['project']['id'], view['project']['session_id']
    storyboard_rows(dramas, pid)
    store.delete_session(account['id'], sid)
    assert dramas.list(account['id']) == []
    with pytest.raises(PermissionError):
        dramas.view(account['id'], pid)
    assert store.deleted_sessions(account['id'])[0] | {'deleted': 0, 'expires': 0} == {
        'id': sid, 'name': '雨夜咖啡馆', 'kind': 'drama', 'deleted': 0, 'expires': 0}
    store.restore_session(account['id'], sid)
    assert [p['id'] for p in dramas.list(account['id'])] == [pid]
    store.delete_session(account['id'], sid)
    store.purge_session(account['id'], sid)
    for table in ('drama_projects', 'drama_scenes', 'drama_shots', 'drama_characters'):
        column = 'id' if table == 'drama_projects' else 'project_id'
        assert not store.db.execute(f'SELECT 1 FROM {table} WHERE {column}=?', (pid,)).fetchone()
