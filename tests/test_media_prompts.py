"""Prompt adaptation stays local, session-bound, durable and reproducible.

Real Store and Harness execution; model/network/GPU boundaries are fake.
"""
import asyncio
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from local_ai import engine as engine_module
from local_ai import media_prompts
from local_ai.engine import Engine
from local_ai.harness import HarnessService
from local_ai.store import Store


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    root=tmp_path/'project';(root/'workflows').mkdir(parents=True)
    for kind in ('image','video'):
        (root/'workflows'/f'{kind}.json').write_text((engine_module.ROOT/'workflows'/f'{kind}.json').read_text())
    (root/'models.lock.json').write_text(json.dumps([
        {'key':key,'repo':'test/repo','file':key+'.bin','sha256':key+'-digest'}
        for key in ('chat','image','image_encoder','video','video_encoder','audio_vae')]))
    monkeypatch.setattr(engine_module,'ROOT',root)
    monkeypatch.setattr(media_prompts,'ROOT',root)
    skills=tmp_path/'skills';(skills/'disabled').mkdir(parents=True)
    (skills/'disabled'/'SKILL.md').write_text('DISABLED_SKILL_SECRET_DO_NOT_LOAD')
    for name in ('image-prompting','h3-prompt-writing'):
        (skills/name).mkdir()
        (skills/name/'SKILL.md').write_text(name+'\nVERSION ONE\nWrite a concise plain-text creative prompt.')
    store=Store(tmp_path/'test.db',skills_root=skills)
    account=store.create_account('Owner')
    first=store.create_session(account['id'],'A',skills=['image-prompting','h3-prompt-writing'])
    second=store.create_session(account['id'],'B')
    env=SimpleNamespace(root=root,store=store,account=account,first=first,second=second,skills=skills)
    yield env
    store.db.close()


@pytest.fixture
def no_expansion(monkeypatch):
    """Isolate skill gating from the automatic short-brief expansion."""
    monkeypatch.setattr(media_prompts,'SIMPLE_BRIEF_UNITS',{'image':0,'video':0})


def enqueue(env,kind='image',prompt='A flat blue F logo with the text “Friday”.',**kwargs):
    kwargs.setdefault('_video_references', {'character_photos':'用户选择不用','environment_photos':'用户选择不用'} if kind=='video' else None)
    jid=env.store.enqueue(env.account['user_id'],None,kind,prompt,session_id=env.first['id'],channel='app',**kwargs)
    return env.store.get(jid)


def snapshot(store,jid):
    return json.loads(store.db.execute('SELECT snapshot FROM media_prompts WHERE job_id=?',(jid,)).fetchone()[0])


def answer(text):
    return json.dumps({'rewritten_prompt':text},ensure_ascii=False)


def test_enqueue_freezes_raw_policy_workflow_models_and_seed_without_inference(scenario):
    env=scenario;job=enqueue(env,request_id='same-input')
    captured=snapshot(env.store,job['id'])
    assert captured['raw_prompt']==job['prompt'] and captured['status']=='pending'
    assert captured['version']=='media-prompt-v2'
    skill=(env.skills/'image-prompting'/'SKILL.md').read_text()
    assert captured['skill']=={'name':'image-prompting','text':skill,'version':hashlib.sha256(skill.encode()).hexdigest()}
    assert skill in captured['system_prompt'] and captured['system_prompt'].endswith(media_prompts.OUTPUT_PROTOCOL)
    assert 'h3-prompt-writing' not in captured['system_prompt']
    assert captured['workflow']['text']['inputs']['prompt']==job['prompt']
    assert captured['workflow']['sample']['inputs']['seed']==captured['seed']
    assert {entry['key'] for entry in captured['models']}=={'chat','image','image_encoder'}
    assert captured['workflow']['save']['inputs']['filename_prefix'].endswith(job['id']+'/image')
    duplicate=enqueue(env,request_id='same-input')
    assert duplicate['id']==job['id']
    assert snapshot(env.store,job['id'])==captured
    queued=env.store.db.execute('SELECT text FROM task_inputs WHERE job_id=?',(job['id'],)).fetchone()[0]
    assert queued==job['prompt']


