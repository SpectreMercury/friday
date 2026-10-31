import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from local_ai.execution import validate_plan
from local_ai.outcomes import OutcomeReviewer, bind_contract
from local_ai.outcome_evidence import evidence_catalog
from local_ai.pi_agent import UserTools
from local_ai.store import Store

@pytest.fixture
def task(tmp_path):
    store=Store(tmp_path/'db');jid=store.enqueue(1,1,'chat','筛选整体评分超过4且环境比口味正好低0.3的店铺')
    yield store,store.get(jid),UserTools(store,store.get(jid),lambda _:True,tmp_path/'notes')
    store.db.close()


@pytest.mark.asyncio
async def test_plan_revision_never_changes_original_user_requirements(task):
    store,job,tools=task
    one=await tools.execute('plan_task',{'goal':job['prompt'],'mode':'research','steps':['取API数据','检查条件']})
    two=await tools.execute('plan_task',{'goal':'调整方法','mode':'research','steps':['API不可用，读取公开原文']})
    assert two['revision']==2 and one['revision']==1
    assert store.task_facts(job)['user_requests'][0]['text']==job['prompt']
    assert validate_plan({'goal':'你好','mode':'answer'})['mode']=='answer'


def test_tasks_link_only_same_principal_and_reset_separates_context(task):
    store,job,tools=task
    store.update(job['id'],status='failed',outcome='blocked')
    other=store.enqueue(1,1,'chat','改成至少低0.3')
    facts=store.link_task(store.get(other),job['id'])
    assert [x['text'] for x in facts['user_requests']]==[job['prompt'],'改成至少低0.3']
    foreign=store.enqueue(2,2,'chat','other')
    with pytest.raises(PermissionError):store.link_task(store.get(foreign),job['id'])
    store.reset(1)
    with pytest.raises(ValueError):store.link_task(store.get(other),job['id'])


@pytest.mark.asyncio
async def test_repeated_continue_task_reports_existing_link_without_relinking(task, monkeypatch):
    store, job, _ = task
    store.update(job['id'], status='succeeded', outcome='completed')
    follow = store.enqueue(1, 1, 'chat', '继续处理上一条')
    tools = UserTools(store, store.get(follow), lambda _: True)
    first = await tools.execute('continue_task', {'job_id': job['id']})
    assert first['already_linked'] is False
    monkeypatch.setattr(store, 'link_task', lambda *_: pytest.fail('existing link must not be rewritten'))
    repeated = await tools.execute('continue_task', {'job_id': job['id']})
    assert repeated['already_linked'] is True
    assert 'user_requests' not in repeated


def test_atomic_inbound_dedup_and_full_tool_conversation(task):
    store,job,tools=task
    store.update(job['id'],status='succeeded')
    store.save_checkpoint(job['id'],[{'role':'user','content':job['prompt']},
        {'role':'assistant','content':[{'type':'toolCall','id':'x','name':'calculate','arguments':{'expression':'4.5-4.2'}}]},
        {'role':'toolResult','toolCallId':'x','toolName':'calculate','content':[{'type':'text','text':'{"value":"0.3"}'}]},
        {'role':'assistant','content':[{'type':'text','text':'0.3'}]}])
    follow=store.enqueue(1,1,'chat','这家符合吗',update_id=42,message_id=2)
    assert store.enqueue(1,1,'chat','重复',update_id=42,message_id=2)==follow
    assert any(m['role']=='toolResult' for m in store.conversation(store.get(follow)))
    assert store.conversation(store.get(store.enqueue(2,2,'chat','other')))==[]


def test_math_claims_are_checked_independently_of_model_verdict():
    from local_ai.outcomes import verify_math_claims
    assert verify_math_claims([{'expression':'4.0 > 4','claimed_value':'true','round_digits':None}])[0]['actual'] is False
    assert not verify_math_claims([{'expression':'4.6 - 4.3','claimed_value':'0.3','round_digits':None},
                                  {'expression':'17 * 23.5 * 0.85','claimed_value':'339.58','round_digits':2},
                                  {'expression':'4.0 > 4','claimed_value':'false','round_digits':None}])
    assert verify_math_claims([{'expression':'__import__("os")','claimed_value':'0','round_digits':None}])


