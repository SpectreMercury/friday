"""Owned, addressable facts for requirement-by-requirement outcome review.

``operation`` proves a real tool invocation, including read-only calls. ``effect``
is a separate proof kind for mutations; an observation cannot establish a write.
Neither proves the requested semantics without the independent scoped review.
"""
import json
from pathlib import Path
from .context_view import bounded_json_view


INSPECTION_TOOLS = {'task_status','task_evidence','continue_task','plan_task','capabilities',
                    'list_skills','read_skill','system_status'}


def input_scope(job, facts):
    """Topic history is background; this execution and absorbed inputs are current."""
    current = [r for r in facts['user_requests'] if r['job_id']==job['id']
               or (r.get('outcome')=='continued' and r['created']>=job['created'])]
    ids = {r['job_id'] for r in current}
    return {'current_inputs':current,
            'historical_inputs':[r for r in facts['user_requests'] if r['job_id'] not in ids],
            'current_input_ids':sorted(ids)}


def _json(value):
    return json.loads(value) if value else None


def _summary(value, limit=1800):
    text=json.dumps(value,ensure_ascii=False)
    return value if len(text)<=limit else {'excerpt':text[:limit],'truncated':True}


def _artifact(path):
    try:
        file=Path(path) if path else None
        if file and not file.is_symlink() and file.is_file() and file.stat().st_size>0:
            return {'filename':file.name,'bytes':file.stat().st_size}
    except OSError:
        pass
    return None


def evidence_catalog(store, job, facts):
    """Proof ids identify one fact and never imply that it proves every goal."""
    catalog=[]
    for request in facts['user_requests']:
        catalog.append({'id':'input:'+request['job_id'],'kind':'input','source_job_id':request['job_id'],
                        'status':'verified','proof_kinds':['reasoning','provided_data'],
                        'data':{'text':request['text']}})
    records=[dict(r) for r in store.db.execute('''
        SELECT o.*,j.channel FROM tool_operations o JOIN jobs j ON j.id=o.job_id
        WHERE j.task_id=? AND j.account_id=? AND j.session_id=? ORDER BY o.started_at''',
        (facts['task_id'],job['account_id'],job['session_id']))]
    for row in records:
        if row['name'] in INSPECTION_TOOLS and not (row['name'] == 'capabilities' and (_json(row['result']) or {}).get('requires_user')):
            continue
        result,failure=_json(row['result']),_json(row['failure'])
        proof=['operation']
        if row['effect'] in ('read','arbitrary'):
            proof.append('observation')
        if row['effect'] not in ('read','state','submit'):
            proof.append('effect')
        if row['name'] in ('read_attachment','calculate'):
            proof += ['provided_data','reasoning']
        state={'succeeded':'verified','started':'uncertain','uncertain':'uncertain',
               'waiting_approval':'needs_input'}.get(row['status'],'failed')
        if state=='verified' and (result in (None,'',[],{}) or
                (isinstance(result,dict) and (result.get('error') or
                 result.get('retrieval_status') in ('empty','access_gate')))):
            state='unavailable'
        if failure and failure.get('requires_user'):
            state='needs_input'
        if isinstance(result, dict) and result.get('requires_user'):
            state='needs_input'
        if row['name'] == 'computer_screen' and isinstance(result, dict) and result.get('captured'):
            proof.append('observation')
        catalog.append({'id':f"operation:{row['job_id']}:{row['call_id']}",'kind':'operation',
                        'source_job_id':row['job_id'],'status':state,'proof_kinds':proof,
                        'data':{'name':row['name'],'effect':row['effect'],
                                'arguments':_json(row['arguments']),
                                'result':result,'failure':failure}})
        if row['name']=='generate_audio' and row['status']=='succeeded' and isinstance(result,dict):
            artifact=_artifact(result.get('path'))
            catalog.append({'id':f"artifact:{row['job_id']}:{row['call_id']}",'kind':'artifact',
                            'source_job_id':row['job_id'],'status':'verified' if artifact else 'unavailable',
                            'proof_kinds':['artifact'],'data':artifact or {}})
        if row['name'] == 'computer_screen' and row['status'] == 'succeeded' and isinstance(result, dict) and result.get('captured'):
            owned = store.db.execute("SELECT path,name FROM attachments WHERE id=? AND job_id=? AND session_id=? AND kind='image'",
                                    (result.get('attachment_id'), row['job_id'], job['session_id'])).fetchone()
            file = _artifact(owned['path']) if owned else None
            catalog.append({'id':f"artifact:{row['job_id']}:{row['call_id']}", 'kind':'artifact',
                            'source_job_id':row['job_id'], 'status':'verified' if file else 'unavailable',
                            'proof_kinds':['artifact'], 'data':file or {}})
            if row['channel'] == 'app':
                catalog.append({'id':f"delivery:{row['job_id']}:{row['call_id']}", 'kind':'delivery',
                                'source_job_id':row['job_id'], 'status':'verified' if file else 'unavailable',
                                'proof_kinds':['delivery'], 'data':{'channel':'app', 'availability':'available_in_session' if file else 'missing'}})

    children=[dict(r) for r in store.db.execute('''
        SELECT child.* FROM jobs child JOIN jobs parent ON child.parent_job_id=parent.id
        WHERE parent.task_id=? AND parent.account_id=? AND parent.session_id=?
          AND child.account_id=? AND child.session_id=? ORDER BY child.created''',
        (facts['task_id'],job['account_id'],job['session_id'],job['account_id'],job['session_id']))]
    for child in children:
        if child['kind'] not in ('image','video'):
            continue
        artifact=_artifact(child.get('result')) if child['status']=='succeeded' else None
        pending=child['status'] in ('queued','running')
        awaiting_approval=child['status']=='waiting_approval'
        state='verified' if artifact else 'needs_input' if awaiting_approval else 'pending' if pending else 'unavailable'
        common={'source_job_id':child['parent_job_id'],'data':{'child_id':child['id'],'kind':child['kind']}}
        catalog.append({'id':'artifact:'+child['id'],'kind':'artifact','status':state,
                        'proof_kinds':['artifact'],**common,
                        'data':{**common['data'],**(artifact or {})}})
        receipt=store.receipt(child['id'],'media')
        telegram=bool(child['channel']=='telegram' and child['status']=='succeeded' and receipt
                      and receipt['chat_id']==child['chat_id'] and receipt['message_id']>0)
        available=child['channel']=='app' and artifact is not None
        awaiting_delivery=bool(child['channel']=='telegram' and artifact and child.get('delivery_state')
                               in ('pending','sending','retrying'))
        delivery_state='verified' if telegram or available else 'needs_input' if awaiting_approval else 'pending' if pending or awaiting_delivery else 'unavailable'
        if child.get('delivery_state')=='uncertain' and not telegram:
            delivery_state='uncertain'
        catalog.append({'id':'delivery:'+child['id'],'kind':'delivery','status':delivery_state,
                        'proof_kinds':['delivery'],**common,'data':{**common['data'],
                        'channel':child['channel'],'availability':'available_in_session' if available else
                        'telegram_accepted_not_read_receipt' if telegram else 'not_verified',
                        'message_id':receipt['message_id'] if telegram else None}})

    # Explicit redelivery and voice sends are journaled under the requesting job.
    for receipt in store.db.execute('''SELECT r.*,j.channel FROM delivery_receipts r
            JOIN jobs j ON j.id=r.job_id WHERE j.task_id=? AND j.account_id=? AND j.session_id=?
            AND r.chat_id=j.chat_id AND r.message_id>0 AND (r.part LIKE 'artifact:%' OR r.part='voice')''',
            (facts['task_id'],job['account_id'],job['session_id'])):
        catalog.append({'id':f"delivery:{receipt['job_id']}:{receipt['part']}",'kind':'delivery',
                        'source_job_id':receipt['job_id'],'status':'verified','proof_kinds':['delivery'],
                        'data':{'channel':receipt['channel'],'message_id':receipt['message_id'],
                                'availability':'telegram_accepted_not_read_receipt','part':receipt['part']}})
    return catalog


