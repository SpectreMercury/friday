"""Owned artifact inventory and explicit, receipt-backed redelivery."""
import re
from pathlib import Path
from PIL import Image
from .evidence import media_state

SEND_REQUEST = re.compile(r'(?:发|给|传|寄).{0,16}(?:我|这里)|(?:send|resend|deliver|show)\b', re.I)
MEDIA_REQUEST = re.compile(r'图|照|视频|文件|image|photo|picture|video|file', re.I)


def wants_delivery(prompt):
    if re.search(r'(?:发|传).{0,12}(?:了吗|了么|没有|没)|(?:是否|有没有).{0,8}(?:发|传)', prompt):
        return False
    if re.search(r'(?:不要|别|不需要|不用).{0,12}(?:发|传|寄)|(?:do not|don.t|never)\s+(?:send|resend)', prompt, re.I):
        return False
    return bool((SEND_REQUEST.search(prompt) and MEDIA_REQUEST.search(prompt)) or re.search(r'(?:图|图片|照片|视频)呢', prompt))


class Artifacts:
    def __init__(self, store, job, allowed, sender=None):
        self.store, self.job, self.allowed, self.sender = store, job, allowed, sender

    def owned(self, artifact_id):
        row = self.store.get(artifact_id)
        if not row or (row['account_id'], row['session_id']) != (self.job['account_id'], self.job['session_id']) or row['kind'] not in ('image', 'video'):
            raise ValueError('未找到当前对话的媒体文件。')
        return row

    def inventory(self, kind='image', offset=0):
        if kind not in ('image', 'video', 'all') or type(offset) is not int or offset < 0:
            raise ValueError('无效的媒体类型或分页位置。')
        rows = [dict(r) for r in self.store.db.execute(
            "SELECT * FROM jobs WHERE session_id=? AND kind IN ('image','video') AND status='succeeded' AND created<=? ORDER BY created,id",
            (self.job['session_id'], self.job['created']))]
        rows = [r for r in rows if kind == 'all' or r['kind'] == kind]
        items = [media_state(self.store, r) for r in rows[offset:offset+50]]
        return {'items': items, 'total': len(rows), 'next_offset': offset+50 if offset+50 < len(rows) else None}

    async def send(self, ids):
        if not wants_delivery(self.job['prompt']):
            raise ValueError('当前请求没有明确要求发送媒体；请先询问用户是否需要发送。')
        if not isinstance(ids, list) or not 1 <= len(ids) <= 50 or any(not isinstance(x,str) for x in ids):
            raise ValueError('每次提供 1–50 个媒体 ID。')
        rows = [self.owned(x) for x in dict.fromkeys(ids)]  # Validate entire batch before sending.
        if self.job.get('channel')=='app':
            items=[{'id':r['id'],'status':'available' if r['status']=='succeeded' and r.get('result') and Path(r['result']).is_file() else 'unavailable',
                    'url':f"/v1/sessions/{self.job['session_id']}/tasks/{r['id']}/artifact"} for r in rows]
            return {'items':items,'available':sum(i['status']=='available' for i in items),'delivery':'available_in_session'}
        if self.sender is None:
            raise ValueError('Telegram 发送通道不可用。')
        results = []
        for row in rows:
            if not self.allowed(self.job['user_id']) or self.store.get(self.job['id'])['status'] == 'cancelled':
                raise ValueError('授权已撤销或请求已取消。')
            part = 'artifact:' + row['id']
            receipt = self.store.receipt(self.job['id'], part)
            if receipt:
                results.append({'id': row['id'], 'status': 'telegram_accepted', 'message_id': receipt['message_id']})
                continue
            try:
                if row['status'] != 'succeeded' or not row.get('result'):
                    raise ValueError('媒体尚未生成完成。')
                path = Path(row['result'])
                if not path.is_file() or path.stat().st_size == 0:
                    raise ValueError('本地文件不存在。')
                if row['kind'] == 'image':
                    with Image.open(path) as image:
                        image.verify()
                self.store.admit_delivery(self.job['id'],part)
                try:
                    message = await self.sender(self.job['chat_id'], row['kind'], path)
                    self.store.record_receipt(self.job['id'], part, self.job['chat_id'], message.message_id)
                except BaseException as exc:
                    self.store.delivery_failed(self.job['id'],part,exc)
                    raise
                results.append({'id':row['id'], 'status':'telegram_accepted', 'message_id':message.message_id})
            except Exception as exc:
                results.append({'id':row['id'], 'status':'failed', 'error':str(exc) if isinstance(exc,ValueError) else type(exc).__name__})
        return {'items':results, 'accepted':sum(r['status']=='telegram_accepted' for r in results), 'failed':sum(r['status']=='failed' for r in results)}