def test_acceptance_row_checker_understands_decision_without_neighbour_leak():
    from scripts.evaluate_system import excluded_row
    for negative in ('否','✗','不入选','不满足'):
        assert excluded_row('| 丙 | 4.0 | '+negative+' |','丙')
    assert not excluded_row('| 丙 | 4.0 | 是 |\n| 丁 | 4.7 | 否 |','丙')
    assert not excluded_row('| 丙 | 4.0>4 ✗ | 是 |','丙')


def test_linked_task_facts_distinguish_completed_history_from_current_request(task):
    store,job,_=task
    store.update(job['id'],status='succeeded',outcome='completed')
    follow=store.enqueue(1,1,'chat','补充一个事实，请按修正后的信息解释')
    store.update(follow,status='running')
    facts=store.link_task(store.get(follow),job['id'])
    assert [(r['job_id'],r['status'],r['outcome']) for r in facts['user_requests']]==[
        (job['id'],'succeeded','completed'),(follow,'running',None)]
    assert facts['user_requests'][0]['text']==job['prompt']



def contract_for(store,job,kind='reasoning',*,proof=None,missing=None,sources=None):
    proof=proof or {'reasoning':'reasoning','provided_data':'provided_data',
                    'external_observation':'observation','effect_receipt':'operation'}[kind]
    return bind_contract({'deliverables':[{'description':'当前产物','evidence':kind,'proof_kind':proof,
        'source_job_ids':sources or [job['id']]}],'known_missing_inputs':missing or []},job,store.task_facts(job))


def verdict_for(contract,*,state='satisfied',refs=None,accepted=True,outcome='completed',repair=False,missing=None,math=None):
    requirement=contract['deliverables'][0]
    coverage=[{'source_job_id':source,'goals':[{'description':requirement['description'],
        'proof_kind':requirement['proof_kind'],'contract_requirement_ids':[requirement['requirement_id']],
        'missing_user_inputs':[]}]} for source in contract['current_input_ids']]
    return {'request_coverage':coverage,'checks':[{'requirement_id':requirement['requirement_id'],'state':state,
                      'evidence_refs':refs or [],'evidence':'检查当前要求'}],
            'accepted':accepted,'outcome':outcome,'repair':repair,'feedback':'',
            'missing_user_inputs':missing or [],'math_claims':math or [],'resolved_missing_inputs':[]}


def mock_review(store,contract,verdict):
    engine=SimpleNamespace(chat=AsyncMock(return_value=json.dumps(verdict)))
    reviewer=OutcomeReviewer(engine,store)
    reviewer.contract=AsyncMock(return_value=contract)
    return reviewer,engine


@pytest.mark.asyncio
async def test_outcome_uses_requests_values_and_receipts_not_tool_names(task):
    store,job,_=task
    contract=contract_for(store,job,'external_observation')
    reviewer,engine=mock_review(store,contract,verdict_for(contract,accepted=False,outcome='blocked',repair=True,state='missing'))
    result=await reviewer.review(job,'已完成：店铺甲')
    assert not result['accepted'] and result['repair'] and result['outcome']=='blocked'
    payload=json.loads(engine.chat.await_args.args[0][1]['content'])
    assert payload['task']['user_requests'][0]['text']==job['prompt']
    assert all(e['kind']=='input' for e in payload['evidence_catalog'])


@pytest.mark.asyncio
async def test_truthful_blocker_is_accepted_but_never_completed(task):
    store,job,_=task
    contract=contract_for(store,job,'external_observation',missing=['凭据'])
    verdict=verdict_for(contract,state='needs_input',outcome='needs_input',missing=['凭据'])
    reviewer,engine=mock_review(store,contract,verdict)
    result=await reviewer.review(job,'缺少此接口的账号凭据。')
    assert result['accepted'] and result['outcome']=='needs_input' and not result['repair']
    engine.chat.return_value='not json'
    failed=await reviewer.review(job,'原候选')
    assert failed['outcome']=='partial' and failed['answer']=='原候选' and failed['review_error']


