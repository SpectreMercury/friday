"""Ground media status in durable jobs, files and channel receipts, not prose."""
import re
from datetime import datetime
from pathlib import Path

# A last line of defence for common status claims, not a universal language verifier.
CLAIM = re.compile(r'(?:图片|图像|视频|文件).{0,14}(?:已经|已|正在).{0,8}(?:生成|发送|发给|发到)|'
                   r'(?:已经|已)(?:成功)?(?:生成|发送|发给|发到)|生成(?:好了|成功了|完成了)|'
                   r'(?:正在|开始)(?:为你|给你)?生成|(?:I.ve|I have).{0,16}(?:sent|generated)|'
                   r'(?:image|video).{0,20}(?:has been|is being).{0,12}(?:sent|generated)', re.I)
NEGATIVE = re.compile(r'没有|未(?:能|经|提交|生成|发送|找到|完成)|无法|不能|不应|尚未|并未|没(?:有|能)|not |haven.t|hasn.t', re.I)


def claims_media(text):
    return any(CLAIM.search(sentence) and not NEGATIVE.search(sentence)
               for sentence in re.split(r'[。！？\n.!?]', text or ''))


def media_state(store, job):
    result = {key: job.get(key) for key in ('id','kind','status','created','parent_job_id')}
    file = Path(job['result']) if job.get('result') and job['kind'] in ('image','video') else None
    try:
        exists = bool(file and file.is_file() and file.stat().st_size > 0)
    except OSError:
        exists = False
    receipt = store.receipt(job['id'], 'media')
    redelivery = store.db.execute(
        'SELECT r.* FROM delivery_receipts r JOIN jobs p ON p.id=r.job_id WHERE r.part=? AND p.user_id=? AND p.chat_id=? AND r.chat_id=? ORDER BY r.sent_at DESC LIMIT 1',
        ('artifact:'+job['id'], job['user_id'], job['chat_id'], job['chat_id'])).fetchone()
    if redelivery and (not receipt or redelivery['sent_at'] > receipt['sent_at']):
        receipt = dict(redelivery)
    result.update(artifact_exists=exists, telegram_message_id=receipt['message_id'] if receipt else None,
                  delivery_error=job.get('delivery_error'), delivery_attempts=job.get('delivery_attempts',0))
    if job.get('channel')=='app':
        result['delivery_state']='available_in_session' if exists else 'not_available'
        result['artifact_url']=f"/v1/sessions/{job['session_id']}/tasks/{job['id']}/artifact" if exists else None
    elif receipt:
        result['delivery_state'] = 'telegram_accepted_not_read_receipt'
    elif job.get('delivery_state')=='uncertain':
        result['delivery_state']='uncertain_not_verified'
    elif job.get('delivery_error'):
        result['delivery_state'] = 'failed_not_delivered' if job.get('delivered') else 'failed_retry_pending'
    elif job.get('delivered'):
        result['delivery_state'] = 'legacy_or_suppressed_unverified'
    else:
        result['delivery_state'] = 'not_sent'
    return result


def children(store, job):
    return [dict(row) for row in store.db.execute(
        "SELECT * FROM jobs WHERE parent_job_id=? AND session_id=? ORDER BY created",
        (job['id'],job['session_id']))]


def snapshot(store, job):
    recent = [dict(row) for row in store.db.execute(
        "SELECT * FROM jobs WHERE session_id=? AND kind IN ('image','video') ORDER BY created DESC LIMIT 5",
        (job['session_id'],))]
    previous = [dict(row) for row in store.db.execute(
        "SELECT * FROM jobs WHERE session_id=? AND kind='chat' AND created<? ORDER BY created DESC LIMIT 5",
        (job['session_id'],job['created']))]
    return {'current_turn_media': [media_state(store,row) for row in children(store,job)],
            'recent_media': [media_state(store,row) for row in recent],
            'recent_turns': [{'id': row['id'], 'created': row['created'],
                              'submitted_media_ids': [child['id'] for child in children(store,row)],
                              'assistant_made_media_claim': claims_media(row.get('result'))}
                             for row in previous],
            'rule': '聊天文本不是执行证据。旧图不是新请求的结果。Telegram接受不表示用户已看到。旧发送标记没有消息ID则不可核实。'}


def submission_reply(store, job):
    rows = children(store,job)
    if not rows:
        return '这轮没有实际提交图片或视频生成请求；不能说正在生成或已经发送。'
    states = [media_state(store,row) for row in rows]
    if any(s['status'] in ('queued','running') for s in states):
        return '正在生成，完成后会发到这里。'
    if any(not s['artifact_exists'] for s in states):
        return '没有找到本次生成的有效文件，目前无法交付。'
    if all(s['telegram_message_id'] for s in states):
        return 'Telegram 已接受这次生成的文件，但我无法确认你是否已经看到。'
    return '文件已生成，但还没有确认发送成功。'


def diagnostic_reply(facts):
    rows = facts['recent_media']
    parts = []
    latest_time = rows[0]['created'] if rows else 0
    missing = [r for r in facts['recent_turns'] if r['assistant_made_media_claim'] and not r['submitted_media_ids'] and r['created'] > latest_time]
    if missing:
        parts.append('核对记录后发现，最近的生成提示没有对应的新图片或视频任务；之前的提示不准确。')
    if not rows:
        parts.append('没有找到可交付的图片或视频生成记录。')
    else:
        row = rows[0]
        when = datetime.fromtimestamp(row['created']).astimezone().strftime('%m月%d日 %H:%M')
        parts.append(f"最近一项媒体记录创建于 {when}，" + ('本地文件存在。' if row['artifact_exists'] else '目前没有可用文件。'))
        if row['telegram_message_id']:
            parts.append('Telegram 有该文件的接受回执，但这不代表你已经看到，也不证明它对应后来的修改要求。')
        elif row['delivery_error']:
            parts.append('发送时发生错误，尚未确认交付成功。')
        elif row['status'] in ('queued','running'):
            parts.append('该记录仍在等待或执行，尚未完成。')
        else:
            parts.append('没有可核验的 Telegram 消息回执，不能确认已送达。')
    return ''.join(parts)


