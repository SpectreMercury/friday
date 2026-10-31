"""Adversarial v2 requirement scopes, typed evidence, and outcome invariants."""
import hashlib
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from local_ai.capabilities import BUILTINS
from local_ai.outcomes import (OutcomeReviewer, bind_contract, normalize_review,
                               request_size, MAX_REVIEW_INPUT_TOKENS, CONTRACT_VERSION)
from local_ai.outcome_evidence import evidence_catalog, input_scope, catalog_view
from local_ai.store import Store


@pytest.fixture
def scenario(tmp_path):
    store=Store(tmp_path/'db')
    jid=store.enqueue(1,1,'chat','完成当前请求')
    store.update(jid,status='running')
    yield store,store.get(jid),tmp_path
    store.db.close()


def item(job,proof='reasoning',description='当前要求',sources=None):
    category={'reasoning':'reasoning','provided_data':'provided_data','observation':'external_observation',
              'artifact':'external_observation','delivery':'effect_receipt','operation':'effect_receipt','effect':'effect_receipt'}[proof]
    return {'description':description,'evidence':category,'proof_kind':proof,'source_job_ids':sources or [job['id']]}


def contract(store,job,*items,missing=None):
    return bind_contract({'deliverables':list(items) or [item(job)],'known_missing_inputs':missing or []},job,store.task_facts(job))


def verdict(bound,refs=None,states=None,**fields):
    refs=refs or [[] for _ in bound['deliverables']]
    states=states or ['satisfied']*len(refs)
    checks=[{'requirement_id':d['requirement_id'],'state':s,'evidence_refs':r,'evidence':'逐项语义检查'}
            for d,r,s in zip(bound['deliverables'],refs,states)]
    coverage=[{'source_job_id':source,'goals':[
        {'description':d['description'],'proof_kind':d['proof_kind'],
         'contract_requirement_ids':[d['requirement_id']],'missing_user_inputs':[]}
        for d in bound['deliverables'] if source in d['source_job_ids']]} for source in bound['current_input_ids']]
    return {'request_coverage':coverage,'checks':checks,'math_claims':[],'accepted':True,'outcome':'completed',
            'repair':False,'feedback':'','missing_user_inputs':[],'resolved_missing_inputs':[],**fields}


def operation(store,job,name,cid='call',result=None,finish=True):
    spec=next(s for s in BUILTINS if s.name==name)
    store.begin_operation(job['id'],cid,spec,{})
    if finish:
        store.finish_operation(job['id'],cid,result=result or {'value':'verified'})
    return f"operation:{job['id']}:{cid}"


def followup(store,old,prompt='纠正一个事实，请直接解释'):
    store.update(old['id'],status='succeeded',outcome='completed')
    jid=store.enqueue(1,1,'chat',prompt)
    store.link_task(store.get(jid),old['id'])
    store.update(jid,status='running')
    return store.get(jid)


@pytest.mark.asyncio
async def test_latest_correction_is_current_scope_and_completed_action_is_background(scenario):
    store,old,_=scenario
    current=followup(store,old)
    raw={'deliverables':[item(current)],'known_missing_inputs':[]}
    engine=SimpleNamespace(chat=AsyncMock(return_value=json.dumps(raw)))
    bound=await OutcomeReviewer(engine,store).contract(current,store.task_facts(current))
    sent=json.loads(engine.chat.await_args.args[0][1]['content'])
    assert sent['latest_request']['job_id']==current['id']
    assert [r['job_id'] for r in sent['current_inputs']]==[current['id']]
    assert [r['job_id'] for r in sent['historical_background']]==[old['id']]
    assert bound['deliverables'][0]['source_job_ids']==[current['id']]
    with pytest.raises(ValueError,match='completed background'):
        contract(store,current,item(old,'artifact'))


def test_current_and_absorbed_requests_must_be_covered(scenario):
    store,job,_=scenario
    incoming=store.enqueue(1,1,'chat','新增一个验收条件')
    store.absorb_input(job,store.get(incoming))
    assert set(input_scope(job,store.task_facts(job))['current_input_ids'])=={job['id'],incoming}
    with pytest.raises(ValueError,match='omitted'):
        contract(store,job,item(job))
    bound=contract(store,job,item(job,sources=[job['id'],incoming]))
    assert set(bound['current_input_ids'])=={job['id'],incoming}


def test_unfinished_request_remains_an_explicit_source_not_a_global_obligation(scenario):
    store,old,_=scenario
    current=followup(store,old,'继续之前没有完成的查询，并解释方法')
    store.update(old['id'],outcome='partial')
    bound=contract(store,current,item(old,'observation'),item(current))
    assert len(bound['deliverables'])==2
    # Work performed now can fulfil the explicitly retained old requirement.
    proof=operation(store,current,'web_read')
    raw=verdict(bound,[[proof],['input:'+current['id']]])
    result=normalize_review(raw,bound,evidence_catalog(store,current,store.task_facts(current)))
    assert result['accepted'] and result['outcome']=='completed'


