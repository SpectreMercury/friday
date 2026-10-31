"""Task-scoped recovery facts. A failed request must survive into the next turn."""
import json
import re


def task_record(store, job):
    events = []
    for row in store.db.execute(
        "SELECT name,args,result,error FROM pi_tools WHERE job_id=? AND name NOT IN "
        "('plan_task','final_check','run_summary','task_status','task_evidence','continue_task') ORDER BY id DESC LIMIT 4",
        (job['id'],),
    ):
        result = json.loads(row['result'] or 'null')
        args = json.loads(row['args'])
        event = {'tool': row['name'], 'arguments': {key:str(args[key])[:2000 if key=='url' else 300] for key in ('query','url','name','job_id') if key in args},
                 'error': (row['error'] or '')[:400]}
        # Keep sources and retrieval quality, not whole pages or private file contents.
        if isinstance(result, dict):
            event['result'] = {key: result[key] for key in
                               ('results', 'url', 'title', 'retrieval_status', 'hint', 'job_id', 'status')
                               if key in result}
            if 'results' in event['result']:
                event['result']['results'] = [{key:str(item.get(key,''))[:2000 if key=='url' else 160] for key in ('title','url','snippet')}
                                             for item in event['result']['results'][:2]]
        events.append(event)
    return {'id': job['id'], 'request': job['prompt'][:1000], 'status': job['status'],
            'error': job.get('error'), 'outcome':job.get('outcome'),'delivery_state':job.get('delivery_state'),'delivery_error':job.get('delivery_error'), 'attempts': list(reversed(events))}


def recent_tasks(store, job):
    rows = store.db.execute(
        "SELECT * FROM jobs WHERE session_id=? AND created<? "
        "AND created>COALESCE((SELECT reset_at FROM session_resets WHERE session_id=?),0) "
        "AND kind='chat' AND status IN ('succeeded','failed','cancelled') AND id NOT IN (SELECT job_id FROM deleted_jobs) ORDER BY created DESC LIMIT 3",
        (job['session_id'], job['created'],job['session_id']),
    )
    return [task_record(store, dict(row)) for row in rows]


def resumable_research(store, job):
    """Resume only a direct follow-up to a failed read-only research task."""
    if not re.fullmatch(r'(?:为什么|为何|怎么|为啥).{0,12}(?:失败|没完成|没能完成|不行)[？?。！!\s]*|(?:继续|继续查|再试一次|重试)[？?。！!\s]*',job['prompt']):
        return None
    recent = recent_tasks(store,job)
    if not recent or recent[0]['status'] != 'failed':
        return None
    task = recent[0]
    names = {event['tool'] for event in task['attempts']}
    if names & {'web_search','web_read'} and names <= {'web_search','web_read','calculate','task_status'}:
        return task
    return None


def failure_reply(store, job, reason, *, execution_error=False):
    facts = task_record(store, job)
    if not execution_error and re.fullmatch(r'(?:为什么|为何|怎么|为啥).{0,12}(?:失败|没完成|没能完成|不行)[？?。！!\s]*',job['prompt']):
        recent = recent_tasks(store, job)
        if recent and recent[0]['status']=='failed':
            facts = recent[0]
    errors = list(dict.fromkeys(e['error'] for e in facts['attempts'] if e['error']))
    details = '；'.join(re.split(r'。|[,，;；](?=请|不要)', error, maxsplit=1)[0] for error in errors[-2:])
    # A handled tool error may precede an unrelated fatal host error. Do not
    # misreport that earlier tool attempt as the reason execution stopped.
    details = reason if execution_error else details or facts.get('error') or reason
    details = details.rstrip('。')
    return f'关于“{facts["request"][:160]}”：这次执行中断，未完成。具体阻碍：{details}。已保留本次请求和执行记录，后续可以接着处理。'


def review_handoff(store, job, answer):
    """Don't ask permission to retry research the user already requested."""
    if not re.search(r'(?:要我|需要我|希望我|你想让我|是否).{0,40}(?:再试|重试|重新|继续|换|搜索|查询).{0,25}[？?吗]', answer):
        return None
    facts = task_record(store, job)
    recent = recent_tasks(store, job)
    followup = bool(re.fullmatch(r'[\s，。？！?]*(?:为什么|为何|怎么|为啥).{0,12}(?:失败|没完成|没能完成|不行)[\s，。？！?]*', job['prompt']))
    target = recent[0] if followup and recent and recent[0]['status']=='failed' else facts
    # Restrict autonomous continuation to an observed web research task.
    if not any(event['tool'] in ('web_search','web_read') for event in target['attempts']):
        return None
    return {'accepted':False, 'answer':failure_reply(store,job,'检索仍未完成，已有执行记录可供续查'),
            'feedback':'原始检索请求已获授权，不要询问是否重试或把检索交回用户。请继续用只读工具换可用来源完成：'+target['request']+
                       '。若工具仍不可用，交代实际尝试、有效结果和确切缺失；不要猜测失败原因或编造评分。',
            'required_tool':None, 'repair_tools':['web_search','web_read','task_status'],
            'evidence':{'task':target}}
