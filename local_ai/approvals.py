"""Durable identity/task-bound approvals; optional directory write grants."""
import asyncio
import contextlib
import hashlib
import json
import secrets
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


class ApprovalPending(BaseException):
    """Suspend without settling the tool operation; resume its checkpoint."""
    pass


class Approvals:
    def __init__(self,store,admin):
        self.store,self.admin,self.bot=store,admin,None
        self.pending={}
        self.durable=False
        store.db.executescript('''
          CREATE TABLE IF NOT EXISTS approvals(token TEXT PRIMARY KEY,job_id TEXT,user_id INTEGER,chat_id INTEGER,
            fingerprint TEXT,description TEXT,scope TEXT,status TEXT,expires REAL,message_id INTEGER);
          CREATE TABLE IF NOT EXISTS permission_grants(task_id TEXT,user_id INTEGER,chat_id INTEGER,
            scope TEXT,expires REAL,PRIMARY KEY(task_id,user_id,chat_id,scope));
        ''')
        if 'purpose' not in {row[1] for row in store.db.execute('PRAGMA table_info(approvals)')}:
            store.db.execute("ALTER TABLE approvals ADD COLUMN purpose TEXT NOT NULL DEFAULT 'computer'")
        store.db.commit()

    def granted(self,job,scope):
        # Scope owner is the immutable request ID. Semantic task association
        # must never transfer permissions into another request.
        if not scope:return False
        row=self.store.db.execute('SELECT 1 FROM permission_grants WHERE task_id=? AND user_id=? AND chat_id=? AND scope=? AND expires>?',
            (job['id'],job['user_id'],job['chat_id'],scope,time.time())).fetchone()
        return bool(row)

    async def request(self,job,description,scope=None,*,purpose="computer"):
        if purpose not in ("computer", "video", "schedule") or (purpose in ("video", "schedule") and scope):
            raise ValueError("无效确认类型。")
        if job.get('channel')=='app':
            return self.mobile_request(job,description,scope,purpose=purpose)
        if self.bot is None or (purpose not in ('video','schedule') and job['user_id']!=self.admin):return False
        if self.granted(job,scope):return True
        fp=hashlib.sha256((('video\n' if purpose=='video' else '')+description+'\n'+(scope or '')).encode()).hexdigest()
        old=self.store.db.execute('SELECT * FROM approvals WHERE job_id=? AND fingerprint=? ORDER BY expires DESC LIMIT 1',(job['id'],fp)).fetchone()
        if old and (old['status'] in ('denied','consumed','expired') or old['expires']<=time.time()):return False
        token=old['token'] if old else secrets.token_hex(12)
        if not old:
            with self.store.db:self.store.db.execute('INSERT INTO approvals(token,job_id,user_id,chat_id,fingerprint,description,scope,status,expires,message_id,purpose) VALUES (?,?,?,?,?,?,?,\'pending\',?,NULL,?)',
                (token,job['id'],job['user_id'],job['chat_id'],fp,description,scope,time.time()+86400,purpose))
        # No side effect has started yet. Restart can resume this approval wait.
        self.store.db.execute("UPDATE tool_operations SET status='waiting_approval' WHERE job_id=? AND status='started'",(job['id'],))
        self.store.db.commit()
        future=asyncio.get_running_loop().create_future()
        self.pending[token]=(job,future)
        message=None
        try:
            if not old or not old['message_id']:
                buttons=[InlineKeyboardButton('允许这一次',callback_data=f'approve:{token}:yes'),InlineKeyboardButton('拒绝',callback_data=f'approve:{token}:no')]
                if scope:buttons.append(InlineKeyboardButton('允许本次请求在此目录写入',callback_data=f'approve:{token}:scope'))
                display=description
                if purpose=='video':
                    for offset in range(0,len(description),3000):
                        await self.bot.send_message(job['chat_id'],description[offset:offset+3000])
                    display='以上为本次视频方案。确认后才开始渲染。'
                message=await self.bot.send_message(job['chat_id'],('需要确认视频方案：\n\n' if purpose=='video' else '需要确认电脑操作：\n\n')+display+
                    (f'\n\n可选范围授权：{scope}；仅本次请求（含重启续接），24小时内有效。' if scope else ''),reply_markup=InlineKeyboardMarkup([buttons]))
                mid=getattr(message,'message_id',None)
                if isinstance(mid,int):
                    self.store.db.execute('UPDATE approvals SET message_id=? WHERE token=?',(mid,token));self.store.db.commit()
            # Poll persisted decisions too: a callback may have arrived after restart
            # and before this operation resumes waiting.
            if self.durable:
                decision=self.store.db.execute('SELECT status FROM approvals WHERE token=?',(token,)).fetchone()
                if decision[0]=='pending':
                    raise ApprovalPending()
            deadline=time.monotonic()+180
            while True:
                row=self.store.db.execute('SELECT * FROM approvals WHERE token=?',(token,)).fetchone()
                if row['status'] in ('approved','denied'):
                    allowed=row['status']=='approved'
                    with self.store.db:
                        if allowed:self.store.db.execute("UPDATE approvals SET status='consumed' WHERE token=?",(token,))
                        self.store.db.execute("UPDATE tool_operations SET status='started' WHERE job_id=? AND status='waiting_approval'",(job['id'],))
                    return allowed
                queued=self.store.db.execute("SELECT 1 FROM jobs WHERE user_id=? AND chat_id=? AND status='queued' AND created>? LIMIT 1",(job['user_id'],job['chat_id'],job['created'])).fetchone()
                if time.monotonic()>deadline or queued:
                    # The request remains persisted; the user may approve and retry later.
                    with self.store.db:self.store.db.execute("UPDATE tool_operations SET status='started' WHERE job_id=? AND status='waiting_approval'",(job['id'],))
                    return False
                try:await asyncio.wait_for(asyncio.shield(future),min(1,max(.01,deadline-time.monotonic())))
                except asyncio.TimeoutError:pass
        finally:
            self.pending.pop(token,None)
            # Preserve buttons on service interruption; cancellation invalidates them.
            current=self.store.get(job['id'])
            if message and (not current or current['status']=='cancelled' or future.done()):
                with contextlib.suppress(Exception):await message.edit_reply_markup(reply_markup=None)

    async def callback(self,update,context):
        query=update.callback_query
        _,token,decision=query.data.split(':')
        row=self.store.db.execute('SELECT * FROM approvals WHERE token=?',(token,)).fetchone()
        if not row or row['status']!='pending' or row['expires']<=time.time():
            await query.answer('此操作已结束或已过期。');return
        if update.effective_user.id!=(row['user_id'] if row['purpose']=='video' else self.admin) or update.effective_chat.id!=row['chat_id'] or update.effective_chat.type!='private':
            await query.answer('只有请求者本人可以确认。' if row['purpose']=='video' else '只有管理员本人可以确认。');return
        job=self.store.get(row['job_id'])
        if not job or job['status']=='cancelled':
            await query.answer('此操作已取消。');return
        if decision=='scope' and not row['scope']:
            await query.answer('此操作没有范围授权。');return
        with self.store.db:
            self.store.db.execute('UPDATE approvals SET status=? WHERE token=?',('denied' if decision=='no' else 'approved',token))
            if job['status']=='waiting_approval':
                self.store.db.execute("UPDATE jobs SET status='queued',outcome='pending' WHERE id=?",(job['id'],))
            if decision=='scope':self.store.db.execute('INSERT OR REPLACE INTO permission_grants VALUES (?,?,?,?,?)',
                (job['id'],self.admin,row['chat_id'],row['scope'],row['expires']))
        item=self.pending.get(token)
        if item and not item[1].done():item[1].set_result(decision!='no')
        await query.answer('已拒绝。' if decision=='no' else '已记录授权。')

    def mobile_request(self,job,description,scope=None,*,purpose='computer'):
        account=self.store.account(job['account_id'])
        session=self.store.require_session(account['id'],job['session_id'])
        if purpose not in ('video','schedule') and (not account['is_admin'] or not session['management']):
            return False
        current=self.store.get(job['id'])
        if current['status']=='cancelled':
            return False
        if scope and self.store.db.execute('SELECT 1 FROM permission_grants WHERE task_id=? AND user_id=? AND scope=? AND expires>?',
            (job['id'],job['user_id'],scope,time.time())).fetchone():
            return True
        fp=hashlib.sha256((('video\n' if purpose=='video' else '')+description+'\n'+(scope or '')).encode()).hexdigest()
        old=self.store.db.execute('SELECT * FROM approvals WHERE job_id=? AND fingerprint=? ORDER BY expires DESC LIMIT 1',(job['id'],fp)).fetchone()
        if old:
            if old['expires']<=time.time() or old['status'] in ('denied','expired','consumed'):
                return False
            if old['status']=='approved':
                with self.store.db:
                    changed=self.store.db.execute("UPDATE approvals SET status='consumed' WHERE token=? AND status='approved'",(old['token'],))
                return changed.rowcount==1
        else:
            token=secrets.token_hex(12)
            with self.store.db:
                self.store.db.execute("INSERT INTO approvals(token,job_id,user_id,chat_id,fingerprint,description,scope,status,expires,message_id,purpose) VALUES (?,?,?,?,?,?,?,'pending',?,NULL,?)",
                    (token,job['id'],job['user_id'],job['chat_id'],fp,description,scope,time.time()+86400,purpose))
                self.store.session_event(account['id'],job['session_id'],'approval_requested',{'job_id':job['id'],'token':token})
        with self.store.db:
            self.store.db.execute("UPDATE tool_operations SET status='waiting_approval' WHERE job_id=? AND status='started'",(job['id'],))
        raise ApprovalPending()

    def list_for_session(self,account_id,session_id):
        self.store.require_session(account_id,session_id)
        return [dict(r) for r in self.store.db.execute("SELECT a.* FROM approvals a JOIN jobs j ON j.id=a.job_id WHERE j.account_id=? AND j.session_id=? AND a.status='pending' AND a.expires>? AND j.status!='cancelled' ORDER BY a.expires",(account_id,session_id,time.time()))]

    def decide(self,account_id,session_id,token,decision):
        session=self.store.require_session(account_id,session_id)
        if decision not in ('approved','denied','scope'):
            raise ValueError('无效批准决定。')
        row=self.store.db.execute('SELECT a.* FROM approvals a JOIN jobs j ON j.id=a.job_id WHERE a.token=? AND j.account_id=? AND j.session_id=?',(token,account_id,session_id)).fetchone()
        if not row:
            raise PermissionError('此确认不属于当前会话。')
        if row['expires']<=time.time() or row['status']!='pending':
            raise ValueError('批准已处理或过期。')
        if row['purpose']!='video' and (not self.store.account(account_id)['is_admin'] or not session['management']):
            raise PermissionError('此工作空间没有管理员操作权限。')
        job=self.store.get(row['job_id'])
        if job['status']=='cancelled' or (decision=='scope' and not row['scope']):
            raise ValueError('任务已取消或授权范围无效。')
        with self.store.db:
            changed=self.store.db.execute("UPDATE approvals SET status=? WHERE token=? AND status='pending'",('denied' if decision=='denied' else 'approved',token))
            if changed.rowcount!=1:
                raise ValueError('批准已处理。')
            if decision=='scope':
                self.store.db.execute('INSERT OR REPLACE INTO permission_grants VALUES (?,?,?,?,?)',(job['id'],job['user_id'],job['chat_id'],row['scope'],row['expires']))
            if job['status']=='waiting_approval':
                self.store.db.execute("UPDATE jobs SET status='queued',outcome='pending' WHERE id=?",(job['id'],))
            self.store.session_event(account_id,session_id,'approval_decided',{'job_id':job['id'],'token':token})
        item=self.pending.get(token)
        if item and not item[1].done():
            item[1].set_result(decision!='denied')
        return {'status':'denied' if decision=='denied' else 'approved'}

    def expire(self):
        for row in self.store.db.execute("SELECT DISTINCT a.job_id FROM approvals a JOIN jobs j ON j.id=a.job_id WHERE a.status='pending' AND a.expires<=? AND j.status='waiting_approval'",(time.time(),)).fetchall():
            with self.store.db:
                self.store.db.execute("UPDATE approvals SET status='expired' WHERE job_id=? AND status='pending' AND expires<=?",(row['job_id'],time.time()))
            self.store.update(row['job_id'],status='queued')