@pytest.mark.asyncio
async def test_pre_v2_contract_cache_is_not_reused(scenario):
    store,job,_=scenario
    facts=store.task_facts(job)
    old_hash=hashlib.sha256(json.dumps(facts['user_requests'],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    with store.db:
        store.db.execute('INSERT INTO task_contracts VALUES (?,?,?,?)',(job['id'],old_hash,
            json.dumps({'deliverables':[{'description':'stale','evidence':'effect_receipt'}],'known_missing_inputs':[]}),time.time()))
    engine=SimpleNamespace(chat=AsyncMock(return_value=json.dumps({'deliverables':[item(job)],'known_missing_inputs':[]})))
    bound=await OutcomeReviewer(engine,store).contract(job,facts)
    assert engine.chat.await_count==1 and bound['schema_version']==CONTRACT_VERSION
    assert bound['deliverables'][0]['description']!='stale'


@pytest.mark.parametrize('bad',['foreign_source','duplicate_requirement','conflicting_proof'])
def test_contract_rejects_invalid_sources_and_proof_categories(scenario,bad):
    store,job,_=scenario
    items=[item(job)]
    if bad=='foreign_source':items[0]['source_job_ids']=['unknown']
    elif bad=='duplicate_requirement':items.append(dict(items[0]))
    else:items[0]['proof_kind']='delivery'
    with pytest.raises(ValueError):contract(store,job,*items)


@pytest.mark.parametrize('bad',['missing_check','duplicate_check','unknown_requirement','fake_evidence'])
def test_review_cannot_omit_duplicate_or_fabricate_proof(scenario,bad):
    store,job,_=scenario
    bound=contract(store,job,item(job,description='first'),item(job,description='second'))
    raw=verdict(bound,[['input:'+job['id']],['input:'+job['id']]])
    if bad=='missing_check':raw['checks'].pop()
    elif bad=='duplicate_check':raw['checks'][1]=dict(raw['checks'][0])
    elif bad=='unknown_requirement':raw['checks'][1]['requirement_id']='fake'
    else:raw['checks'][0]['evidence_refs']=['operation:fake:fake']
    with pytest.raises(ValueError):normalize_review(raw,bound,evidence_catalog(store,job,store.task_facts(job)))


@pytest.mark.parametrize('new_proof',['observation','artifact','delivery'])
def test_old_picture_cannot_prove_new_query_or_new_picture(scenario,new_proof):
    store,old,tmp=scenario
    path=tmp/'old.png';path.write_bytes(b'existing artifact')
    child=store.enqueue(1,1,'image','old picture',parent_job_id=old['id'])
    store.update(child,status='succeeded',outcome='completed',result=str(path),delivery_state='delivered')
    store.record_receipt(child,'media',old['chat_id'],1)
    current=followup(store,old,'查询新资料并生成新的图片')
    bound=contract(store,current,item(current,new_proof))
    raw=verdict(bound,[['artifact:'+child]])
    with pytest.raises(ValueError,match='different request'):
        normalize_review(raw,bound,evidence_catalog(store,current,store.task_facts(current)))


def test_same_request_delivery_does_not_satisfy_web_observation(scenario):
    store,job,_=scenario
    child=store.enqueue(1,1,'image','picture',parent_job_id=job['id'])
    store.update(child,status='succeeded',delivery_state='delivered')
    store.record_receipt(child,'media',job['chat_id'],1)
    bound=contract(store,job,item(job,'observation','网页资料'),item(job,'delivery','图片交付'))
    result=normalize_review(verdict(bound,[['delivery:'+child],['delivery:'+child]]),bound,
                            evidence_catalog(store,job,store.task_facts(job)))
    assert not result['accepted'] and result['outcome']=='partial'
    assert not result['checks'][0]['satisfied'] and result['checks'][1]['satisfied']
    assert not result['completion_watch_only']


@pytest.mark.parametrize('foreign_state',['pending','uncertain'])
def test_unrelated_history_cannot_keep_current_chat_pending(scenario,foreign_state):
    store,old,_=scenario
    if foreign_state=='pending':
        store.enqueue(1,1,'image','previous pending',parent_job_id=old['id'])
    else:
        operation(store,old,'write_file',finish=False)
    current=followup(store,old)
    bound=contract(store,current)
    result=normalize_review(verdict(bound,[['input:'+current['id']]]),bound,
                            evidence_catalog(store,current,store.task_facts(current)))
    assert result['accepted'] and result['outcome']=='completed' and not result['repair']
    assert not result['completion_watch_only']


@pytest.mark.parametrize('channel',['app','telegram'])
@pytest.mark.parametrize('file_state',['ready','missing','empty','symlink'])
def test_artifact_and_channel_delivery_are_distinct_verified_stages(scenario,channel,file_state):
    store,old,tmp=scenario
    store.update(old['id'],status='succeeded')
    session=store.create_session(old['account_id'],'artifact test')
    jid=store.enqueue(1,None,'chat','创建文件并交付',session_id=session['id'],channel=channel)
    job=store.get(jid)
    child=store.enqueue(1,job['chat_id'],'image','new media',parent_job_id=jid)
    path=tmp/'artifact'
    if file_state=='ready':path.write_bytes(b'file bytes')
    elif file_state=='empty':path.write_bytes(b'')
    elif file_state=='symlink':
        target=tmp/'other';target.write_bytes(b'file bytes');path.symlink_to(target)
    store.update(child,status='succeeded',outcome='completed',result=str(path))
    if channel=='telegram':store.record_receipt(child,'media',job['chat_id'],44)
    catalog={e['id']:e for e in evidence_catalog(store,job,store.task_facts(job))}
    assert (catalog['artifact:'+child]['status']=='verified') is (file_state=='ready')
    # Telegram acceptance remains real after a local file is deleted. App
    # availability requires the actual file, not an invented send receipt.
    assert (catalog['delivery:'+child]['status']=='verified') is (channel=='telegram' or file_state=='ready')


def test_only_pending_requirements_can_create_completion_watch(scenario):
    store,job,_=scenario
    child=store.enqueue(1,1,'image','picture',parent_job_id=job['id'])
    bound=contract(store,job,item(job,'artifact'),item(job,'observation','missing query'))
    raw=verdict(bound,[['artifact:'+child],[]],['pending','missing'],accepted=False,outcome='partial',repair=True)
    result=normalize_review(raw,bound,evidence_catalog(store,job,store.task_facts(job)))
    assert result['outcome']=='partial' and not result['completion_watch_only'] and result['completion_watch']==[]
    pending=contract(store,job,item(job,'artifact'))
    good=normalize_review(verdict(pending,[['artifact:'+child]],['pending'],outcome='pending'),pending,
                          evidence_catalog(store,job,store.task_facts(job)))
    assert good['accepted'] and not good['repair'] and good['completion_watch_only']
    assert good['completion_watch']==[child]


def test_user_prerequisite_never_triggers_another_execution_loop(scenario):
    store,job,_=scenario
    bound=contract(store,job,item(job,'observation'),missing=['账号访问'])
    raw=verdict(bound,states=['needs_input'],outcome='needs_input',repair=True,missing_user_inputs=['账号访问'])
    result=normalize_review(raw,bound,evidence_catalog(store,job,store.task_facts(job)))
    assert result['accepted'] and result['outcome']=='needs_input' and not result['repair']


@pytest.mark.parametrize('proof_kind',['artifact','delivery','operation','effect'])
def test_durable_approval_wait_does_not_become_blocked_or_invent_missing_information(scenario,proof_kind):
    store,job,_=scenario
    bound=contract(store,job,item(job,proof_kind))
    if proof_kind in ('operation','effect'):
        proof=operation(store,job,'write_file',finish=False)
        with store.db:
            store.db.execute("UPDATE tool_operations SET status='waiting_approval' WHERE job_id=?",(job['id'],))
    else:
        child=store.enqueue(1,1,'video','An unrendered scene',parent_job_id=job['id'])
        store.update(child,status='waiting_approval',outcome='needs_input')
        proof=proof_kind+':'+child
    catalog=evidence_catalog(store,job,store.task_facts(job))
    assert next(entry for entry in catalog if entry['id']==proof)['status']=='needs_input'
    result=normalize_review(verdict(bound,[[proof]],['needs_input'],outcome='needs_input'),bound,catalog)
    assert result['accepted'] and result['outcome']=='needs_input' and not result['repair']
    assert result['missing_user_inputs']==[]
    assert not result['completion_watch_only'] and result['completion_watch']==[]


def test_truthful_partial_blocker_remains_deliverable(scenario):
    store,job,_=scenario
    bound=contract(store,job,item(job),item(job,'observation','仍然缺少资料'))
    raw=verdict(bound,[['input:'+job['id']],[]],['satisfied','missing'],outcome='partial')
    result=normalize_review(raw,bound,evidence_catalog(store,job,store.task_facts(job)))
    assert result['accepted'] and result['outcome']=='partial' and not result['repair']


@pytest.mark.parametrize('reported_outcome',['partial','blocked','pending','needs_input'])
def test_passed_checks_cannot_promote_incomplete_semantic_verdict(scenario,reported_outcome):
    store,job,_=scenario
    bound=contract(store,job)
    raw=verdict(bound,[['input:'+job['id']]],outcome=reported_outcome)
    result=normalize_review(raw,bound,evidence_catalog(store,job,store.task_facts(job)))
    # A valid input reference only identifies the request. It cannot override
    # the reviewer's conclusion that the delivered text is not the full result.
    assert result['accepted'] and result['outcome']=='partial' and not result['repair']
    assert result['model_outcome']==reported_outcome and result['checks'][0]['satisfied']
    assert not result['completion_watch_only'] and result['completion_watch']==[]


def test_accepted_and_repair_cannot_both_stop_and_continue(scenario):
    store,job,_=scenario
    bound=contract(store,job)
    result=normalize_review(verdict(bound,[['input:'+job['id']]],repair=True),bound,
                            evidence_catalog(store,job,store.task_facts(job)))
    assert not result['accepted'] and result['repair'] and result['outcome']=='partial'


@pytest.mark.asyncio
@pytest.mark.parametrize('stage',['contract','review'])
async def test_invalid_model_protocol_preserves_candidate_for_bounded_repair(scenario,stage):
    store,job,_=scenario
    engine=SimpleNamespace(chat=AsyncMock(return_value='not json'))
    reviewer=OutcomeReviewer(engine,store)
    if stage=='review':reviewer.contract=AsyncMock(return_value=contract(store,job))
    result=await reviewer.review(job,'unchanged candidate')
    assert result['answer']=='unchanged candidate' and not result['accepted']
    assert result['repair'] and result['review_stage']==stage and not result['completion_watch_only']


def test_incorrect_global_label_does_not_reject_truthful_pending_reply(scenario):
    store,job,_=scenario
    child=store.enqueue(1,1,'image','picture',parent_job_id=job['id'])
    bound=contract(store,job,item(job,'artifact'))
    raw=verdict(bound,[['artifact:'+child]],['pending'],outcome='completed')
    result=normalize_review(raw,bound,evidence_catalog(store,job,store.task_facts(job)))
    assert result['model_outcome']=='completed' and result['outcome']=='pending'
    assert result['accepted'] and not result['repair'] and result['completion_watch_only']


@pytest.mark.asyncio
async def test_submitted_video_corrects_cached_observation_contract(scenario):
    store,job,_=scenario
    raw={'deliverables':[item(job,'observation','实际生成视频文件')],
         'known_missing_inputs':[]}
    engine=SimpleNamespace(chat=AsyncMock(return_value=json.dumps(raw,ensure_ascii=False)))
    reviewer=OutcomeReviewer(engine,store)
    before=await reviewer.contract(job,store.task_facts(job))
    assert before['deliverables'][0]['proof_kind']=='observation'
    child=store.enqueue(1,1,'video','a blue ball rolls',parent_job_id=job['id'])
    after=await reviewer.contract(job,store.task_facts(job))
    assert engine.chat.await_count==1
    assert after['deliverables'][0]['proof_kind']=='artifact'
    pending=normalize_review(verdict(after,[['artifact:'+child]],['pending'],outcome='pending'),
                             after,evidence_catalog(store,job,store.task_facts(job)))
    assert pending['accepted'] and pending['outcome']=='pending' and pending['completion_watch']==[child]


@pytest.mark.asyncio
async def test_truncated_current_requirement_can_never_be_declared_complete(scenario):
    store,job,_=scenario
    store.update(job['id'],prompt='A'*12000)
    incoming=store.enqueue(1,1,'chat','B'*12000)
    store.absorb_input(job,store.get(incoming))
    bound=contract(store,job,item(job,sources=[job['id'],incoming]))
    raw=verdict(bound,[['input:'+incoming]])
    engine=SimpleNamespace(chat=AsyncMock(return_value=json.dumps(raw)))
    reviewer=OutcomeReviewer(engine,store)
    reviewer.contract=AsyncMock(return_value=bound)
    result=await reviewer.review(job,'candidate')
    payload=json.loads(engine.chat.await_args.args[0][1]['content'])
    assert not payload['task']['current_inputs_complete']
    assert not result['accepted'] and result['outcome']!='completed'
    assert result['answer']=='candidate'


def test_catalog_budget_preserves_real_proof_ids_and_exposes_omissions(scenario):
    store,job,_=scenario
    bound=contract(store,job,item(job,'observation'))
    for index in range(50):
        operation(store,job,'web_read',f'read-{index}',{'text':'large proof '*3000})
    full=evidence_catalog(store,job,store.task_facts(job))
    visible,coverage=catalog_view(full,bound,max_chars=6000)
    assert len(json.dumps(visible,ensure_ascii=False,separators=(',',':')))<=6000
    assert coverage['omitted_items']>0
    assert {e['id'] for e in visible}<={e['id'] for e in full}
    assert f"operation:{job['id']}:read-49" in {e['id'] for e in visible}
    omitted=next(e['id'] for e in full if e['id'] not in {v['id'] for v in visible})
    with pytest.raises(ValueError,match='unknown'):
        normalize_review(verdict(bound,[[omitted]]),bound,visible)


@pytest.mark.parametrize('data,complete',[
    ({'excerpt':'partial','truncated':True},False),
    ({'result':{'excerpt':'partial','truncated':True}},True),
])
def test_truncated_evidence_cannot_certify_satisfied_requirement(scenario,data,complete):
    store,job,_=scenario
    bound=contract(store,job,item(job,'observation'))
    proof={'id':'read-proof','kind':'operation','source_job_id':job['id'],
           'status':'verified','proof_kinds':['observation'],'data':data,'data_complete':complete}
    result=normalize_review(verdict(bound,[['read-proof']]),bound,[proof])
    assert not result['accepted'] and result['outcome']!='completed' and result['repair']


@pytest.mark.parametrize('state,missing,outcome',[
    ('needs_input',['Access to the requested private system'],'needs_input'),
    ('missing',[],'blocked'),
])
def test_incomplete_source_does_not_reject_truthful_incomplete_reply(scenario,state,missing,outcome):
    store,job,_=scenario
    bound={**contract(store,job),'source_coverage':{job['id']:False}}
    raw=verdict(bound,[[]],[state],missing_user_inputs=missing,repair=False)
    result=normalize_review(raw,bound,[])
    assert result['accepted'] and result['outcome']==outcome and not result['repair']


def test_complete_operation_content_is_retained_when_catalog_budget_allows(scenario):
    store,job,_=scenario
    bound=contract(store,job,item(job,'observation'))
    proof=operation(store,job,'web_read',result={'text':'checked source '*250})
    visible,_=catalog_view(evidence_catalog(store,job,store.task_facts(job)),bound)
    result=normalize_review(verdict(bound,[[proof]]),bound,visible)
    entry=next(e for e in visible if e['id']==proof)
    assert entry['data_complete'] and len(entry['data']['result']['text'])>1800
    assert result['outcome']=='completed'


@pytest.mark.asyncio
async def test_complete_review_request_budget_includes_schema_and_candidate(scenario):
    store,job,_=scenario
    store.update(job['id'],prompt='文'*12000)
    job=store.get(job['id'])
    for index in range(30):
        operation(store,job,'web_read',f'read-{index}',{'text':'观测'*2000})
    bound=contract(store,job)
    engine=SimpleNamespace(chat=AsyncMock(return_value=json.dumps(verdict(bound,[['input:'+job['id']]]))))
    reviewer=OutcomeReviewer(engine,store)
    reviewer.contract=AsyncMock(return_value=bound)
    candidate='回答'*800
    result=await reviewer.review(job,candidate)
    call=engine.chat.await_args
    payload=json.loads(call.args[0][1]['content'])
    assert request_size(call.args[0],call.kwargs['schema'])<=MAX_REVIEW_INPUT_TOKENS
    assert payload['candidate']==candidate and result['answer']==candidate
    assert payload['catalog_coverage']['omitted_items']>0
    inputs=[e for e in payload['evidence_catalog'] if e['kind']=='input']
    assert inputs and inputs[0]['data']=={'request_ref':job['id']}
    schema=call.kwargs['schema']['properties']['checks']['items']['properties']
    assert schema['requirement_id']['enum']==[bound['deliverables'][0]['requirement_id']]
    assert set(schema['evidence_refs']['items']['enum'])=={e['id'] for e in payload['evidence_catalog']}


@pytest.mark.asyncio
async def test_oversized_candidate_returns_review_error_without_model_call(scenario):
    store,job,_=scenario
    engine=SimpleNamespace(chat=AsyncMock())
    reviewer=OutcomeReviewer(engine,store)
    reviewer.contract=AsyncMock(return_value=contract(store,job))
    candidate='界'*20000
    result=await reviewer.review(job,candidate)
    assert engine.chat.await_count==0 and result['review_stage']=='review'
    assert result['review_error']=='RuntimeError' and result['answer']==candidate
    assert not result['accepted']


@pytest.mark.asyncio
async def test_contract_schema_restricts_sources_to_visible_real_requests(scenario):
    store,job,_=scenario
    engine=SimpleNamespace(chat=AsyncMock(return_value=json.dumps({'deliverables':[item(job)],'known_missing_inputs':[]})))
    await OutcomeReviewer(engine,store).contract(job,store.task_facts(job))
    call=engine.chat.await_args
    sources=call.kwargs['schema']['properties']['deliverables']['items']['properties']['source_job_ids']['items']
    assert sources['enum']==[job['id']]
    assert request_size(call.args[0],call.kwargs['schema'])<=MAX_REVIEW_INPUT_TOKENS


def resolution(bound,refs,index=0,prerequisite=0):
    return {'prerequisite_id':bound['prerequisites'][prerequisite]['prerequisite_id'],
            'requirement_id':bound['deliverables'][index]['requirement_id'],'evidence_refs':refs}


def test_explaining_all_blockers_does_not_complete_the_unavailable_actual_goal(scenario):
    store,job,_=scenario
    # Guard even against an imperfect contract that omitted its blocked data
    # requirement: its explicit prerequisites still cannot vanish implicitly.
    bound=contract(store,job,item(job,description='Explain the missing access'),missing=['Private data access'])
    result=normalize_review(verdict(bound,[['input:'+job['id']]]),bound,
                            evidence_catalog(store,job,store.task_facts(job)))
    assert result['checks'][0]['satisfied']
    assert result['accepted'] and result['outcome']=='needs_input' and not result['repair']
    assert result['missing_user_inputs']==['Private data access'] and not result['completion_watch_only']


def test_combined_actual_goal_and_explanation_preserve_blocker_but_advice_alone_completes(scenario):
    store,job,_=scenario
    combined=contract(store,job,item(job,'observation','Obtain the requested private total'),
                      item(job,description='Explain missing prerequisites'),missing=['Private data access'])
    raw=verdict(combined,[[],['input:'+job['id']]],['needs_input','satisfied'])
    catalog=evidence_catalog(store,job,store.task_facts(job))
    blocked=normalize_review(raw,combined,catalog)
    assert blocked['accepted'] and blocked['outcome']=='needs_input' and not blocked['repair']
    advice=contract(store,job,item(job,description='Only explain how to prepare for a future query'))
    completed=normalize_review(verdict(advice,[['input:'+job['id']]]),advice,catalog)
    assert completed['accepted'] and completed['outcome']=='completed'
    assert not completed['missing_user_inputs']


def test_real_tool_observation_explicitly_clears_earlier_missing_prerequisite(scenario):
    store,job,_=scenario
    bound=contract(store,job,item(job,'observation'),missing=['Private data access'])
    proof=operation(store,job,'web_read',result={'authorized':True,'requested_total':391})
    raw=verdict(bound,[[proof]],resolved_missing_inputs=[resolution(bound,[proof])])
    result=normalize_review(raw,bound,evidence_catalog(store,job,store.task_facts(job)))
    assert result['accepted'] and result['outcome']=='completed' and not result['missing_user_inputs']


def test_resolving_one_prerequisite_does_not_erase_another(scenario):
    store,job,_=scenario
    bound=contract(store,job,item(job,'observation'),missing=['Data access','Required accounting definition'])
    proof=operation(store,job,'web_read')
    raw=verdict(bound,[[proof]],resolved_missing_inputs=[resolution(bound,[proof])])
    result=normalize_review(raw,bound,evidence_catalog(store,job,store.task_facts(job)))
    assert result['accepted'] and result['outcome']=='needs_input'
    assert result['missing_user_inputs']==['Required accounting definition'] and not result['repair']


@pytest.mark.parametrize('bad',['input','foreign','truncated','failed','unreferenced','fake','duplicate','reasoning'])
def test_prerequisites_cannot_be_resolved_by_unqualified_receipts(scenario,bad):
    store,job,_=scenario
    bound=contract(store,job,item(job,'reasoning' if bad in ('input','reasoning') else 'observation'),
                   missing=['Private data access'])
    proof={'id':'proof','kind':'operation','source_job_id':job['id'],'status':'verified',
           'proof_kinds':['reasoning' if bad in ('input','reasoning') else 'observation'],'data':{'value':391}}
    if bad=='input':proof['kind']='input'
    elif bad=='foreign':proof['source_job_id']='different-request'
    elif bad=='truncated':proof['data_complete']=False
    elif bad=='failed':proof['status']='failed'
    raw=verdict(bound,[[] if bad=='unreferenced' else ['proof']],
                resolved_missing_inputs=[resolution(bound,['fake' if bad=='fake' else 'proof'])])
    if bad=='duplicate':raw['resolved_missing_inputs']*=2
    with pytest.raises(ValueError):normalize_review(raw,bound,[proof])


@pytest.mark.asyncio
async def test_new_user_input_rebuilds_contract_and_can_remove_old_prerequisite(scenario):
    store,job,_=scenario
    first={'deliverables':[item(job,'observation')],'known_missing_inputs':['Private data access']}
    engine=SimpleNamespace(chat=AsyncMock(return_value=json.dumps(first)))
    reviewer=OutcomeReviewer(engine,store)
    before=await reviewer.contract(job,store.task_facts(job))
    incoming=store.enqueue(1,1,'chat','Here is the exact requested source data: total=391.')
    store.absorb_input(job,store.get(incoming))
    engine.chat.return_value=json.dumps({'deliverables':[item(job,'provided_data',sources=[job['id'],incoming])],
                                         'known_missing_inputs':[]})
    after=await reviewer.contract(job,store.task_facts(job))
    assert engine.chat.await_count==2 and before['prerequisites'] and not after['prerequisites']


@pytest.mark.asyncio
async def test_prerequisite_resolution_schema_only_exposes_real_ids(scenario):
    store,job,_=scenario
    bound=contract(store,job,item(job,'observation'),missing=['Data access'])
    proof=operation(store,job,'web_read')
    raw=verdict(bound,[[proof]],resolved_missing_inputs=[resolution(bound,[proof])])
    engine=SimpleNamespace(chat=AsyncMock(return_value=json.dumps(raw)))
    reviewer=OutcomeReviewer(engine,store)
    reviewer.contract=AsyncMock(return_value=bound)
    result=await reviewer.review(job,'The requested data has been obtained.')
    props=engine.chat.await_args.kwargs['schema']['properties']['resolved_missing_inputs']['items']['properties']
    assert props['prerequisite_id']['enum']==[bound['prerequisites'][0]['prerequisite_id']]
    assert props['requirement_id']['enum']==[bound['deliverables'][0]['requirement_id']]
    assert proof in props['evidence_refs']['items']['enum'] and result['outcome']=='completed'


def uncovered_goal(job,proof='observation',missing=None,mapped=None):
    return {'source_job_id':job['id'],'goals':[{
        'description':'Obtain the actual result requested in the original message',
        'proof_kind':proof,'contract_requirement_ids':mapped or [],'missing_user_inputs':missing or []}]}


def test_independent_source_review_catches_actual_goal_misclassified_as_explanation(scenario):
    store,job,_=scenario
    bound=contract(store,job,item(job,description='Explain missing prerequisites'))
    coverage=[uncovered_goal(job,missing=['Access to the original data'],
                            mapped=[bound['deliverables'][0]['requirement_id']])]
    raw=verdict(bound,[['input:'+job['id']]],request_coverage=coverage)
    result=normalize_review(raw,bound,evidence_catalog(store,job,store.task_facts(job)))
    assert result['accepted'] and result['outcome']=='needs_input' and not result['repair']
    assert result['contract_gaps'][0]['proof_kind']=='observation'
    assert result['contract_gaps'][0]['reason']=='proof_kind_mismatch'
    assert not result['completion_watch_only']


@pytest.mark.parametrize('contract_kind,goal_kind',[
    ('reasoning','observation'),('observation','reasoning'),
    ('provided_data','observation'),('reasoning','provided_data')])
def test_shared_calculator_receipt_resolves_proof_labels_without_inventing_another_goal(scenario,contract_kind,goal_kind):
    store,job,_=scenario
    bound=contract(store,job,item(job,contract_kind,'Determine the comparison result'))
    proof=operation(store,job,'calculate',result={'expression':'4.3 < 4.6 - 0.3','value':False})
    raw=verdict(bound,[[proof]],request_coverage=[uncovered_goal(job,goal_kind,
        mapped=[bound['deliverables'][0]['requirement_id']])],
        math_claims=[{'expression':'4.3 < 4.6 - 0.3','claimed_value':'false','round_digits':None}])
    result=normalize_review(raw,bound,evidence_catalog(store,job,store.task_facts(job)))
    assert result['accepted'] and result['outcome']=='completed' and not result['repair']
    assert result['contract_gaps']==[]


@pytest.mark.parametrize('tool,result',[
    ('calculate',{'expression':'4.3 < 4.6 - 0.3','value':False}),
    ('api_get',{'url':'https://example.test/data','selected':{'total':391}})])
def test_successful_read_invocation_proves_operation_without_proving_effect(scenario,tool,result):
    store,job,_=scenario
    proof=operation(store,job,tool,result=result)
    catalog=evidence_catalog(store,job,store.task_facts(job))
    receipt=next(entry for entry in catalog if entry['id']==proof)
    assert receipt['data']['effect']=='read' and receipt['status']=='verified'
    assert {'operation','observation'}<=set(receipt['proof_kinds'])
    assert 'effect' not in receipt['proof_kinds']
    bound=contract(store,job,item(job,'operation','Invoke the requested read tool'))
    raw=verdict(bound,[[proof]])
    # Two independent interpretations of a real invocation are compatible only
    # because its actual cited receipt supports both kinds.
    raw['request_coverage'][0]['goals'][0]['proof_kind']='observation'
    completed=normalize_review(raw,bound,catalog)
    assert completed['accepted'] and completed['outcome']=='completed'
    assert not completed['contract_gaps'] and not completed['repair']
    write=contract(store,job,item(job,'effect','Persist the requested change'))
    rejected=normalize_review(verdict(write,[[proof]]),write,catalog)
    assert not rejected['accepted'] and rejected['outcome']!='completed'
    assert 'lacks effect evidence' in rejected['feedback']


def test_independent_effect_goal_cannot_be_downgraded_to_read_invocation(scenario):
    store,job,_=scenario
    bound=contract(store,job,item(job,'operation','Call an API'))
    proof=operation(store,job,'api_get',result={'available':True})
    raw=verdict(bound,[[proof]])
    raw['request_coverage'][0]['goals'][0].update(
        description='Create the requested reservation',proof_kind='effect')
    result=normalize_review(raw,bound,evidence_catalog(store,job,store.task_facts(job)))
    assert result['checks'][0]['satisfied']
    assert result['outcome']=='partial'
    assert result['contract_gaps'][0]['proof_kind']=='effect'
    assert result['contract_gaps'][0]['reason']=='proof_kind_mismatch'


@pytest.mark.parametrize('tool,complete',[('calculate',False),('api_get',False),('write_file',True)])
def test_v5_operation_contract_keeps_its_original_mutation_requirement(scenario,tool,complete):
    store,job,_=scenario
    bound=contract(store,job,item(job,'operation','Save or change the requested state'))
    bound['schema_version']=5
    proof=operation(store,job,tool)
    raw=verdict(bound,[[proof]])
    result=normalize_review(raw,bound,evidence_catalog(store,job,store.task_facts(job)))
    assert (result['outcome']=='completed') is complete
    # Conservative compatibility is local to normalization, never a mutation
    # of persisted legacy contracts or the model's source verdict.
    assert bound['deliverables'][0]['proof_kind']=='operation'
    assert raw['request_coverage'][0]['goals'][0]['proof_kind']=='operation'
    if not complete:
        assert 'lacks effect evidence' in result['feedback']


def test_real_acceptance_calculation_receipts_complete_without_repeating_tools(scenario):
    store,job,_=scenario
    fixture=json.loads((Path(__file__).parent/'fixtures/outcome_calculator_read_invocation.json').read_text())
    source=fixture['source_job_id']
    saved=fixture['contract']
    raw_contract={'deliverables':[{**{k:v for k,v in d.items() if k!='requirement_id'},
                                  'source_job_ids':[job['id']]} for d in saved['deliverables']],
                  'known_missing_inputs':saved['known_missing_inputs']}
    bound=bind_contract(raw_contract,job,store.task_facts(job))
    requirements={old['requirement_id']:new['requirement_id']
                  for old,new in zip(saved['deliverables'],bound['deliverables'])}
    for row in fixture['operations']:
        spec=next(s for s in BUILTINS if s.name==row['name'])
        assert spec.effect==row['effect']=='read' and row['status']=='succeeded'
        store.begin_operation(job['id'],row['call_id'],spec,row['arguments'])
        store.finish_operation(job['id'],row['call_id'],result=row['result'])
    original=fixture['original_normalized_review']
    assert original['outcome']=='partial' and original['model_outcome']=='completed'
    assert 'lacks operation evidence' in original['feedback']
    raw=verdict(bound,math_claims=original['math_claims'])
    raw['checks']=[{**{k:check[k] for k in ('evidence','evidence_refs')},
                    'requirement_id':requirements[check['requirement_id']], 'state':'satisfied',
                    'evidence_refs':[ref.replace(source,job['id']) for ref in check['evidence_refs']]}
                   for check in original['checks']]
    raw['request_coverage']=[{'source_job_id':job['id'],'goals':[
        {**goal,'contract_requirement_ids':[requirements[ident] for ident in goal['contract_requirement_ids']]}
        for goal in original['request_coverage'][0]['goals']]}]
    result=normalize_review(raw,bound,evidence_catalog(store,job,store.task_facts(job)))
    assert result['accepted'] and result['outcome']=='completed' and not result['repair']
    assert not result['contract_gaps'] and not result['evidence']['math_errors']
    assert store.db.execute('SELECT count(*) FROM tool_operations WHERE job_id=?',(job['id'],)).fetchone()[0]==2


@pytest.mark.parametrize('unusable',['other_requirement','uncited','truncated','unfinished'])
def test_proof_label_compatibility_requires_the_mapped_check_to_cite_a_complete_success(scenario,unusable):
    store,job,_=scenario
    bound=contract(store,job,item(job,'reasoning','Comparison'),item(job,'reasoning','Explanation'))
    proof=operation(store,job,'calculate',result={'value':False},finish=unusable!='unfinished')
    refs=[['input:'+job['id']],[proof] if unusable=='other_requirement' else ['input:'+job['id']]]
    if unusable in ('truncated','unfinished'):
        refs[0]=[proof]
    catalog=evidence_catalog(store,job,store.task_facts(job))
    if unusable=='truncated':
        next(entry for entry in catalog if entry['id']==proof)['data_complete']=False
    raw=verdict(bound,refs)
    raw['request_coverage'][0]['goals'][0]['proof_kind']='observation'
    result=normalize_review(raw,bound,catalog)
    assert result['outcome']!='completed'
    assert any(gap['reason']=='proof_kind_mismatch' for gap in result['contract_gaps'])


def test_unmapped_actual_action_cannot_complete_even_when_all_contract_checks_pass(scenario):
    store,job,_=scenario
    bound=contract(store,job)
    raw=verdict(bound,[['input:'+job['id']]],request_coverage=[uncovered_goal(job,'operation')])
    result=normalize_review(raw,bound,evidence_catalog(store,job,store.task_facts(job)))
    assert result['accepted'] and result['outcome']=='partial' and not result['repair']
    assert result['contract_gaps'][0]['reason']=='missing_requirement'
    assert result['checks'][0]['satisfied']


@pytest.mark.parametrize('bad',['omitted_input','duplicate_input','foreign_source','fake_requirement'])
def test_independent_coverage_cannot_omit_or_forge_current_sources(scenario,bad):
    store,job,_=scenario
    incoming=store.enqueue(1,1,'chat','One more current requirement')
    store.absorb_input(job,store.get(incoming))
    bound=contract(store,job,item(job,sources=[job['id'],incoming]))
    raw=verdict(bound,[['input:'+job['id']]])
    if bad=='omitted_input':raw['request_coverage'].pop()
    elif bad=='duplicate_input':raw['request_coverage'][1]=raw['request_coverage'][0]
    elif bad=='foreign_source':raw['request_coverage'][0]['source_job_id']='old-unrelated-job'
    else:raw['request_coverage'][0]['goals'][0]['contract_requirement_ids']=['fake-requirement']
    with pytest.raises(ValueError):
        normalize_review(raw,bound,evidence_catalog(store,job,store.task_facts(job)))


def test_uncovered_goal_prevents_unrelated_async_completion_watch(scenario):
    store,job,_=scenario
    bound=contract(store,job,item(job,'artifact'))
    child=store.enqueue(1,1,'image','picture',parent_job_id=job['id'])
    raw=verdict(bound,[['artifact:'+child]],['pending'])
    raw['request_coverage'][0]['goals']+=uncovered_goal(job)['goals']
    result=normalize_review(raw,bound,evidence_catalog(store,job,store.task_facts(job)))
    assert result['outcome']=='partial' and not result['completion_watch_only']


@pytest.mark.asyncio
async def test_discovered_goal_repairs_cached_contract_and_next_review_can_converge(scenario):
    store,job,_=scenario
    # A prior input revision or schema version must not intercept repair of the
    # newly generated contract for this execution.
    stale=contract(store,job,item(job,description='An earlier interpretation'))
    with store.db:
        store.db.execute('INSERT INTO task_contracts VALUES (?,?,?,?)',
            (job['id'],'old-input-hash',json.dumps(stale,ensure_ascii=False),time.time()-1))
    initial={'deliverables':[item(job,description='Explain the current situation')],'known_missing_inputs':[]}
    bound=bind_contract(initial,job,store.task_facts(job))
    first=verdict(bound,[['input:'+job['id']]],request_coverage=[uncovered_goal(job)],repair=True,accepted=False)
    engine=SimpleNamespace(chat=AsyncMock(side_effect=[json.dumps(initial),json.dumps(first)]))
    reviewer=OutcomeReviewer(engine,store)
    result=await reviewer.review(job,'The source has not been read yet.')
    assert result['outcome']=='partial' and result['contract_gaps']
    amended=json.loads(store.db.execute('SELECT contract FROM task_contracts WHERE job_id=? ORDER BY created DESC LIMIT 1',(job['id'],)).fetchone()[0])
    assert [d['proof_kind'] for d in amended['deliverables']]==['reasoning','observation']
    proof=operation(store,job,'web_read',result={'actual_requested_result':391})
    engine.chat.side_effect=None
    engine.chat.return_value=json.dumps(verdict(amended,[['input:'+job['id']],[proof]]))
    complete=await reviewer.review(job,'The actual requested result is 391.')
    assert complete['accepted'] and complete['outcome']=='completed' and not complete['contract_gaps']
    assert engine.chat.await_count==3  # next contract uses its corrected cache


@pytest.mark.asyncio
async def test_coverage_schema_and_payload_put_original_sources_before_contract(scenario):
    store,job,_=scenario
    bound=contract(store,job)
    engine=SimpleNamespace(chat=AsyncMock(return_value=json.dumps(verdict(bound,[['input:'+job['id']]]))))
    reviewer=OutcomeReviewer(engine,store)
    reviewer.contract=AsyncMock(return_value=bound)
    result=await reviewer.review(job,'Only preparation advice was requested.')
    call=engine.chat.await_args
    schema=call.kwargs['schema']
    assert next(iter(schema['properties']))=='request_coverage'
    coverage=schema['properties']['request_coverage']['items']['properties']
    assert coverage['source_job_id']['enum']==[job['id']]
    assert coverage['goals']['items']['properties']['contract_requirement_ids']['items']['enum']==[
        bound['deliverables'][0]['requirement_id']]
    payload=json.loads(call.args[0][1]['content'])
    assert list(payload).index('task')<list(payload).index('contract')
    assert result['accepted'] and result['outcome']=='completed' and not result['contract_gaps']