@pytest.mark.asyncio
async def test_mechanical_evidence_minimum_rejects_false_positive_reviewer(task):
    store,job,_=task
    contract=contract_for(store,job,'external_observation',missing=['系统访问'])
    reviewer,_=mock_review(store,contract,verdict_for(contract,refs=['input:'+job['id']]))
    result=await reviewer.review(job,'已取得真实数据。')
    assert not result['accepted'] and result['outcome']=='needs_input' and not result['repair']
    assert not result['checks'][0]['satisfied']


@pytest.mark.asyncio
async def test_review_cannot_accept_a_false_arithmetic_claim(task):
    store,job,_=task
    contract=contract_for(store,job)
    verdict=verdict_for(contract,refs=['input:'+job['id']],math=[
        {'expression':'3.2*4','claimed_value':'12.9','round_digits':None}])
    reviewer,_=mock_review(store,contract,verdict)
    result=await reviewer.review(job,'3.2乘4是12.9')
    assert not result['accepted'] and result['repair']
    assert result['evidence']['math_errors'][0]['actual']=='12.8'


@pytest.mark.asyncio
async def test_status_polling_cannot_crowd_real_receipts_out_of_review(task):
    from local_ai.capabilities import BUILTINS
    store,job,_=task
    specs={s.name:s for s in BUILTINS}
    store.begin_operation(job['id'],'actual-read',specs['web_read'],{'url':'https://example.test'})
    store.finish_operation(job['id'],'actual-read',result={'text':'verified source'})
    for index in range(35):
        cid=f'poll-{index}'
        store.begin_operation(job['id'],cid,specs['task_status'],{})
        store.finish_operation(job['id'],cid,result={'status':'running','text':'x'*10000})
    contract=contract_for(store,job,'external_observation')
    reviewer,engine=mock_review(store,contract,verdict_for(contract,refs=[f"operation:{job['id']}:actual-read"]))
    result=await reviewer.review(job,'verified source')
    catalog=json.loads(engine.chat.await_args.args[0][1]['content'])['evidence_catalog']
    assert [e['data']['name'] for e in catalog if e['kind']=='operation']==['web_read']
    assert result['accepted'] and result['outcome']=='completed'


@pytest.mark.asyncio
@pytest.mark.parametrize('has_child',[True,False])
@pytest.mark.parametrize('claims_complete',[True,False])
async def test_pending_notice_requires_actual_async_child_not_final_artifact(task,has_child,claims_complete):
    store,job,_=task
    refs=[]
    if has_child:
        child=store.enqueue(1,1,'image','paper boat',parent_job_id=job['id'])
        refs=['artifact:'+child]
    contract=contract_for(store,job,'external_observation',proof='artifact')
    verdict=verdict_for(contract,state='satisfied' if claims_complete else 'pending',refs=refs,
                        outcome='completed' if claims_complete else 'pending')
    reviewer,_=mock_review(store,contract,verdict)
    result=await reviewer.review(job,'正在生成图片。')
    assert result['accepted'] is (has_child and not claims_complete)
    assert result['completion_watch_only'] is (has_child and not claims_complete)
    assert result['outcome']==('pending' if has_child else 'blocked')


@pytest.mark.asyncio
async def test_contract_cache_refreshes_when_historical_completion_changes(task):
    store,job,_=task
    store.update(job['id'],status='succeeded',outcome='partial')
    follow=store.enqueue(1,1,'chat','解释当前结果')
    store.link_task(store.get(follow),job['id']); current=store.get(follow)
    raw={'deliverables':[{'description':'解释','evidence':'reasoning','proof_kind':'reasoning',
                         'source_job_ids':[follow]}],'known_missing_inputs':[]}
    engine=SimpleNamespace(chat=AsyncMock(return_value=json.dumps(raw)))
    reviewer=OutcomeReviewer(engine,store)
    one=await reviewer.contract(current,store.task_facts(current))
    await reviewer.contract(current,store.task_facts(current))
    assert engine.chat.await_count==1
    first=json.loads(engine.chat.await_args.args[0][1]['content'])
    assert first['historical_background'][0]['outcome']=='partial'
    store.update(job['id'],outcome='completed')
    two=await reviewer.contract(current,store.task_facts(current))
    assert engine.chat.await_count==2
    second=json.loads(engine.chat.await_args.args[0][1]['content'])
    assert second['historical_background'][0]['outcome']=='completed'
    assert first['current_inputs']==second['current_inputs']
    assert one['deliverables'][0]['requirement_id']==two['deliverables'][0]['requirement_id']


