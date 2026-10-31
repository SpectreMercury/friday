import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from local_ai.store import Store
from local_ai.harness import HarnessService
from local_ai.engine import build_workflow
from local_ai.video_projects import VideoProjects,DAY


@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.setattr("local_ai.approvals.Approvals.request",AsyncMock(return_value=True))
    skills=tmp_path/'skills'
    for name in ('short-drama','h3-prompt-writing'):
        (skills/name).mkdir(parents=True);(skills/name/'SKILL.md').write_text('Frozen version one')
    store=Store(tmp_path/'db',skills_root=skills)
    account=store.create_account('owner')
    session=store.create_session(account['id'],'film',skills=['short-drama','h3-prompt-writing'])
    parent=store.enqueue(account['user_id'],None,'chat','Make a one-minute film',session_id=session['id'],channel='app')
    store.update(parent,status='running')
    yield SimpleNamespace(store=store,account=account,session=session,parent=store.get(parent),manager=store.video_projects,root=tmp_path,skills=skills)
    store.db.close()


def project(env,minutes=1):
    result=env.manager.create(env.parent,'Blue circle','A blue circle crosses a white room.',minutes,reference_choices={'character_photos':'用户选择不用角色照片','environment_photos':'用户选择不用环境照片'})
    return env.store.get(result['job_id'])


async def plan_reply(messages,schema=None,**kwargs):
    if 'shots' not in schema['properties']:
        return json.dumps({key:'A consistent blue circle in a white room' for key in schema['required']})
    indices=schema['properties']['shots']['items']['properties']['ordinal']['enum']
    shots=[]
    for i in indices:
        beat={'start_state':'A blue circle is in a white room.',
              'action':f'It rolls toward floor coordinate ({i*10}, {i*20}).',
              'end_state':f'It stops at coordinate ({i*10}, {i*20}).'}
        shots.append({'ordinal':i,'summary':f'Shot {i+1}','progression':beat,'prompt':' '.join(beat.values())})
    return json.dumps({'shots':shots})


async def plan_review(env,job,model):
    from local_ai.video_review import require_review
    await env.manager.plan(job,model)
    await require_review(env.store,job,AsyncMock(return_value=True),env.manager.review_document(job))


def fake_engine():
    return SimpleNamespace(chat=AsyncMock(side_effect=plan_reply),speech_worker=None,lock=asyncio.Lock(),stop=AsyncMock(),process=None,voice_reserved=False)


def test_default_duration_and_request_id_respect_explicit_duration(env):
    job=env.store.enqueue(env.account['user_id'],None,'video','scene',session_id=env.session['id'],channel='app',request_id='fifteen')
    frozen=json.loads(env.store.db.execute('select snapshot from media_prompts where job_id=?',(job,)).fetchone()[0])
    assert frozen['workflow']['text']['inputs']['length']==362
    assert env.store.get(job)['duration_seconds']==15
    assert build_workflow('video','x','id',duration_seconds=4)['text']['inputs']['length']==107
    with pytest.raises(ValueError):build_workflow('video','x','id',duration_seconds=16)
    with pytest.raises(ValueError):env.store.enqueue(env.account['user_id'],None,'video','scene',session_id=env.session['id'],channel='app',request_id='fifteen',duration_seconds=10)


def test_capacity_rejects_100_minutes_without_truncating_or_submitting(env):
    before=env.store.db.execute('select count(*) from jobs').fetchone()[0]
    result=env.manager.create(env.parent,'Long film','An entire story',100)
    assert result['reason']=='exceeds_24h_budget' and not result['submitted']
    assert result['estimate']['render_concurrency']==1 and result['estimate']['prepared_window']==2
    assert env.store.db.execute('select count(*) from jobs').fetchone()[0]==before


def test_project_marker_and_job_commit_atomically_and_submission_is_idempotent(env,monkeypatch):
    original=env.manager.insert
    def fail(*args):raise RuntimeError('crash before project marker')
    monkeypatch.setattr(env.manager,'insert',fail)
    with pytest.raises(RuntimeError):project(env)
    assert env.store.db.execute('select count(*) from jobs').fetchone()[0]==1
    monkeypatch.setattr(env.manager,'insert',original)
    first=project(env);second=project(env)
    assert first['id']==second['id']
    assert env.manager.get(first['id'])['deadline']==pytest.approx(first['created']+DAY)