def catalog_view(catalog, contract, max_chars=16000):
    """Keep real ids and typed metadata; never substitute a JSON excerpt for ids.

    Prioritize at least the recent candidates for every current requirement.
    The host validates only this exposed directory, so omitted facts cannot
    accidentally qualify as evidence the reviewer never saw.
    """
    current=set(contract['current_input_ids'])
    sources=current|{s for d in contract['deliverables'] for s in d['source_job_ids']}
    relevant=[e for e in catalog if e['source_job_id'] in sources]
    preferred=[e for e in relevant if e['kind']=='input']
    for requirement in contract['deliverables']:
        allowed=current|set(requirement['source_job_ids'])
        matches=[e for e in relevant if e['source_job_id'] in allowed
                 and requirement['proof_kind'] in e['proof_kinds'] and e['kind']!='input']
        preferred.extend(matches[-3:])
    preferred.extend(reversed(relevant))
    selected=[]
    seen=set()
    for entry in preferred:
        if entry['id'] in seen:
            continue
        seen.add(entry['id'])
        if entry['kind']=='input':
            visible={**entry,'data':{'request_ref':entry['source_job_id']}}
        else:
            visible={**entry,'data_complete':True}
            # Prefer the actual receipt when it fits. An excerpt is useful for
            # finding more evidence, but cannot substantiate satisfied claims.
            if len(json.dumps(selected+[visible],ensure_ascii=False,separators=(',',':')))>max_chars:
                view=bounded_json_view(entry['data'],max_chars=1000)
                visible={**entry,'data':view['value'],'data_complete':view['complete']}
        if len(json.dumps(selected+[visible],ensure_ascii=False,separators=(',',':')))<=max_chars:
            selected.append(visible)
    return selected,{'total_items':len(relevant),'visible_items':len(selected),
                     'omitted_items':len(relevant)-len(selected),'budget_characters':max_chars,
                     'complete':len(selected)==len(relevant) and all(e.get('data_complete',True) for e in selected)}
