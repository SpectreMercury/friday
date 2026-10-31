"""No GPU/network: exercise review, recovery and per-shot prompt routing."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from local_ai.approvals import ApprovalPending
from local_ai.harness import HarnessService
from local_ai.store import Store
from local_ai.video_review import check_distinct, require_review

REFS={'character_photos':'用户选择不用角色照片：无人场景','environment_photos':'用户选择不用环境照片：纯色背景'}
BEATS=[
    ('A blue ball sits beside a table.', 'The ball rolls across the white floor.', 'The ball rests beside a door.'),
    ('A red cube hangs over a yellow box.', 'The cube drops into the box.', 'The cube rests inside the box.'),
    ('A green kite lies in a meadow.', 'The kite rises above the grass.', 'The kite floats above a tree.'),
    ('A purple door is closed in a hallway.', 'The door swings open.', 'An empty room is visible through the door.'),
]
PROGRESSION=[dict(zip(('start_state','action','end_state'),beat)) for beat in BEATS]
PROMPTS=[' '.join(beat) for beat in BEATS]


@pytest.fixture
def env(tmp_path):
    store=Store(tmp_path/'db',skills_root=tmp_path/'empty-skills')
    owner=store.create_account('owner');session=store.create_session(owner['id'],'review')
    jid=store.enqueue(owner['user_id'],None,'chat','Discuss a film',session_id=session['id'],channel='app')
    async def chat(messages,schema=None,**kwargs):
        if 'shots' not in schema['properties']:
            return json.dumps({key:'Simple objects, no characters' for key in schema['required']})
        indices=schema['properties']['shots']['items']['properties']['ordinal']['enum']
        return json.dumps({'shots':[{'ordinal':i,'summary':PROMPTS[i],'prompt':PROMPTS[i],'progression':PROGRESSION[i]} for i in indices]})
    engine=SimpleNamespace(chat=AsyncMock(side_effect=chat),render=AsyncMock(),speech_worker=None)
    service=HarnessService(tmp_path/'runtime',store=store,engine=engine)
    yield SimpleNamespace(store=store,job=store.get(jid),service=service,engine=engine,owner=owner,session=session,root=tmp_path)
    store.db.close()


def project(env):
    result=env.store.video_projects.create(env.job,'Objects','Four different scenes.',1,reference_choices=REFS)
    job=env.store.get(result['job_id']);env.store.start_run(job['id'])
    return env.store.get(job['id'])


@pytest.mark.parametrize('variant',['A blue ball rolls.', '  a BLUE ball rolls! ', '[Shot 2] A blue ball rolls.', '第二镜头：A blue ball rolls.'])
def test_repeated_prompts_including_ordinal_only_changes_are_blocked(variant):
    with pytest.raises(RuntimeError,match='重复'):
        check_distinct([{'ordinal':0,'prompt':'[Shot 1] A blue ball rolls.'},{'ordinal':1,'prompt':variant}])


def test_camera_and_costume_rewording_cannot_hide_a_repeated_story_beat():
    shots=[{'ordinal':i,'prompt':f'{camera} {PROMPTS[0]}','progression':PROGRESSION[0]}
           for i,camera in enumerate(('A wide view in soft daylight.', 'A close view with a brighter blue ball.'))]
    with pytest.raises(RuntimeError,match='剧情进展重复'):check_distinct(shots)


def test_progression_cannot_claim_actions_missing_from_the_render_prompt():
    with pytest.raises(RuntimeError,match='未落实'):
        check_distinct([{'ordinal':0,'prompt':PROMPTS[0],'progression':PROGRESSION[1]}])


def test_shared_character_descriptions_are_allowed_when_actions_advance():
    stable='A young explorer wears the same blue coat in a warmly lit room. '*20
    check_distinct([{'ordinal':i,'prompt':stable+prompt,'progression':PROGRESSION[i]}
                    for i,prompt in enumerate(PROMPTS)])


async def test_project_waits_for_actual_owner_approval_then_routes_four_distinct_prompts(env,monkeypatch):
    job=project(env);manager=env.store.video_projects
    await env.service.run_job(job)
    assert env.store.get(job['id'])['status']=='waiting_approval'
    env.engine.render.assert_not_awaited()
    assert env.store.db.execute('select count(*) from jobs where parent_job_id=?',(job['id'],)).fetchone()[0]==0
    pending=env.service.approvals.list_for_session(env.owner['id'],env.session['id'])
    assert len(pending)==1 and pending[0]['purpose']=='video'
    assert all(prompt in pending[0]['description'] for prompt in PROMPTS)
    # A normal session may approve its own creative work, never computer access.
    assert not env.session['management']
    other=env.store.create_session(env.owner['id'],'other')
    with pytest.raises(PermissionError):env.service.approvals.decide(env.owner['id'],other['id'],pending[0]['token'],'approved')
    env.service.approvals.decide(env.owner['id'],env.session['id'],pending[0]['token'],'approved')
    prompts=[]
    async def render(kind,prompt,jid,**kwargs):
        prompts.append(kwargs['workflow']['text']['inputs']['prompt'])
        assert prompts[-1]==prompt
        path=env.root/(jid+'.mp4');path.write_bytes(b'fake');return path
    env.engine.render.side_effect=render
    monkeypatch.setattr('local_ai.video_export.trim_clip',AsyncMock(side_effect=lambda path,*_:path))
    monkeypatch.setattr('local_ai.video_export.assemble_project',AsyncMock(return_value=env.root/'film.mp4'))
    await env.service.run_job(env.store.get(job['id']))
    assert env.store.get(job['id'])['status']=='succeeded'
    assert prompts==PROMPTS
    assert env.engine.chat.await_count==2
    # Resume cannot replan or ask again for an unchanged reviewed version.
    await manager.execute(env.store.get(job['id']),env.service)
    assert prompts==PROMPTS and env.engine.chat.await_count==2


async def test_duplicate_model_plan_never_submits_a_child(env):
    job=project(env)
    original=env.engine.chat.side_effect
    async def duplicates(messages,**kwargs):
        value=json.loads(await original(messages,**kwargs))
        for shot in value.get('shots',[]):shot['prompt']=PROMPTS[0]
        return json.dumps(value)
    env.engine.chat.side_effect=duplicates
    with pytest.raises(RuntimeError,match='重复'):await env.store.video_projects.execute(job,env.service)
    env.engine.render.assert_not_awaited()
    assert env.store.db.execute('select count(*) from jobs where parent_job_id=?',(job['id'],)).fetchone()[0]==0


@pytest.mark.parametrize('defect',['duplicate_prompt','repeated_progression','unbound_progression','missing_progression'])
async def test_invalid_first_plan_is_revised_before_owner_review(env,defect):
    job=project(env)
    original=env.engine.chat.side_effect
    shot_calls=0
    async def first_duplicate(messages,**kwargs):
        nonlocal shot_calls
        value=json.loads(await original(messages,**kwargs))
        if 'shots' in value:
            shot_calls+=1
            if shot_calls==1:
                if defect=='duplicate_prompt':
                    value['shots'][3]['prompt']=value['shots'][2]['prompt']
                elif defect=='repeated_progression':
                    value['shots'][3]['progression']=value['shots'][2]['progression']
                    value['shots'][3]['prompt']='A different camera angle. '+value['shots'][2]['prompt']
                elif defect=='unbound_progression':
                    value['shots'][3]['progression']=value['shots'][2]['progression']
                else:
                    value['shots'][3].pop('progression')
        return json.dumps(value)
    env.engine.chat.side_effect=first_duplicate
    await env.store.video_projects.plan(job,env.engine)
    assert shot_calls==2
    assert len(env.store.video_projects.shots(job['id']))==4
    assert env.store.db.execute('select count(*) from jobs where parent_job_id=?',(job['id'],)).fetchone()[0]==0
    env.engine.render.assert_not_awaited()


async def test_reviewed_project_rejects_changed_plan_and_wrong_child_prompt(env):
    job=project(env);manager=env.store.video_projects
    await manager.plan(job,env.engine)
    with pytest.raises(RuntimeError,match='尚未获'):manager.fill_window(job)
    await require_review(env.store,job,AsyncMock(return_value=True),manager.review_document(job))
    manager.fill_window(job)
    child=env.store.get(env.store.db.execute('select job_id from video_shots where project_id=? and ordinal=0',(job['id'],)).fetchone()[0])
    from local_ai.media_prompts import prepare
    workflow=await prepare(env.store,env.engine,child)
    assert manager.validate_child(child,workflow)
    wrong=copy.deepcopy(workflow);wrong['text']['inputs']['prompt']=PROMPTS[1]
    with pytest.raises(RuntimeError,match='不一致'):manager.validate_child(child,wrong)
    plan=manager.shots(job['id'])[0];plan['prompt']+=' A silver train crosses a bridge in the background.'
    with env.store.db:env.store.db.execute('update video_shots set plan=? where project_id=? and ordinal=0',(json.dumps(plan),job['id']))
    with pytest.raises(RuntimeError,match='已被修改'):manager.fill_window(job)
    env.engine.render.assert_not_awaited()


async def test_single_video_needs_a_readable_confirmation_but_no_photo_discussion(env,monkeypatch):
    # Photo notes are optional now; confirmation is still required before any render.
    jid=env.store.enqueue(env.job['user_id'],None,'video',PROMPTS[0],parent_job_id=env.job['id'])
    await env.service.run_job(env.store.get(jid))
    assert env.store.get(jid)['status']=='waiting_approval'
    env.engine.render.assert_not_awaited()
    pending=env.service.approvals.list_for_session(env.owner['id'],env.session['id'])[0]
    description=env.store.db.execute('SELECT description FROM approvals WHERE token=?',(pending['token'],)).fetchone()[0]
    assert description.startswith('将生成一段') and '画面描述：' in description and '{' not in description
    env.service.approvals.decide(env.owner['id'],env.session['id'],pending['token'],'denied')
    await env.service.run_job(env.store.get(jid))
    assert env.store.get(jid)['status']=='failed'
    env.engine.render.assert_not_awaited()


async def test_review_survives_database_reopen_but_never_authorizes_another_job(env):
    document={'script':'A blue ball rolls.',**REFS}
    approve=AsyncMock(return_value=True)
    await require_review(env.store,env.job,approve,document)
    reopened=Store(env.store.path,skills_root=env.root/'empty-skills')
    try:
        deny=AsyncMock(return_value=False)
        await require_review(reopened,reopened.get(env.job['id']),deny,document)
        deny.assert_not_awaited()
        jid=reopened.enqueue(env.job['user_id'],None,'chat','another request',session_id=env.session['id'],channel='app')
        with pytest.raises(RuntimeError,match='尚未获'):await require_review(reopened,reopened.get(jid),deny,document)
        deny.assert_awaited_once()
    finally:reopened.db.close()