async def test_window_refills_and_freezes_guidance_workflow_on_resume(env):
    job=project(env);model=fake_engine();await plan_review(env,job,model)
    assert model.chat.await_count==2
    env.manager.fill_window(job)
    children=[env.store.get(r[0]) for r in env.store.db.execute('select job_id from video_shots where project_id=? and job_id is not null order by ordinal',(job['id'],))]
    assert len(children)==2
    # Change live library and session; already accepted project keeps its version.
    for name in ('short-drama','h3-prompt-writing'):(env.skills/name/'SKILL.md').write_text('VERSION TWO')
    session=env.store.require_session(env.account['id'],env.session['id'])
    env.store.update_session(env.account['id'],session['id'],session['revision'],skill_names=[])
    env.store.update(children[0]['id'],status='succeeded',outcome='completed',result='clip.mp4')
    env.manager.fill_window(job)
    rows=env.store.db.execute('select job_id from video_shots where project_id=? and job_id is not null',(job['id'],)).fetchall()
    assert len(rows)==3
    for row in rows:
        assert env.store.capture_skills(row[0])['short-drama']['text']=='Frozen version one'
        snapshot=json.loads(env.store.db.execute('select snapshot from media_prompts where job_id=?',(row[0],)).fetchone()[0])
        assert snapshot['status']=='planned' and snapshot['workflow']['text']['inputs']['length']==362
    await plan_review(env,job,model)
    assert model.chat.await_count==2, 'resume must not rewrite the accepted storyboard'
    assert (env.store.workspace_directory(env.account['id'],env.session['id'])/f"video-project-{job['id']}.json").is_file()


async def test_serial_child_execution_publishes_each_clip_and_restarts_without_repeating(env,monkeypatch):
    job=project(env);service=HarnessService(env.root/'runtime',store=env.store,engine=fake_engine())
    calls=[]
    async def render(kind,prompt,jid,**kwargs):
        assert sum(r[0] for r in env.store.db.execute("select count(*) from jobs where parent_job_id=? and status='running'",(job['id'],)))==1
        calls.append(jid);path=env.root/(jid+'.mp4');path.write_bytes(b'fixture');return path
    service.engine.render=AsyncMock(side_effect=render)
    monkeypatch.setattr('local_ai.video_export.trim_clip',AsyncMock(side_effect=lambda source,*args:source))
    output=env.root/'film.mp4';output.write_bytes(b'merged')
    monkeypatch.setattr('local_ai.video_export.assemble_project',AsyncMock(return_value=output))
    env.store.start_run(job['id']);job.update(env.store.get(job['id']))
    assert await env.manager.execute(job,service)==str(output)
    assert len(calls)==4 and len(set(calls))==4
    assert service.engine.chat.await_count==2, 'each clip must not reload the chat model to rewrite its prompt'
    assert env.manager.public(job['id'])['completed']==4 and not service.video_child_tasks
    assert await env.manager.execute(job,service)==str(output)
    assert len(calls)==4
    complete=env.store.db.execute("select count(*) from session_events where kind='task_updated' and json_extract(payload,'$.status')='succeeded'").fetchone()[0]
    assert complete>=4


async def test_failure_cancels_prepared_clips_and_deadline_never_resets(env,monkeypatch):
    job=project(env);service=HarnessService(env.root/'runtime',store=env.store,engine=fake_engine())
    service.engine.render=AsyncMock(side_effect=RuntimeError('render failed'))
    env.store.start_run(job['id']);job.update(env.store.get(job['id']))
    with pytest.raises(RuntimeError,match='镜头未完成'):await env.manager.execute(job,service)
    assert env.manager.public(job['id'])['state']=='partial'
    assert not env.store.db.execute("select id from jobs where parent_job_id=? and status in ('queued','running')",(job['id'],)).fetchall()
    with env.store.db:env.store.db.execute('update video_projects set deadline=? where job_id=?',(time.time()-1,job['id']))
    with pytest.raises(RuntimeError,match='24小时'):await env.manager.execute(job,service)
    assert service.engine.render.await_count==1