async def test_persisted_adaptation_reuses_exact_graph_after_restart_and_template_change(scenario,monkeypatch):
    env=scenario;job=enqueue(env)
    model=SimpleNamespace(chat=AsyncMock(return_value=answer('A restrained flat blue F, with “Friday” below it.')))
    first=await media_prompts.prepare(env.store,model,job)
    captured=snapshot(env.store,job['id'])
    assert captured['status']=='enhanced' and captured['prepared_at']>0
    assert env.store.get(job['id'])['prompt']==job['prompt']
    path=env.store.path;env.store.db.close()
    (env.root/'workflows'/'image.json').write_text('{}')
    (env.root/'models.lock.json').write_text('[]')
    monkeypatch.setattr(media_prompts,'OUTPUT_PROTOCOL','NEW OUTPUT PROTOCOL')
    (env.skills/'image-prompting'/'SKILL.md').write_text('VERSION TWO AFTER RESTART')
    reopened=Store(path,skills_root=env.skills)
    try:
        recovered=await media_prompts.prepare(reopened,model,reopened.get(job['id']))
        assert recovered==first
        assert snapshot(reopened,job['id'])==captured
        model.chat.assert_awaited_once()
        # Callers cannot mutate the durable graph accidentally.
        recovered['sample']['inputs']['seed']=-1
        again=await media_prompts.prepare(reopened,model,reopened.get(job['id']))
        assert again==first
    finally:
        reopened.db.close()


async def test_pending_job_keeps_enqueue_policy_and_settings_when_templates_change(scenario,monkeypatch):
    env=scenario;job=enqueue(env)
    captured=snapshot(env.store,job['id'])
    graph=json.loads((env.root/'workflows'/'image.json').read_text())
    graph['latent']['inputs']['width']=512
    (env.root/'workflows'/'image.json').write_text(json.dumps(graph))
    (env.skills/'image-prompting'/'SKILL.md').write_text('image-prompting\nVERSION TWO')
    monkeypatch.setattr(media_prompts,'OUTPUT_PROTOCOL','UNRELATED_NEW_PROTOCOL')
    model=SimpleNamespace(chat=AsyncMock(return_value=answer('A flat F. “Friday” appears beneath it.')))
    frozen=await media_prompts.prepare(env.store,model,job)
    assert model.chat.call_args.args[0][0]['content']==captured['system_prompt']
    assert 'VERSION ONE' in model.chat.call_args.args[0][0]['content']
    assert 'VERSION TWO' not in model.chat.call_args.args[0][0]['content']
    assert frozen['latent']['inputs']['width']==captured['workflow']['latent']['inputs']['width']
    newer=enqueue(env,prompt='A circle')
    assert snapshot(env.store,newer['id'])['workflow']['latent']['inputs']['width']==512
    assert 'VERSION TWO' in snapshot(env.store,newer['id'])['system_prompt']
    assert snapshot(env.store,newer['id'])['skill']['version']!=captured['skill']['version']


@pytest.mark.parametrize('field',['account_id','session_id'])
async def test_cross_account_or_session_snapshot_access_is_rejected(scenario,field):
    env=scenario;job=enqueue(env);forged={**job,field:'foreign-resource'}
    model=SimpleNamespace(chat=AsyncMock())
    with pytest.raises(PermissionError,match='归属'):
        await media_prompts.prepare(env.store,model,forged)
    model.chat.assert_not_awaited()
    assert snapshot(env.store,job['id'])['status']=='pending'


