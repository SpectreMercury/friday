import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from local_ai.approvals import Approvals
from local_ai.capabilities import CapabilityRegistry
from local_ai.pi_agent import PiAgent
from local_ai.providers import Providers, Skills, project_result
from local_ai.store import Store
from local_ai.tool_errors import ToolFailure
from local_ai.bot import LocalBot


def make(tmp_path):
    store=Store(tmp_path/'db');jid=store.enqueue(1,1,'chat','保存报告')
    store.update(jid,status='running');return store,store.get(jid)


@pytest.mark.asyncio
async def test_structured_provider_registration_permissions_and_secret_not_in_schema(tmp_path,monkeypatch):
    from local_ai import providers as module
    config=tmp_path/'providers.json'
    config.write_text(json.dumps([{'name':'api_private','url':'https://example.com/data','description':'data','secret_env':'PROVIDER_SAMPLE','properties':{'q':{'type':'string'}},'required':['q']}]))
    registry=CapabilityRegistry(admin=True);provider=Providers(config);provider.register(registry)
    assert not registry.definitions() and not registry.describe()[0]['available']
    monkeypatch.setenv('PROVIDER_SAMPLE','test-secret')
    assert 'test-secret' not in json.dumps(registry.definitions())
    ordinary=CapabilityRegistry();provider.register(ordinary)
    assert ordinary.definitions()==[]
    with pytest.raises(PermissionError):await ordinary.execute('api_private',{'q':'test'})
    fetch=AsyncMock(return_value={'data':{'score':4.2},'source':'https://example.com/data'})
    monkeypatch.setattr(module,'fetch_json',fetch)
    result=await registry.execute('api_private',{'q':'test'})
    assert result['data']['score']==4.2
    assert fetch.await_args.kwargs['headers']['Authorization']=='Bearer test-secret'
    with pytest.raises(ValueError):await registry.execute('api_private',{'q':1})
    config.write_text(json.dumps([{'name':'api_private','url':'http://127.0.0.1','description':'no'}]))
    with pytest.raises(ValueError):Providers(config)


def test_api_json_pointer_pages_and_skills_cannot_escape_root(tmp_path):
    result=project_result({'data':{'rows':list(range(50))}},'/rows',30)
    assert result['data']==list(range(30,50)) and result['next_offset'] is None
    with pytest.raises(ToolFailure):project_result({'data':{}},'/missing')
    root=tmp_path/'skills';(root/'example').mkdir(parents=True);(root/'example/SKILL.md').write_text('示例技能\n说明')
    skill=Skills(root)
    assert skill.list()[0]['name']=='example'
    assert skill.read('example')['permissions']=='unchanged'
    with pytest.raises(ValueError):skill.read('../other')


@pytest.mark.asyncio
async def test_approval_survives_restart_and_directory_grant_stays_in_task(tmp_path):
    store,job=make(tmp_path)
    manager=Approvals(store,1);message=SimpleNamespace(message_id=9,edit_reply_markup=AsyncMock())
    manager.bot=SimpleNamespace(send_message=AsyncMock(return_value=message))
    scope='computer_write:/Users/test/Documents/report'
    task=asyncio.create_task(manager.request(job,'write contents',scope=scope));await asyncio.sleep(.01)
    token=next(iter(manager.pending));task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    message.edit_reply_markup.assert_not_awaited()
    manager=Approvals(store,1);manager.bot=SimpleNamespace(send_message=AsyncMock())
    update=SimpleNamespace(callback_query=SimpleNamespace(data=f'approve:{token}:scope',answer=AsyncMock()),effective_user=SimpleNamespace(id=1),effective_chat=SimpleNamespace(id=1,type='private'))
    await manager.callback(update,None)
    assert await manager.request(job,'write contents',scope=scope)
    manager.bot.send_message.assert_not_awaited()
    assert manager.granted(job,scope) and not manager.granted(job,scope+'/other')
    other=store.enqueue(1,1,'chat','unrelated')
    assert not manager.granted(store.get(other),scope)
    store.db.close()


@pytest.mark.asyncio
async def test_active_correction_is_persisted_new_topic_remains_queued(tmp_path):
    store,job=make(tmp_path)
    correction=store.enqueue(1,1,'chat','改用中文并注明来源')
    other=store.enqueue(1,1,'chat','明天东京天气')
    engine=SimpleNamespace(chat=AsyncMock(side_effect=['{"continue":true}','{"continue":false}']))
    agent=PiAgent(engine,store,lambda _:True,tmp_path)
    await agent.sync_inputs(job)
    assert store.pending_inputs(job)==[{'job_id':correction,'text':'改用中文并注明来源'}]
    assert store.get(correction)['delivery_state']=='suppressed'
    assert store.get(other)['status']=='queued'
    # Reopen preserves unread correction. Consumption happens only after checkpoint.
    store.db.close();store=Store(tmp_path/'db')
    assert store.pending_inputs(job)
    store.consume_inputs(job,[correction]);assert not store.pending_inputs(job)
    assert len(store.task_facts(job)['user_requests'])==2
    store.db.close()