def review_answer(store, job, answer):
    from .artifacts import wants_delivery
    import json
    from .task_context import review_handoff
    handoff = review_handoff(store,job,answer)
    if handoff:
        return handoff
    if not answer.strip():
        from .task_context import failure_reply
        return {'accepted': False, 'answer': failure_reply(store, job, '本地模型未返回正文'),
                'feedback': '答复为空。请根据本轮真实执行记录给出结果、尝试过的路径和具体阻碍。不要转去讨论无关历史媒体。',
                'evidence': {}, 'required_tool': None}
    # Media verification must not hijack unrelated research or file-writing requests.
    media_scope = bool(re.search(r'图|照片|视频|photo|image|video|没收到|没发|发送.*文件|发.*文件', job['prompt'], re.I))
    if not media_scope and re.search(r'为什么|怎么|继续|刚才|上次|没完成|没能完成|收到|发了|why|continue', job['prompt'], re.I):
        previous = store.db.execute(
            "SELECT prompt FROM jobs WHERE session_id=? AND created<? ORDER BY created DESC LIMIT 1",
            (job['session_id'], job['created'])).fetchone()
        media_scope = bool(previous and re.search(r'图|照片|视频|photo|image|video', previous['prompt'], re.I))
    rows = children(store,job)
    if rows:
        return {'accepted': True, 'answer': submission_reply(store,job)}
    events = [dict(r) for r in store.db.execute(
        "SELECT name,result FROM pi_tools WHERE job_id=? AND error IS NULL ORDER BY id", (job['id'],))]
    explicit_media_claim = claims_media(answer) and bool(re.search(r'图片|图像|视频|photo|image|video', answer, re.I))
    if not media_scope and (explicit_media_claim or any(marker in answer for marker in ('最近一项媒体记录','核对了媒体记录','运行时校验未通过'))):
        from .task_context import recent_tasks, failure_reply
        return {'accepted':False, 'answer':failure_reply(store,job,'回答偏离本轮任务，未能形成有效结果'),
                'required_tool':None, 'repair_tools':['task_status','web_search','web_read'],
                'feedback':'回答混入了无关的历史媒体。本轮用户请求是：'+job['prompt']+
                           '。结合最近一次相关请求回答；不要查询媒体、解释旧图片或要求用户重说已知任务。',
                'evidence':{'recent_tasks':recent_tasks(store,job)}}
    if not media_scope and not explicit_media_claim and not any(r['name'] in ('list_media','send_media') for r in events):
        if '[此前助手曾声称' not in answer:
            return {'accepted': True, 'answer': answer}
    inventories = [json.loads(r['result']) for r in events if r['name'] == 'list_media']
    sends = [json.loads(r['result']) for r in events if r['name'] == 'send_media']
    facts = snapshot(store,job)
    facts['inventory'] = inventories
    facts['send_results'] = sends
    internal = any(marker in answer for marker in ('[此前助手曾声称', '运行时校验未通过', '本轮能力约束：', '运行时交付证据'))
    sent_claim = bool(re.search(r'(?:已|已经|成功|现在|马上|这就).{0,8}(?:发送|发给|发到)|(?:I.ve|I have).{0,10}sent', answer, re.I)) and not NEGATIVE.search(answer)
    wants = wants_delivery(job['prompt'])
    # A successful send tool contains per-file results. Summarize these, never model prose.
    if sends:
        items = {item['id']:item for batch in sends for item in batch['items']}
        accepted = sum(bool(store.receipt(job['id'], 'artifact:'+key)) for key in items)
        failed = len(items)-accepted
        remaining = {i['id'] for page in inventories for i in page['items']} - items.keys()
        all_requested = bool(re.search(r'所有|全部|全都|\ball\b', job['prompt'], re.I))
        incomplete = wants and all_requested and (remaining or (inventories and inventories[-1]['next_offset'] is not None))
        reply = (f'已发送 {accepted} 个文件。' if accepted else '这次没有成功发送文件。')
        if failed: reply += f'另有 {failed} 个发送失败。'
        if incomplete: reply += '还有文件尚未发送。'
        if not incomplete:
            return {'accepted':True, 'answer':reply}
    else:
        incomplete = wants and (not inventories or any(p['items'] for p in inventories))
        reply = '这次还没有完成文件发送。'
    if not internal and not incomplete and not sent_claim:
        if not claims_media(answer) or inventories:
            return {'accepted':True, 'answer':answer}
    return {'accepted':False, 'answer':reply if wants else diagnostic_reply(facts),
            'required_tool': ('list_media' if not inventories or inventories[-1]['next_offset'] is not None else 'send_media') if wants and incomplete else None,
            'feedback':'请继续完成用户原始请求。要已有文件时先 list_media（查完分页），再 send_media。查询不等于发送。仅报告实际回执和错误；无文件则明确说明。不要为了补救声明而创建用户没要求的新任务。',
            'evidence':facts}