@pytest.mark.asyncio
@pytest.mark.parametrize('evidence_kind',['external_observation','effect_receipt'])
@pytest.mark.parametrize('case',['delivered','missing_receipt','failed','submission_only','wrong_chat_receipt','foreign_account','foreign_session'])
async def test_media_minimum_evidence_requires_verified_owned_delivery(task,evidence_kind,case):
    from local_ai.capabilities import BUILTINS
    store,job,_=task
    store.update(job['id'],status='succeeded',outcome='completed')
    parent=job
    if case=='foreign_account':
        pid=store.enqueue(2,2,'chat','foreign request');store.update(pid,status='succeeded')
        parent=store.get(pid)
    elif case=='foreign_session':
        session=store.create_session(job['account_id'],'separate')
        pid=store.enqueue(1,None,'chat','separate request',session_id=session['id'],channel='app')
        store.update(pid,status='succeeded');parent=store.get(pid)
    if parent['id']!=job['id']:
        with store.db:store.db.execute('UPDATE jobs SET task_id=? WHERE id=?',(job['task_id'],parent['id']))
    child=store.enqueue(parent['user_id'],parent['chat_id'],'image','paper boat',parent_job_id=parent['id'])
    state='failed' if case=='failed' else 'queued' if case=='submission_only' else 'succeeded'
    store.update(child,status=state,outcome='completed' if state=='succeeded' else None,
                 delivery_state='pending' if case=='submission_only' else 'delivered')
    if case not in ('missing_receipt','submission_only'):
        store.record_receipt(child,'media',999 if case=='wrong_chat_receipt' else store.get(child)['chat_id'],101)
    spec=next(s for s in BUILTINS if s.name=='generate_image')
    store.begin_operation(parent['id'],'generate',spec,{'prompt':'paper boat'})
    store.finish_operation(parent['id'],'generate',result={'job_id':child,'status':'queued'})
    follow=store.enqueue(1,1,'chat','检查此前图片是否已交付')
    store.link_task(store.get(follow),job['id']);current=store.get(follow)
    contract=contract_for(store,current,evidence_kind,proof='delivery',sources=[job['id'],follow])
    pending=case=='submission_only'
    verdict=verdict_for(contract,state='pending' if pending else 'satisfied',refs=['delivery:'+child],
                        outcome='pending' if pending else 'completed')
    reviewer,engine=mock_review(store,contract,verdict)
    result=await reviewer.review(current,'检查交付结果。')
    if case=='delivered':
        assert result['accepted'] and result['outcome']=='completed' and not result['repair']
    elif pending:
        assert result['accepted'] and result['outcome']=='pending'
    else:
        assert not result['accepted'] and result['outcome']!='completed'
    if case in ('foreign_account','foreign_session'):
        catalog=json.loads(engine.chat.await_args.args[0][1]['content'])['evidence_catalog']
        assert not any(e['kind']!='input' for e in catalog)
        assert result['review_error']


@pytest.mark.asyncio
async def test_media_delivery_does_not_override_unsatisfied_review_checks(task):
    store,job,_=task
    child=store.enqueue(1,1,'image','paper boat',parent_job_id=job['id'])
    store.update(child,status='succeeded',outcome='completed',delivery_state='delivered')
    store.record_receipt(child,'media',job['chat_id'],101)
    contract=contract_for(store,job,'external_observation',proof='delivery')
    verdict=verdict_for(contract,state='missing',refs=['delivery:'+child],accepted=False,outcome='partial',repair=True)
    reviewer,_=mock_review(store,contract,verdict)
    result=await reviewer.review(job,'图片内容不符合要求。')
    assert not result['accepted'] and result['outcome']=='blocked' and result['repair']