@pytest.mark.asyncio
async def test_uncertain_send_is_not_automatically_repeated(tmp_path):
    store,job=make(tmp_path);store.update(job['id'],status='succeeded',result='报告完成')
    bot=LocalBot.__new__(LocalBot);bot.store=store;bot.is_allowed=lambda _:True
    api=SimpleNamespace(send_message=AsyncMock(side_effect=TimeoutError('after send')))
    with pytest.raises(ToolFailure):await bot.deliver(SimpleNamespace(bot=api),store.get(job['id']))
    assert store.get(job['id'])['delivery_state']=='uncertain'
    assert not store.pending_delivery()
    with pytest.raises(ToolFailure):await bot.deliver(SimpleNamespace(bot=api),store.get(job['id']))
    assert api.send_message.await_count==1 and not store.receipt(job['id'],'text:0')
    store.db.close()


@pytest.mark.asyncio
async def test_resume_does_not_launch_unadmitted_write_after_correction(tmp_path):
    from local_ai.capabilities import Capability
    from local_ai.tool_runtime import ToolRuntime
    store,job=make(tmp_path)
    correction=store.enqueue(1,1,'chat','不要写文件，先告诉我内容')
    store.absorb_input(job,store.get(correction))
    handler=AsyncMock(return_value={'saved':'file'})
    registry=CapabilityRegistry();registry.register(Capability('write','write',effect='local_write',replay='reconcile'),handler)
    messages=[{'role':'user','content':'写文件'},{'role':'assistant','content':[{'type':'toolCall','id':'old-write','name':'write','arguments':{}}]}]
    restored=await ToolRuntime(store,job,registry,lambda _:True).repair_checkpoint(messages)
    assert restored[-1]['isError'] and 'task_updated' in restored[-1]['content'][0]['text']
    handler.assert_not_awaited();store.db.close()


def test_parent_completion_requires_children_and_delivery_receipts(tmp_path):
    store,job=make(tmp_path);store.update(job['id'],status='succeeded',outcome='pending')
    child=store.enqueue(1,1,'image','cat',parent_job_id=job['id'])
    artifact=tmp_path/'generated.png';artifact.write_bytes(b'synthetic artifact')
    proof={'accepted':True,'completion_watch':[child],'completion_watch_only':True}
    store.db.execute("INSERT INTO pi_tools(job_id,user_id,name,args,result,created) VALUES (?,?, 'final_check','{}',?,?)",
                     (job['id'],1,json.dumps(proof),job['created']))
    store.db.commit()
    store.update(child,status='succeeded',result=str(artifact),outcome='completed')
    assert store.get(job['id'])['outcome']=='pending'
    store.record_receipt(child,'media',1,100)
    store.update(child,delivery_state='delivered',delivered=1)
    assert store.get(job['id'])['outcome']=='completed'
    store.db.close()


@pytest.mark.asyncio
async def test_pending_approval_operation_replays_wait_not_write(tmp_path):
    from local_ai.capabilities import Capability
    from local_ai.tool_runtime import ToolRuntime
    store,job=make(tmp_path)
    manager=Approvals(store,1)
    manager.bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=12,edit_reply_markup=AsyncMock())))
    effects=[]
    async def handler(name,args):
        if not await manager.request(job,'write reviewed bytes'):raise ValueError('denied')
        effects.append('written');return {'saved':True}
    registry=CapabilityRegistry();registry.register(Capability('write','write',effect='local_write',replay='reconcile'),handler)
    runtime=ToolRuntime(store,job,registry,lambda _:True)
    running=asyncio.create_task(runtime.call('write1','write',{}));await asyncio.sleep(.01)
    token=next(iter(manager.pending));running.cancel()
    with pytest.raises(asyncio.CancelledError):await running
    assert store.operation(job['id'],'write1')['status']=='waiting_approval' and not effects
    manager=Approvals(store,1);manager.bot=SimpleNamespace(send_message=AsyncMock())
    update=SimpleNamespace(callback_query=SimpleNamespace(data=f'approve:{token}:yes',answer=AsyncMock()),effective_user=SimpleNamespace(id=1),effective_chat=SimpleNamespace(id=1,type='private'))
    await manager.callback(update,None)
    result=await runtime.call('write1','write',{})
    assert result['result']=={'saved':True} and effects==['written']
    assert (await runtime.call('write1','write',{}))['cached'] and len(effects)==1
    manager.bot.send_message.assert_not_awaited();store.db.close()


@pytest.mark.asyncio
async def test_prior_failed_turn_is_imported_as_evidence_never_reexecuted(tmp_path):
    from local_ai.capabilities import Capability
    from local_ai.tool_runtime import ToolRuntime
    store,job=make(tmp_path)
    spec=Capability('write','write',effect='local_write',replay='reconcile')
    store.begin_operation(job['id'],'same-id',spec,{})
    store.save_checkpoint(job['id'],[{'role':'user','content':'写文件'},
        {'role':'assistant','content':[{'type':'toolCall','id':'same-id','name':'write','arguments':{}}]}])
    store.update(job['id'],status='failed')
    follow=store.enqueue(1,1,'chat','刚才写成功了吗')
    imported=store.conversation(store.get(follow))
    assert imported[-1]['role']=='toolResult' and imported[-1]['isError']
    assert imported[-1]['toolCallId']!='same-id'
    assert imported[-2]['content'][0]['id']==imported[-1]['toolCallId']
    handler=AsyncMock();registry=CapabilityRegistry();registry.register(spec,handler)
    await ToolRuntime(store,store.get(follow),registry,lambda _:True).repair_checkpoint(imported)
    handler.assert_not_awaited()
    assert store.operation(job['id'],'same-id')['status']=='started'
    store.db.close()