async def test_live_project_cancel_cancels_child_and_preserves_other_session(env):
    job=project(env);service=HarnessService(env.root/'runtime',store=env.store,engine=fake_engine());started=asyncio.Event()
    async def render(*args,**kwargs):started.set();await asyncio.Event().wait()
    service.engine.render=AsyncMock(side_effect=render)
    env.store.start_run(job['id']);job.update(env.store.get(job['id']))
    task=asyncio.create_task(service.run_job(job));service.active_runs[job['id']]=(job,task)
    await asyncio.wait_for(started.wait(),3)
    await service.cancel_job(job['id'])
    assert env.store.get(job['id'])['status']=='cancelled'
    assert not service.video_child_tasks
    assert not env.store.db.execute("select id from jobs where parent_job_id=? and status in ('queued','running')",(job['id'],)).fetchall()

async def test_real_ffmpeg_trims_and_combines_in_order(tmp_path):
    from local_ai.video_export import command,assemble_project,duration,FFMPEG
    if not Path(FFMPEG).exists():pytest.skip('ffmpeg not installed')
    sources=[]
    for index,color in enumerate(('blue','white')):
        output=tmp_path/f'source-{index}.mp4'
        await command([FFMPEG,'-v','error','-y','-f','lavfi','-i',f'color=c={color}:s=64x64:r=24:d=1','-f','lavfi','-i','anullsrc=r=32000:cl=stereo','-t','1','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(output)])
        sources.append({'ordinal':index,'duration':.5,'result':str(output)})
    output=await assemble_project(sources,tmp_path)
    assert abs(await duration(output)-1)<.12
    await command([FFMPEG,'-v','error','-i',str(output),'-f','null','-'])
    assert all(Path(row['result']).is_file() for row in sources)


async def test_trim_cache_replaces_old_clip_when_source_changes_at_same_duration(tmp_path,monkeypatch):
    from local_ai import video_export
    if not Path(video_export.FFMPEG).exists():pytest.skip('ffmpeg not installed')
    source=tmp_path/'render.mp4';target=tmp_path/'trimmed.mp4'
    async def make_source(color):
        await video_export.command([video_export.FFMPEG,'-v','error','-y','-f','lavfi','-i',
            f'color=c={color}:s=64x64:r=24:d=1','-c:v','libx264','-pix_fmt','yuv420p',str(source)])
    await make_source('blue')
    await video_export.trim_clip(source,.5,target)
    blue=target.read_bytes()
    original_command=video_export.command
    commands=AsyncMock(side_effect=original_command)
    monkeypatch.setattr(video_export,'command',commands)
    await video_export.trim_clip(source,.5,target)
    assert all(call.args[0][0]!=video_export.FFMPEG for call in commands.await_args_list), 'unchanged source should reuse the checked export'
    await make_source('red')
    await video_export.trim_clip(source,.5,target)
    assert target.read_bytes()!=blue, 'same duration must not hide a new source render'
    assert abs(await video_export.duration(target)-.5)<.12
    # Even an altered cache file with the right duration is not trusted.
    target.write_bytes(blue)
    await video_export.trim_clip(source,.5,target)
    assert target.read_bytes()!=blue

async def test_script_message_and_attachment_sources_are_complete_and_session_bound(env):
    assert await env.manager.script_source(env.parent,{'script_source_job_id':env.parent['id']})==env.parent['prompt']
    other=env.store.create_session(env.account['id'],'Other')
    other_job=env.store.enqueue(env.account['user_id'],None,'chat','private elsewhere',session_id=other['id'],channel='app')
    with pytest.raises(PermissionError):await env.manager.script_source(env.parent,{'script_source_job_id':other_job})
    with pytest.raises(ValueError):await env.manager.script_source(env.parent,{'script':'hello','script_source_job_id':env.parent['id']})
    from local_ai.attachments import Attachments
    path=env.root/'script.txt';path.write_text('完整台词。'*300)
    attachments=Attachments(env.store,env.parent,env.root)
    aid=attachments.add(path,'script.txt')
    assert await env.manager.script_source(env.parent,{'script_attachment_id':aid})==path.read_text()

async def test_cancelling_one_active_shot_stops_project_without_auto_restart(env):
    job=project(env);service=HarnessService(env.root/'runtime',store=env.store,engine=fake_engine());started=asyncio.Event()
    async def render(*args,**kwargs):started.set();await asyncio.Event().wait()
    service.engine.render=AsyncMock(side_effect=render)
    run=env.store.start_run(job['id']);job.update(env.store.get(job['id']));job['run_id']=run['id']
    task=asyncio.create_task(service.run_job(job));service.active_runs[job['id']]=(job,task)
    await asyncio.wait_for(started.wait(),3)
    child=next(iter(service.video_child_tasks))
    await service.cancel_job(child)
    await asyncio.wait_for(task,3)
    assert env.store.get(job['id'])['status']=='failed'
    assert env.store.get(child)['status']=='cancelled'
    assert not env.store.db.execute("select id from jobs where parent_job_id=? and status in ('queued','running')",(job['id'],)).fetchall()


async def test_expired_queued_project_cancels_its_prepared_window(env):
    job=project(env);service=HarnessService(env.root/'runtime',store=env.store,engine=fake_engine())
    await plan_review(env,job,service.engine);env.manager.fill_window(job)
    with env.store.db:env.store.db.execute('update video_projects set deadline=? where job_id=?',(time.time()-1,job['id']))
    with pytest.raises(RuntimeError,match='24小时'):await env.manager.execute(job,service)
    assert env.manager.get(job['id'])['state']=='budget_exhausted'
    assert not env.store.db.execute("select id from jobs where parent_job_id=? and status='queued'",(job['id'],)).fetchall()

async def test_explicit_resume_reuses_completed_shot_and_keeps_original_deadline(env):
    job=project(env);await plan_review(env,job,fake_engine());env.manager.fill_window(job)
    children=[r[0] for r in env.store.db.execute('select job_id from video_shots where project_id=? and job_id is not null order by ordinal',(job['id'],))]
    env.store.update(children[0],status='succeeded',outcome='completed',result='existing.mp4')
    env.store.update(children[1],status='failed',outcome='blocked')
    env.store.update(job['id'],status='failed',outcome='blocked')
    deadline=env.manager.get(job['id'])['deadline']
    follow=env.store.enqueue(env.account['user_id'],None,'chat','继续这个项目',session_id=env.session['id'],channel='app')
    result=env.manager.resume(env.store.get(follow),job['id'])
    assert result['resumed'] and env.store.get(children[0])['status']=='succeeded'
    assert env.store.get(children[1])['status']=='queued'
    assert env.manager.get(job['id'])['deadline']==deadline


async def test_large_telegram_film_uses_real_segment_receipts_not_a_fake_media_receipt(env):
    from local_ai.bot import LocalBot
    # Route the isolated fixture through Telegram; no external sends occur.
    with env.store.db:env.store.db.execute("UPDATE jobs SET channel='telegram',chat_id=123 WHERE id=?",(env.parent['id'],))
    env.parent=env.store.get(env.parent['id'])
    job=project(env);await plan_review(env,job,fake_engine())
    clips=[]
    for _ in range(2):
        env.manager.fill_window(job)
        for row in env.store.db.execute("SELECT id FROM jobs WHERE parent_job_id=? AND status='queued'",(job['id'],)).fetchall():
            path=env.root/(row['id']+'.mp4');path.write_bytes(b'clip')
            env.store.update(row['id'],status='succeeded',result=str(path))
            clips.append(row['id'])
    film=env.root/'large.mp4'
    with film.open('wb') as stream:stream.truncate(49_000_001)
    env.store.update(job['id'],status='succeeded',result=str(film))
    proof={'accepted':True,'completion_watch_only':True,'completion_watch':[job['id']]}
    with env.store.db:env.store.db.execute("INSERT INTO pi_tools(job_id,user_id,name,args,result,created) VALUES (?,?,'final_check','{}',?,1)",(env.parent['id'],env.account['user_id'],json.dumps(proof)))
    env.store.update(env.parent['id'],status='succeeded',outcome='pending',result='Queued')
    bot=LocalBot.__new__(LocalBot);bot.store=env.store;bot.admin=0;bot.allowed=set()
    api=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=99)),send_video=AsyncMock())
    await bot.deliver(SimpleNamespace(bot=api),env.store.get(job['id']))
    api.send_video.assert_not_awaited()
    assert env.store.receipt(job['id'],'project_summary')
    assert not env.store.receipt(job['id'],'media')
    assert env.store.get(env.parent['id'])['outcome']=='pending'
    for index,clip in enumerate(clips):
        env.store.record_receipt(clip,'media',123,index+1)
        env.store.update(clip,delivered=1,delivery_state='delivered')
    assert env.store.get(env.parent['id'])['outcome']=='completed'
