"""Account-owned workspaces and immutable per-task skill snapshots.

Legacy Telegram identifiers remain channel metadata, never mobile identities.
All public accessors require an account and session, including file access.
"""
import hashlib
from contextlib import nullcontext
import json
import os
import re
import shutil
import stat
import time
import uuid
from pathlib import Path


class RevisionConflict(ValueError):
    pass


class Sessions:
    def init_sessions(self):
        self._migrating_sessions=True
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY,user_id INTEGER UNIQUE NOT NULL,
            display_name TEXT NOT NULL,is_admin INTEGER NOT NULL DEFAULT 0,enabled INTEGER NOT NULL DEFAULT 1);
          CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,account_id TEXT NOT NULL REFERENCES accounts(id),
            name TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,skill_names TEXT NOT NULL DEFAULT '[]',
            management INTEGER NOT NULL DEFAULT 0,archived INTEGER NOT NULL DEFAULT 0,created REAL NOT NULL,
            workspace_key TEXT NOT NULL UNIQUE);
          CREATE TABLE IF NOT EXISTS channel_sessions(channel TEXT,user_id INTEGER,chat_id INTEGER,
            session_id TEXT NOT NULL REFERENCES sessions(id),PRIMARY KEY(channel,user_id,chat_id));
          CREATE TABLE IF NOT EXISTS session_resets(session_id TEXT PRIMARY KEY,reset_at REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS task_skills(task_id TEXT PRIMARY KEY,snapshot TEXT NOT NULL,created REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS session_events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id TEXT NOT NULL,session_id TEXT,kind TEXT NOT NULL,payload TEXT NOT NULL,created REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS mobile_requests(account_id TEXT,request_id TEXT,session_id TEXT NOT NULL,
            fingerprint TEXT NOT NULL,job_id TEXT NOT NULL,PRIMARY KEY(account_id,request_id));
          CREATE TABLE IF NOT EXISTS session_imports(id INTEGER PRIMARY KEY,session_id TEXT NOT NULL,
            source_session_id TEXT NOT NULL,content TEXT NOT NULL,created REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS schema_versions(name TEXT PRIMARY KEY,version INTEGER NOT NULL);
        ''')
        if 'deleted' not in {r[1] for r in self.db.execute('PRAGMA table_info(sessions)')}:
            self.db.execute('ALTER TABLE sessions ADD COLUMN deleted REAL')
        # A deleted turn stays out of every listing and every later context.
        self.db.execute('CREATE TABLE IF NOT EXISTS deleted_jobs(job_id TEXT PRIMARY KEY,account_id TEXT NOT NULL,created REAL NOT NULL)')
        for table in ('jobs', 'tasks'):
            columns = {r[1] for r in self.db.execute(f'PRAGMA table_info({table})')}
            for name in ('account_id', 'session_id'):
                if name not in columns:
                    self.db.execute(f'ALTER TABLE {table} ADD COLUMN {name} TEXT')
        columns = {r[1] for r in self.db.execute('PRAGMA table_info(jobs)')}
        if 'channel' not in columns:
            self.db.execute("ALTER TABLE jobs ADD COLUMN channel TEXT NOT NULL DEFAULT 'telegram'")
        attachment_columns={r[1]:r for r in self.db.execute('PRAGMA table_info(attachments)')}
        if attachment_columns['chat_id'][3]:
            # Legacy NOT NULL chat_id cannot represent a non-Telegram upload.
            self.db.execute('ALTER TABLE attachments RENAME TO attachments_legacy')
            self.db.execute('''CREATE TABLE attachments(id TEXT PRIMARY KEY,job_id TEXT NOT NULL,user_id INTEGER NOT NULL,chat_id INTEGER,
                name TEXT NOT NULL,kind TEXT NOT NULL,path TEXT NOT NULL,size INTEGER NOT NULL,digest TEXT NOT NULL,
                created REAL NOT NULL,extraction TEXT)''')
            self.db.execute('INSERT INTO attachments SELECT id,job_id,user_id,chat_id,name,kind,path,size,digest,created,extraction FROM attachments_legacy')
            self.db.execute('DROP TABLE attachments_legacy')
        if 'session_id' not in attachment_columns:
            self.db.execute('ALTER TABLE attachments ADD COLUMN session_id TEXT')
        for row in self.db.execute('SELECT DISTINCT user_id,chat_id FROM jobs WHERE session_id IS NULL ORDER BY user_id,CASE WHEN chat_id=user_id THEN 0 ELSE 1 END,chat_id').fetchall():
            session = self.telegram_session(row['user_id'], row['chat_id'])
            for table in ('jobs', 'tasks'):
                self.db.execute(f'UPDATE {table} SET account_id=?,session_id=? WHERE user_id=? AND chat_id=? AND session_id IS NULL',
                                (session['account_id'],session['id'],row['user_id'],row['chat_id']))
        for row in self.db.execute('SELECT user_id,reset_at FROM context_resets').fetchall():
            self.db.execute('INSERT OR IGNORE INTO session_resets SELECT id,? FROM sessions WHERE account_id=(SELECT id FROM accounts WHERE user_id=?)', (row['reset_at'],row['user_id']))
        self.db.execute('UPDATE attachments SET session_id=(SELECT session_id FROM jobs WHERE id=attachments.job_id) WHERE session_id IS NULL')
        self.db.execute("INSERT OR REPLACE INTO schema_versions VALUES ('sessions',1)")
        self.db.execute('CREATE INDEX IF NOT EXISTS jobs_session_order ON jobs(session_id,created)')
        self.db.execute('UPDATE accounts SET enabled=(SELECT allowed FROM access WHERE user_id=accounts.user_id) WHERE EXISTS(SELECT 1 FROM access WHERE user_id=accounts.user_id)')
        self.db.commit()
        self._migrating_sessions=False

    def account_for_uid(self, uid):
        row = self.db.execute('SELECT * FROM accounts WHERE user_id=?',(uid,)).fetchone()
        if row:
            return dict(row)
        aid = uuid.uuid4().hex
        access=self.db.execute('SELECT allowed FROM access WHERE user_id=?',(uid,)).fetchone()
        enabled=1 if getattr(self,'_migrating_sessions',False) or access is None else int(bool(access[0]))
        self.db.execute('INSERT INTO accounts(id,user_id,display_name,enabled) VALUES (?,?,?,?)',(aid,uid,str(uid),enabled))
        self.db.commit()
        return self.account(aid)

    def account(self, account_id):
        row = self.db.execute('SELECT * FROM accounts WHERE id=?',(account_id,)).fetchone()
        if not row:
            raise PermissionError('账号不存在。')
        return dict(row)

    def create_account(self, display_name, *, is_admin=False):
        if not isinstance(display_name,str) or not 1 <= len(display_name.strip()) <= 100:
            raise ValueError('账号名称须为1–100字符。')
        # A private execution key, never used as a Telegram identity/chat ID.
        uid = min(0,self.db.execute('SELECT COALESCE(min(user_id),0) FROM accounts').fetchone()[0])-1
        aid = uuid.uuid4().hex
        with self.db:
            self.db.execute('INSERT INTO accounts VALUES (?,?,?,?,1)',(aid,uid,display_name.strip(),int(is_admin)))
            self.db.execute('INSERT OR REPLACE INTO access VALUES (?,1)',(uid,))
        return self.account(aid)

    def skill_catalog(self):
        from .providers import Skills
        library = Skills(self.skills_root)
        result=[]
        for item in library.list():
            data=library.read(item['name'])
            result.append({**item,'version':hashlib.sha256(data['text'].encode()).hexdigest()})
        return result

    @staticmethod
    def _session(row):
        item=dict(row)
        item['skill_names']=json.loads(item['skill_names'])
        item['management']=bool(item['management'])
        item['archived']=bool(item['archived'])
        return item

    def require_session(self, account_id, session_id, *, deleted=False):
        account=self.account(account_id)
        if not account['enabled']:
            raise PermissionError('账号已停用。')
        row=self.db.execute('SELECT * FROM sessions WHERE id=? AND account_id=?',(session_id,account_id)).fetchone()
        if not row or (row['deleted'] is not None)!=deleted:
            raise PermissionError('工作空间不存在或无权访问。')
        return self._session(row)

    def list_sessions(self, account_id):
        if not self.account(account_id)['enabled']:
            raise PermissionError('账号已停用。')
        return [self._session(r) for r in self.db.execute('SELECT * FROM sessions WHERE account_id=? AND deleted IS NULL ORDER BY created DESC',(account_id,))]

    def create_session(self, account_id, name, *, skills=(), workspace_key=None, auto_title=False):
        if not self.account(account_id)['enabled']:
            raise PermissionError('账号已停用。')
        if auto_title:
            from .agent.roster import DEFAULT_TITLE
            name=DEFAULT_TITLE
        if not isinstance(name,str) or not 1 <= len(name.strip()) <= 100:
            raise ValueError('工作空间名称须为1–100字符。')
        self.validate_skills(skills)
        sid=uuid.uuid4().hex
        with self.db:
            self.db.execute('INSERT INTO sessions(id,account_id,name,skill_names,created,workspace_key) VALUES (?,?,?,?,?,?)',
                            (sid,account_id,name.strip(),json.dumps(list(skills)),time.time(),workspace_key or sid))
            if auto_title:
                # Named by the server from the first exchange; see agent.roster.
                self.db.execute('UPDATE sessions SET auto_title=1 WHERE id=?',(sid,))
            self.session_event(account_id,sid,'session_created',{'id':sid})
        return self.require_session(account_id,sid)

    def telegram_session(self, uid, chat_id):
        row=self.db.execute("SELECT c.session_id FROM channel_sessions c JOIN sessions s ON s.id=c.session_id WHERE c.channel='telegram' AND c.user_id=? AND c.chat_id=? AND s.deleted IS NULL",(uid,chat_id)).fetchone()
        account=self.account_for_uid(uid)
        if row:
            return self.require_session(account['id'],row[0])
        # Only the first historical chat inherits the legacy per-user directory.
        legacy=str(uid)
        if self.db.execute('SELECT 1 FROM sessions WHERE workspace_key=?',(legacy,)).fetchone():
            legacy=None
        # Historical migration retains its original skill selection. Newly
        # created workspaces require explicit opt-in, including Telegram.
        skills=[s['name'] for s in self.skill_catalog()] if getattr(self,'_migrating_sessions',False) else []
        session=self.create_session(account['id'],'Telegram',skills=skills,workspace_key=legacy)
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO channel_sessions VALUES (?,?,?,?)',('telegram',uid,chat_id,session['id']))
        return session

    def validate_skills(self, names):
        if not isinstance(names,(list,tuple)) or len(names)>100 or any(not isinstance(n,str) for n in names):
            raise ValueError('无效的技能清单。')
        if len(set(names))!=len(names) or (names and set(names)-{s['name'] for s in self.skill_catalog()}):
            raise ValueError('技能不存在或重复。')

    def update_session(self, account_id, session_id, revision, **changes):
        session=self.require_session(account_id,session_id)
        if type(revision) is not int or revision!=session['revision']:
            raise RevisionConflict('工作空间已更新，请刷新后重试。')
        if not changes or set(changes)-{'name','skill_names','management','archived'}:
            raise ValueError('不支持的工作空间设置。')
        if 'name' in changes:
            if not isinstance(changes['name'],str) or not 1<=len(changes['name'].strip())<=100:
                raise ValueError('工作空间名称须为1–100字符。')
            changes['name']=changes['name'].strip()
            changes['auto_title']=0  # the user's own name is never replaced
        for key in ('management','archived'):
            if key in changes and type(changes[key]) is not bool:
                raise ValueError('开关必须为布尔值。')
        if changes.get('management') and not self.account(account_id)['is_admin']:
            raise PermissionError('只有所有者可以启用设备管理。')
        if 'skill_names' in changes:
            self.validate_skills(changes['skill_names'])
            changes['skill_names']=json.dumps(changes['skill_names'])
        with self.db:
            result=self.db.execute('UPDATE sessions SET '+','.join(k+'=?' for k in changes)+',revision=revision+1 WHERE id=? AND revision=?',(*changes.values(),session_id,revision))
            if result.rowcount!=1:
                raise RevisionConflict('工作空间已更新，请刷新后重试。')
            self.session_event(account_id,session_id,'session_updated',{'revision':revision+1})
        return self.require_session(account_id,session_id)

    def capture_skills(self, job_id):
        job=self.get(job_id)
        tid=job['task_id']
        row=self.db.execute('SELECT snapshot FROM task_skills WHERE task_id=?',(tid,)).fetchone()
        if row:
            return json.loads(row[0])
        from .providers import Skills
        library=Skills(self.skills_root)
        session=self.require_session(job['account_id'],job['session_id'])
        snapshot={}
        for name in session['skill_names']:
            try:
                content=library.read(name)['text']
            except FileNotFoundError:
                continue
            snapshot[name]={'name':name,'text':content,'version':hashlib.sha256(content.encode()).hexdigest(),
                            'description':library.description(content)}
        # Enqueue owns the outer transaction. A nested sqlite connection context
        # would commit its job before the media snapshot is captured.
        with nullcontext() if self.db.in_transaction else self.db:
            self.db.execute('INSERT OR IGNORE INTO task_skills VALUES (?,?,?)',(tid,json.dumps(snapshot,ensure_ascii=False),time.time()))
        return snapshot

    def session_event(self, account_id, session_id, kind, payload):
        self.db.execute('INSERT INTO session_events(account_id,session_id,kind,payload,created) VALUES (?,?,?,?,?)',
                        (account_id,session_id,kind,json.dumps(payload,ensure_ascii=False),time.time()))

    def session_events(self, account_id, after=0):
        if type(after) is not int or after<0:
            raise ValueError('无效同步游标。')
        items=[]
        for row in self.db.execute('SELECT * FROM session_events WHERE account_id=? AND sequence>? ORDER BY sequence LIMIT 200',(account_id,after)):
            item=dict(row);item['payload']=json.loads(item['payload']);items.append(item)
        return {'items':items,'cursor':items[-1]['sequence'] if items else after}

    def get_session_job(self, account_id, session_id, job_id):
        self.require_session(account_id,session_id)
        job=self.get(job_id)
        if not job or job['account_id']!=account_id or job['session_id']!=session_id:
            raise PermissionError('任务不属于当前工作空间。')
        return job

    def workspace_directory(self, account_id, session_id, base=None):
        session=self.require_session(account_id,session_id)
        base=Path(base or self.path.parent/'workspaces')
        if base.is_symlink():
            raise ValueError('工作区不能是符号链接。')
        root=base/session['workspace_key']
        if root.is_symlink():
            raise ValueError('工作区不能是符号链接。')
        root.mkdir(parents=True,exist_ok=True,mode=0o700)
        return root

    def workspace_file(self, account_id, session_id, name, base=None):
        if not isinstance(name,str) or not re.fullmatch(r'[\w][\w .-]{0,159}',name) or name in ('.','..'):
            raise ValueError('无效文件名。')
        path=self.workspace_directory(account_id,session_id,base)/name
        if path.is_symlink():
            raise ValueError('不能访问符号链接。')
        if path.exists():
            info=path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
                raise ValueError('仅支持独立普通文件。')
        return path

    def fork_session(self, account_id, session_id, name, message_ids=(), file_names=()):
        source=self.require_session(account_id,session_id)
        if not isinstance(message_ids,list) or not isinstance(file_names,list) or len(message_ids)>100 or len(file_names)>100:
            raise ValueError('最多复制100条消息或文件。')
        messages=[self.get_session_job(account_id,session_id,jid) for jid in dict.fromkeys(message_ids)]
        paths=[self.workspace_file(account_id,session_id,n) for n in dict.fromkeys(file_names)]
        if any(not p.is_file() or p.stat().st_size>20*1024*1024 for p in paths):
            raise ValueError('文件缺失或超过20MB。')
        target=self.create_session(account_id,name,skills=source['skill_names'])
        try:
            for path in paths:
                destination=self.workspace_file(account_id,target['id'],path.name)
                with path.open('rb') as src, destination.open('xb') as dst:
                    shutil.copyfileobj(src,dst)
                destination.chmod(0o600)
            with self.db:
                for job in messages:
                    # Copy user-visible material, not tool transcripts or authority.
                    self.db.execute('INSERT INTO session_imports(session_id,source_session_id,content,created) VALUES (?,?,?,?)',
                        (target['id'],session_id,json.dumps({'prompt':job['prompt'],'result':job['result'] if job['kind']=='chat' else None,'source_job_id':job['id']},ensure_ascii=False),time.time()))
        except BaseException:
            with self.db:
                self.db.execute('DELETE FROM sessions WHERE id=?',(target['id'],))
            directory=self.path.parent/'workspaces'/target['workspace_key']
            if directory.exists():
                shutil.rmtree(directory)
            raise
        return target

    # -- deletion ---------------------------------------------------------
    # Deleted sessions wait TRASH_DAYS in 「最近删除」 and can be restored;
    # purging then removes their rows and workspace for good.
    TRASH_DAYS=30

    def delete_session(self, account_id, session_id):
        self.require_session(account_id,session_id)
        with self.db:
            self.db.execute('UPDATE sessions SET deleted=?,revision=revision+1 WHERE id=? AND account_id=?',(time.time(),session_id,account_id))
            self.session_event(account_id,session_id,'session_deleted',{'id':session_id})

    def restore_session(self, account_id, session_id):
        self.require_session(account_id,session_id,deleted=True)
        with self.db:
            self.db.execute('UPDATE sessions SET deleted=NULL,revision=revision+1 WHERE id=? AND account_id=?',(session_id,account_id))
            self.session_event(account_id,session_id,'session_restored',{'id':session_id})
        return self.require_session(account_id,session_id)

    def deleted_sessions(self, account_id):
        self.purge_expired(account_id)
        rows=self.db.execute('''SELECT s.*,p.id AS drama_id,p.title AS drama_title FROM sessions s
            LEFT JOIN drama_projects p ON p.session_id=s.id
            WHERE s.account_id=? AND s.deleted IS NOT NULL ORDER BY s.deleted DESC''',(account_id,)).fetchall() if self._has_table('drama_projects') else \
            self.db.execute('SELECT *,NULL AS drama_id,NULL AS drama_title FROM sessions WHERE account_id=? AND deleted IS NOT NULL ORDER BY deleted DESC',(account_id,)).fetchall()
        return [{'id':r['id'],'name':r['drama_title'] or r['name'],'kind':'drama' if r['drama_id'] else 'session',
                 'deleted':r['deleted'],'expires':r['deleted']+self.TRASH_DAYS*86400} for r in rows]

    def purge_expired(self, account_id=None):
        cutoff=time.time()-self.TRASH_DAYS*86400
        query='SELECT id,account_id FROM sessions WHERE deleted IS NOT NULL AND deleted<?'+(' AND account_id=?' if account_id else '')
        for row in self.db.execute(query,(cutoff,account_id) if account_id else (cutoff,)).fetchall():
            self.purge_session(row['account_id'],row['id'])

    def _has_table(self, name):
        return bool(self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone())

    def _columns(self):
        tables=[r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND sql NOT LIKE 'CREATE VIRTUAL%' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%_fts%'")]
        return {t:{r[1] for r in self.db.execute(f'PRAGMA table_info("{t}")')} for t in tables}

    def purge_session(self, account_id, session_id):
        session=self.require_session(account_id,session_id,deleted=True)
        jobs=[r[0] for r in self.db.execute('SELECT id FROM jobs WHERE session_id=? AND account_id=?',(session_id,account_id))]
        projects=[r[0] for r in self.db.execute('SELECT id FROM drama_projects WHERE session_id=?',(session_id,))] if self._has_table('drama_projects') else []
        files=[r[0] for r in self.db.execute("SELECT result FROM jobs WHERE session_id=? AND kind IN ('image','video') AND result IS NOT NULL",(session_id,))]
        with self.db:
            for table,columns in self._columns().items():
                if table in ('sessions','accounts'):
                    continue
                for column,ids in (('job_id',jobs),('project_id',projects)):
                    if column in columns and ids:
                        for start in range(0,len(ids),500):
                            chunk=ids[start:start+500]
                            self.db.execute(f'DELETE FROM "{table}" WHERE {column} IN ({",".join("?"*len(chunk))})',chunk)
                if 'session_id' in columns:
                    self.db.execute(f'DELETE FROM "{table}" WHERE session_id=?',(session_id,))
            self.db.execute('DELETE FROM sessions WHERE id=? AND account_id=?',(session_id,account_id))
        for value in files:
            self._remove_artifact(value)
        directory=self.path.parent/'workspaces'/session['workspace_key']
        if directory.is_dir() and not directory.is_symlink() and not self.db.execute('SELECT 1 FROM sessions WHERE workspace_key=?',(session['workspace_key'],)).fetchone():
            shutil.rmtree(directory,ignore_errors=True)

    def _remove_artifact(self, value):
        # Generated media lives under the data directory; never follow a path outside it.
        try:
            path=Path(value).resolve()
            root=self.path.parent.resolve()
        except (OSError,TypeError,ValueError):
            return
        if root in path.parents and path.is_file() and not self.db.execute('SELECT 1 FROM jobs WHERE result=?',(value,)).fetchone():
            path.unlink(missing_ok=True)

    def delete_job(self, account_id, session_id, job_id):
        """Removes one turn (and the work it spawned) from the conversation for good."""
        job=self.get_session_job(account_id,session_id,job_id)
        ids=[job['id']];index=0
        while index<len(ids):
            ids+=[r[0] for r in self.db.execute('SELECT id FROM jobs WHERE parent_job_id=? AND session_id=?',(ids[index],session_id)) if r[0] not in ids]
            index+=1
        media=[r[0] for r in self.db.execute(f"SELECT result FROM jobs WHERE id IN ({','.join('?'*len(ids))}) AND kind IN ('image','video') AND result IS NOT NULL",ids)]
        with self.db:
            self.db.executemany('INSERT OR IGNORE INTO deleted_jobs VALUES (?,?,?)',[(i,account_id,time.time()) for i in ids])
            if self._has_table('agent_messages'):
                self.db.execute(f"DELETE FROM agent_messages WHERE job_id IN ({','.join('?'*len(ids))})",ids)
            if self._has_table('agent_summaries'):
                # A summary may quote the deleted turn; the next turn rebuilds it.
                self.db.execute('DELETE FROM agent_summaries WHERE session_id=?',(session_id,))
            self.db.execute(f"UPDATE jobs SET result=NULL WHERE id IN ({','.join('?'*len(ids))}) AND kind IN ('image','video')",ids)
            self.session_event(account_id,session_id,'job_deleted',{'ids':ids})
        for value in media:
            self._remove_artifact(value)
        return ids

    def delete_workspace_file(self, account_id, session_id, name):
        path=self.workspace_file(account_id,session_id,name)
        if not path.is_file():
            raise FileNotFoundError()
        path.unlink()
        with self.db:
            self.session_event(account_id,session_id,'file_deleted',{'name':name})

    def imported_context(self, session_id):
        return [json.loads(r[0]) for r in self.db.execute('SELECT content FROM session_imports WHERE session_id=? ORDER BY id',(session_id,))]


class SessionSkills:
    def __init__(self, store, job_id):
        self.store,self.job_id=store,job_id

    def list(self):
        return [{k:v[k] for k in ('name','description','version')} for v in self.store.capture_skills(self.job_id).values()]

    def read(self, name):
        item=self.store.capture_skills(self.job_id).get(name)
        if not item:
            raise PermissionError('此任务未启用该技能。')
        return {**item,'authority':'guidance_only','permissions':'unchanged'}