async def test_preparation_reads_no_skills_history_memory_or_other_session(scenario,monkeypatch):
    env=scenario;job=enqueue(env)
    env.store.remember(env.account['user_id'],'SECRET_OTHER_SESSION','SECRET_ANSWER',session_id=env.second['id'])
    for method in ('capture_skills','skill_catalog','history','task_facts'):
        monkeypatch.setattr(env.store,method,Mock(side_effect=AssertionError('No skill/context reads')),raising=False)
    original_read=Path.read_text
    def guarded_read(path,*args,**kwargs):
        if path.is_relative_to(env.skills):
            raise AssertionError('Disabled skills must not be read')
        return original_read(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',guarded_read)
    model=SimpleNamespace(chat=AsyncMock(return_value=answer('Flat blue F with “Friday”.')))
    await media_prompts.prepare(env.store,model,job)
    messages=model.chat.call_args.args[0]
    assert [message['role'] for message in messages]==['system','user']
    body=json.loads(messages[1]['content'])
    assert set(body)=={'brief','render'} and body['brief']==job['prompt']
    assert 'SECRET' not in json.dumps(messages)
    assert model.chat.call_args.kwargs['schema']==media_prompts.SCHEMA


@pytest.mark.parametrize('failure',[RuntimeError('private model response'),TimeoutError(),ValueError('invalid')])
async def test_model_failure_durably_falls_back_without_rewriting_user_input(scenario,failure,caplog):
    env=scenario;job=enqueue(env)
    model=SimpleNamespace(chat=AsyncMock(side_effect=failure))
    graph=await media_prompts.prepare(env.store,model,job)
    assert graph['text']['inputs']['prompt']==job['prompt']
    stored=snapshot(env.store,job['id'])
    assert stored['status']=='original' and stored['fallback']==type(failure).__name__
    assert env.store.get(job['id'])['prompt']==job['prompt']
    assert job['prompt'] not in caplog.text and 'private model response' not in caplog.text
    assert await media_prompts.prepare(env.store,model,job)==graph
    model.chat.assert_awaited_once()


@pytest.mark.parametrize('invalid',['not JSON','[]','{}',answer(''),answer('x'*6001),answer('Changed the requested lettering')])
async def test_invalid_or_lost_lettering_uses_raw_brief(scenario,invalid):
    env=scenario;job=enqueue(env)
    model=SimpleNamespace(chat=AsyncMock(return_value=invalid))
    graph=await media_prompts.prepare(env.store,model,job)
    assert graph['text']['inputs']['prompt']==job['prompt']
    assert snapshot(env.store,job['id'])['status']=='original'


async def test_cancellation_propagates_and_leaves_pending_snapshot_recoverable(scenario):
    env=scenario;job=enqueue(env)
    model=SimpleNamespace(chat=AsyncMock(side_effect=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        await media_prompts.prepare(env.store,model,job)
    assert snapshot(env.store,job['id'])['status']=='pending'
    model.chat=AsyncMock(return_value=answer('Flat blue F with “Friday”.'))
    graph=await media_prompts.prepare(env.store,model,job)
    assert graph['text']['inputs']['prompt']=='Flat blue F with “Friday”.'


async def test_legacy_jobs_keep_original_path_without_silent_new_adaptation(scenario):
    env=scenario;job=enqueue(env)
    with env.store.db:
        env.store.db.execute('DELETE FROM media_prompts WHERE job_id=?',(job['id'],))
    model=SimpleNamespace(chat=AsyncMock())
    assert await media_prompts.prepare(env.store,model,job) is None
    model.chat.assert_not_awaited()


@pytest.mark.parametrize('kind,hd,first_frame',[('image',False,False),('video',False,False),('video',True,True)])
def test_settings_match_actual_frozen_workflow(scenario,kind,hd,first_frame):
    job=enqueue(scenario,kind=kind,hd=hd,image='first-frame.png' if first_frame else None)
    saved=snapshot(scenario.store,job['id']);settings=media_prompts.settings(job,saved)
    if kind=='image':
        assert settings=={'mode':'text_to_image','width':1024,'height':1024}
    else:
        params=saved['workflow']['text']['inputs']
        assert settings['mode']=='FL2VA' and settings['first_frame'] is first_frame
        assert settings['width']==params['width'] and settings['height']==params['height']
        assert settings['frames']==params['length'] and settings['fps']==saved['workflow']['video']['inputs']['fps']
        assert settings['duration_seconds']==round(params['length']/settings['fps'],2)
        if hd:
            assert (settings['width'],settings['height'])==(480,864)
        if first_frame:
            assert params['first_frame']==['input_image',0]
            assert saved['workflow']['input_image']['inputs']['image']=='first-frame.png'
        assert {entry['key'] for entry in saved['models']}=={'chat','video','video_encoder','audio_vae'}


@pytest.mark.parametrize('kind',['image','video'])
async def test_harness_renders_only_after_durable_preparation_and_passes_frozen_graph(scenario,kind,monkeypatch):
    env=scenario;job=enqueue(env,kind=kind)
    captured=snapshot(env.store,job['id']);raw=job['prompt'];output=env.root/'output.bin'
    monkeypatch.setattr('local_ai.video_export.trim_clip',AsyncMock(return_value=output))
    model=SimpleNamespace(chat=AsyncMock(return_value=answer('A flat blue F and “Friday”.')))
    async def render(*args,**kwargs):
        assert args==(kind,raw,job['id'])
        prepared=snapshot(env.store,job['id'])
        assert prepared['status']=='enhanced'  # Crash recovery can reuse before GPU starts.
        expected=copy.deepcopy(captured['workflow'])
        expected['text']['inputs']['prompt']=prepared['rewritten_prompt']
        assert kwargs['workflow']==expected
        assert kwargs['image']==job['image'] and kwargs['hd']==bool(job['hd'])
        return output
    model.render=AsyncMock(side_effect=render)
    harness=HarnessService(env.root/'runtime',store=env.store,engine=model)
    harness.approvals.request=AsyncMock(return_value=True)
    assert await harness.execute(job)==str(output)
    assert await harness.execute(job)==str(output)
    model.chat.assert_awaited_once()
    assert model.render.await_count==2 and env.store.get(job['id'])['prompt']==raw


async def test_engine_submits_supplied_workflow_without_rebuilding_it(tmp_path,monkeypatch):
    output=tmp_path/'comfy'/'output';output.mkdir(parents=True)
    artifact=output/'result.png';artifact.write_bytes(b'not decoded by engine')
    runtime=tmp_path/'runtime';runtime.mkdir()
    monkeypatch.setattr(engine_module,'COMFY',output.parent)
    monkeypatch.setattr(engine_module,'RUNTIME',runtime)
    monkeypatch.setattr(engine_module,'build_workflow',Mock(side_effect=AssertionError('Frozen workflow must not be rebuilt')))
    graph={'frozen':{'inputs':{'seed':42}}};sent=[]
    class Response:
        status=200
        def __init__(self,value): self.value=value
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def json(self): return self.value
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        def post(self,url,**kwargs):
            sent.append(kwargs['json'])
            return Response({'prompt_id':'p1'})
        def get(self,url):
            return Response({'p1':{'status':{'completed':True},'outputs':{'save':{'images':[{'filename':'result.png'}]}}}})
    monkeypatch.setattr(engine_module.aiohttp,'ClientSession',lambda **kwargs:Client())
    engine=Engine();engine.start=AsyncMock();engine.process=SimpleNamespace(returncode=None)
    result=await engine.render('image','raw prompt','task',workflow=graph)
    assert result==artifact and sent==[{'prompt':graph,'client_id':'task'}]
    assert not engine.lock.locked() and not engine._media_running


def test_failed_snapshot_capture_does_not_leave_half_enqueued_task(scenario):
    env=scenario
    (env.root/'models.lock.json').unlink()
    with pytest.raises(FileNotFoundError):
        enqueue(env,request_id='failed-request')
    env.store.db.commit()  # An unrelated later write must not persist an orphan.
    for table in ('jobs','tasks','task_inputs','task_skills','mobile_requests','media_prompts'):
        assert env.store.db.execute('SELECT count(*) FROM '+table).fetchone()[0]==0,table
    assert env.store.db.execute("SELECT count(*) FROM session_events WHERE kind='message_created'").fetchone()[0]==0


@pytest.mark.parametrize('kind',['image','video'])
async def test_disabled_media_skill_never_reads_library_or_calls_chat(scenario,no_expansion,monkeypatch,kind):
    env=scenario
    original_read=Path.read_text
    def guarded_read(path,*args,**kwargs):
        if path.is_relative_to(env.skills):
            raise AssertionError('An empty session must not load skill bodies')
        return original_read(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',guarded_read)
    jid=env.store.enqueue(env.account['user_id'],None,kind,'Keep this exact raw brief',
                          session_id=env.second['id'],channel='app')
    model=SimpleNamespace(chat=AsyncMock(side_effect=AssertionError('Disabled skill must not call model')))
    graph=await media_prompts.prepare(env.store,model,env.store.get(jid))
    saved=snapshot(env.store,jid)
    assert saved['status']=='original' and saved['fallback']=='skill_not_enabled'
    assert saved['skill'] is None and saved['system_prompt'] is None
    assert graph['text']['inputs']['prompt']=='Keep this exact raw brief'
    model.chat.assert_not_awaited()


async def test_only_matching_enabled_skill_enters_model_context(scenario,no_expansion):
    env=scenario
    current=env.store.require_session(env.account['id'],env.first['id'])
    env.store.update_session(env.account['id'],current['id'],current['revision'],skill_names=['h3-prompt-writing'])
    image=enqueue(env)
    model=SimpleNamespace(chat=AsyncMock(return_value=answer('Flat blue F with “Friday”.')))
    await media_prompts.prepare(env.store,model,image)
    model.chat.assert_not_awaited()
    video=enqueue(env,kind='video')
    await media_prompts.prepare(env.store,model,video)
    system=model.chat.call_args.args[0][0]['content']
    assert 'h3-prompt-writing' in system and 'image-prompting' not in system
    assert snapshot(env.store,image['id'])['skill'] is None


async def test_enabling_later_does_not_rewrite_already_queued_raw_task(scenario,no_expansion):
    env=scenario
    jid=env.store.enqueue(env.account['user_id'],None,'image','original blue circle',
                          session_id=env.second['id'],channel='app')
    current=env.store.require_session(env.account['id'],env.second['id'])
    env.store.update_session(env.account['id'],current['id'],current['revision'],skill_names=['image-prompting'])
    model=SimpleNamespace(chat=AsyncMock(return_value=answer('blue circle')))
    first=await media_prompts.prepare(env.store,model,env.store.get(jid))
    assert first['text']['inputs']['prompt']=='original blue circle'
    model.chat.assert_not_awaited()
    next_id=env.store.enqueue(env.account['user_id'],None,'image','next blue circle',
                              session_id=env.second['id'],channel='app')
    await media_prompts.prepare(env.store,model,env.store.get(next_id))
    model.chat.assert_awaited_once()


async def test_disabling_or_removing_skill_does_not_change_pending_snapshot(scenario,no_expansion):
    env=scenario;job=enqueue(env)
    current=env.store.require_session(env.account['id'],env.first['id'])
    env.store.update_session(env.account['id'],current['id'],current['revision'],skill_names=[])
    (env.skills/'image-prompting'/'SKILL.md').unlink()
    model=SimpleNamespace(chat=AsyncMock(return_value=answer('Flat blue F with “Friday”.')))
    await media_prompts.prepare(env.store,model,job)
    assert 'VERSION ONE' in model.chat.call_args.args[0][0]['content']
    assert snapshot(env.store,job['id'])['status']=='enhanced'
    newer=enqueue(env)
    assert snapshot(env.store,newer['id'])['status']=='original'


async def test_old_v1_pending_snapshot_keeps_its_policy_without_new_skill_lookup(scenario,monkeypatch):
    env=scenario;job=enqueue(env)
    old=snapshot(env.store,job['id'])
    old.update(version='media-prompt-v1',system_prompt='OLD V1 EMBEDDED INSTRUCTION',status='pending')
    old.pop('skill')
    with env.store.db:
        env.store.db.execute('UPDATE media_prompts SET snapshot=? WHERE job_id=?',(json.dumps(old),job['id']))
    (env.skills/'image-prompting'/'SKILL.md').unlink()
    (env.root/'models.lock.json').unlink()
    monkeypatch.setattr(media_prompts,'OUTPUT_PROTOCOL','NEW V2 PROTOCOL')
    media_prompts.capture(env.store,job)  # Recapture must not touch or upgrade it.
    assert snapshot(env.store,job['id'])==old
    model=SimpleNamespace(chat=AsyncMock(return_value=answer('Flat blue F with “Friday”.')))
    first=await media_prompts.prepare(env.store,model,job)
    assert model.chat.call_args.args[0][0]['content']=='OLD V1 EMBEDDED INSTRUCTION'
    assert snapshot(env.store,job['id'])['version']=='media-prompt-v1'
    assert await media_prompts.prepare(env.store,model,job)==first
    model.chat.assert_awaited_once()


def test_missing_enabled_skill_falls_back_without_implicit_enabling(scenario,no_expansion):
    env=scenario
    (env.skills/'image-prompting'/'SKILL.md').unlink()
    job=enqueue(env)
    assert snapshot(env.store,job['id'])['status']=='original'
    assert snapshot(env.store,job['id'])['skill'] is None
    assert env.store.require_session(env.account['id'],env.second['id'])['skill_names']==[]


def test_new_telegram_session_starts_with_no_implicitly_enabled_skills(scenario):
    current=scenario.store.telegram_session(7357,7357)
    assert current['skill_names']==[]


def test_capture_skills_respects_outer_transaction_and_commits_when_standalone(scenario):
    env=scenario;job=enqueue(env)
    before=env.store.db.execute('SELECT snapshot FROM task_skills WHERE task_id=?',(job['task_id'],)).fetchone()[0]
    (env.skills/'image-prompting'/'SKILL.md').write_text('image-prompting\nVERSION TWO')
    with pytest.raises(RuntimeError,match='rollback'):
        with env.store.db:
            env.store.db.execute('DELETE FROM task_skills WHERE task_id=?',(job['task_id'],))
            updated=env.store.capture_skills(job['id'])
            assert 'VERSION TWO' in updated['image-prompting']['text']
            assert env.store.db.in_transaction
            raise RuntimeError('rollback')
    assert env.store.db.execute('SELECT snapshot FROM task_skills WHERE task_id=?',(job['task_id'],)).fetchone()[0]==before
    with env.store.db:
        env.store.db.execute('DELETE FROM task_skills WHERE task_id=?',(job['task_id'],))
    env.store.capture_skills(job['id'])
    assert not env.store.db.in_transaction


def test_skills_root_prefers_explicit_then_environment_then_seed(tmp_path,monkeypatch):
    external=tmp_path/'independent';explicit=tmp_path/'explicit'
    monkeypatch.setenv('FRIDAY_SKILLS_DIR',str(external))
    store=Store(tmp_path/'environment.db')
    try:
        assert store.skills_root==external
    finally:
        store.db.close()
    store=Store(tmp_path/'explicit.db',skills_root=explicit)
    try:
        assert store.skills_root==explicit
    finally:
        store.db.close()
    monkeypatch.setenv('FRIDAY_SKILLS_DIR','~/.friday/skills')
    store=Store(tmp_path/'expanded.db')
    try:
        assert store.skills_root==Path('~/.friday/skills').expanduser()
    finally:
        store.db.close()
    monkeypatch.delenv('FRIDAY_SKILLS_DIR')
    store=Store(tmp_path/'seed.db')
    try:
        assert store.skills_root==Path(__file__).resolve().parents[1]/'assistant-skills'
    finally:
        store.db.close()

async def test_media_kinds_share_process_without_bypassing_model_or_voice_checks(monkeypatch):
    engine=Engine();engine.process=SimpleNamespace(returncode=None);engine.kind='image'
    engine.stop=AsyncMock()
    monkeypatch.setattr(engine_module,'model_status',lambda _:[])
    await engine.start('video')
    assert engine.kind=='video'
    engine.stop.assert_not_awaited()
    monkeypatch.setattr(engine_module,'model_status',lambda _:['invalid model'])
    with pytest.raises(RuntimeError,match='invalid model'):await engine.start('image')
    engine._voice_owner='active-call'
    with pytest.raises(RuntimeError,match='语音'):await engine.start('image')


@pytest.mark.parametrize('kind,prompt',[('image','一只猫'),('image','A red apple'),('video','女孩在海边跑步')])
async def test_short_brief_is_expanded_without_skill_or_library_read(scenario,monkeypatch,kind,prompt):
    env=scenario
    original_read=Path.read_text
    def guarded_read(path,*args,**kwargs):
        if path.is_relative_to(env.skills):
            raise AssertionError('Expansion must not load skill bodies')
        return original_read(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',guarded_read)
    jid=env.store.enqueue(env.account['user_id'],None,kind,prompt,session_id=env.second['id'],channel='app')
    saved=snapshot(env.store,jid)
    assert saved['status']=='pending' and saved['expansion'] is True and saved['skill'] is None
    assert media_prompts.EXPANSION_GUIDANCE[kind] in saved['system_prompt']
    assert saved['system_prompt'].endswith(media_prompts.OUTPUT_PROTOCOL)
    model=SimpleNamespace(chat=AsyncMock(return_value=answer('A detailed, expanded scene description.')))
    graph=await media_prompts.prepare(env.store,model,env.store.get(jid))
    assert graph['text']['inputs']['prompt']=='A detailed, expanded scene description.'
    assert json.loads(model.chat.call_args.args[0][1]['content'])['brief']==prompt
    assert snapshot(env.store,jid)['status']=='enhanced'
    assert env.store.get(jid)['prompt']==prompt


async def test_detailed_brief_without_skill_is_rendered_verbatim(scenario):
    env=scenario
    prompt=('A square flat illustration of a ginger cat asleep on a teal velvet sofa, soft window light '
            'from the left, warm afternoon shadows, a potted fern in the background and a knitted blanket draped over the armrest.')
    assert not media_prompts.is_simple('image',prompt)
    jid=env.store.enqueue(env.account['user_id'],None,'image',prompt,session_id=env.second['id'],channel='app')
    model=SimpleNamespace(chat=AsyncMock(side_effect=AssertionError('Detailed brief must not call model')))
    graph=await media_prompts.prepare(env.store,model,env.store.get(jid))
    assert graph['text']['inputs']['prompt']==prompt and snapshot(env.store,jid)['expansion'] is False


def test_enabled_skill_with_short_brief_adds_expansion_after_skill(scenario):
    saved=snapshot(scenario.store,enqueue(scenario,prompt='A circle')['id'])
    system=saved['system_prompt']
    assert system.index('VERSION ONE')<system.index(media_prompts.EXPANSION_GUIDANCE['image'])<system.index(media_prompts.OUTPUT_PROTOCOL)


async def test_failed_expansion_falls_back_to_short_brief(scenario):
    env=scenario
    jid=env.store.enqueue(env.account['user_id'],None,'image','一只猫',session_id=env.second['id'],channel='app')
    model=SimpleNamespace(chat=AsyncMock(side_effect=TimeoutError()))
    graph=await media_prompts.prepare(env.store,model,env.store.get(jid))
    assert graph['text']['inputs']['prompt']=='一只猫'
    assert snapshot(env.store,jid)['fallback']=='TimeoutError'


@pytest.mark.parametrize('prompt,units',[('一只猫',1.5),('A red apple',3),('红色 apple 两个',3)])
def test_detail_units_counts_words_and_cjk(prompt,units):
    assert media_prompts.detail_units(prompt)==units