def test_model_task_link_does_not_transfer_directory_permission(tmp_path):
    store,job=make(tmp_path);manager=Approvals(store,1)
    scope='computer_write:/Users/test/Documents/project'
    import time
    store.db.execute('INSERT INTO permission_grants VALUES (?,?,?,?,?)',(job['id'],1,1,scope,time.time()+100));store.db.commit()
    follow=store.enqueue(1,1,'chat','继续')
    store.link_task(store.get(follow),job['id'])
    assert manager.granted(job,scope)
    assert not manager.granted(store.get(follow),scope)
    store.db.close()


@pytest.mark.asyncio
async def test_uncertain_operation_cannot_be_replayed_in_followup_request(tmp_path):
    from local_ai.capabilities import Capability
    from local_ai.tool_runtime import ToolRuntime
    store,job=make(tmp_path)
    spec=Capability('write','write',effect='external_write',replay='reconcile')
    store.begin_operation(job['id'],'old-call',spec,{})
    store.update(job['id'],status='failed')
    follow=store.enqueue(1,1,'chat','继续刚才任务')
    store.link_task(store.get(follow),job['id'])
    handler=AsyncMock();registry=CapabilityRegistry();registry.register(spec,handler)
    with pytest.raises(ToolFailure,match='不能重复'):
        await ToolRuntime(store,store.get(follow),registry,lambda _:True).call('new-call','write',{})
    handler.assert_not_awaited();store.db.close()


@pytest.mark.asyncio
async def test_builtin_write_interruption_is_reconciled_from_actual_file(tmp_path,monkeypatch):
    from local_ai.pi_agent import UserTools
    from local_ai.tool_runtime import ToolRuntime
    store,job=make(tmp_path)
    original=UserTools.execute;writes=[]
    async def interrupted(self,name,args):
        result=await original(self,name,args)
        if name=='write_file':
            writes.append(name)
            raise asyncio.CancelledError()
        return result
    monkeypatch.setattr(UserTools,'execute',interrupted)
    tools=UserTools(store,job,lambda _:True,tmp_path/'workspaces')
    runtime=ToolRuntime(store,job,tools.registry(),lambda _:True)
    args={'name':'restored.txt','content':'已写入，回执之前中断'}
    with pytest.raises(asyncio.CancelledError):await runtime.call('write1','write_file',args)
    assert store.operation(job['id'],'write1')['status']=='started'
    result=await runtime.call('write1','write_file',args)
    assert result['cached'] and result['result']['verified_after_interruption']
    assert writes==['write_file'] and store.operation(job['id'],'write1')['status']=='succeeded'
    store.db.close()


@pytest.mark.asyncio
async def test_media_submission_is_reconciled_even_before_legacy_log(tmp_path,monkeypatch):
    from local_ai.pi_agent import UserTools
    from local_ai.tool_runtime import ToolRuntime
    import local_ai.pi_agent as module
    monkeypatch.setattr(module,'model_status',lambda _:[])
    store,job=make(tmp_path)
    tools=UserTools(store,job,lambda _:True,tmp_path/'workspaces');registry=tools.registry()
    args={'prompt':'blue paper boat'}
    store.begin_operation(job['id'],'submit1',registry.spec('generate_image'),args)
    child=store.enqueue(1,1,'image',args['prompt'],parent_job_id=job['id'])
    runtime=ToolRuntime(store,job,registry,lambda _:True)
    first=await runtime.call('submit1','generate_image',args)
    assert first['cached'] and first['result']['job_id']==child
    second=await runtime.call('submit2','generate_image',args)
    assert second['result']['job_id']==child
    assert store.db.execute('SELECT count(*) FROM jobs WHERE parent_job_id=?',(job['id'],)).fetchone()[0]==1
    store.db.close()


@pytest.mark.asyncio
async def test_file_mismatch_cannot_be_claimed_as_reconciled(tmp_path):
    from local_ai.pi_agent import UserTools
    from local_ai.tool_runtime import ToolRuntime
    store,job=make(tmp_path);tools=UserTools(store,job,lambda _:True,tmp_path/'workspaces');registry=tools.registry()
    args={'name':'partial.txt','content':'complete result'}
    tools.file_path(args['name']).write_text('incomplete')
    store.begin_operation(job['id'],'write1',registry.spec('write_file'),args)
    result=await ToolRuntime(store,job,registry,lambda _:True).call('write1','write_file',args)
    assert result['failure']['uncertain'] and tools.file_path(args['name']).read_text()=='incomplete'
    store.db.close()
