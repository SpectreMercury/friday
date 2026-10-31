import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from .runtime_store import RuntimeStore
from .tasks import Tasks
from .outbox import Outbox
from .sessions import Sessions


class Store(RuntimeStore, Tasks, Outbox, Sessions):
    def __init__(self, path: Path, *, skills_root=None):
        self.path = Path(path)
        self.skills_root = Path(skills_root or os.getenv('FRIDAY_SKILLS_DIR') or Path(__file__).resolve().parents[1]/'assistant-skills').expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        existing = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone()
        migrated = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sessions'").fetchone()
        if existing and not migrated:
            backup = path.with_name(path.name+'.pre-sessions-'+str(time.time_ns())+'.bak')
            with sqlite3.connect(backup) as destination:
                self.db.backup(destination)
            backup.chmod(0o600)
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, user_id INTEGER, chat_id INTEGER, kind TEXT,
                prompt TEXT, image TEXT, hd INTEGER, status TEXT, created REAL,
                result TEXT, error TEXT, delivered INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY, user_id INTEGER, role TEXT, content TEXT);
            CREATE TABLE IF NOT EXISTS context_resets (user_id INTEGER PRIMARY KEY, reset_at REAL);
            CREATE TABLE IF NOT EXISTS access (user_id INTEGER PRIMARY KEY, allowed INTEGER);
            CREATE TABLE IF NOT EXISTS seen (update_id INTEGER PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS delivery_receipts (
                job_id TEXT, part TEXT, chat_id INTEGER, message_id INTEGER, sent_at REAL,
                PRIMARY KEY(job_id,part)
            );
            CREATE TABLE IF NOT EXISTS pi_tools (
                id INTEGER PRIMARY KEY, job_id TEXT, user_id INTEGER, name TEXT,
                args TEXT, result TEXT, error TEXT, created REAL
            );
        ''')
        columns = {row[1] for row in self.db.execute('PRAGMA table_info(jobs)')}
        for name, sql_type in [('duration_seconds','INTEGER'), ('outcome','TEXT'), ('audio', 'TEXT'), ('voice_result', 'TEXT'), ('text_delivered', 'INTEGER DEFAULT 0'), ('parent_job_id', 'TEXT'), ('delivery_attempts', 'INTEGER DEFAULT 0'), ('delivery_error', 'TEXT'), ('retry_after', 'REAL DEFAULT 0')]:
            if name not in columns:
                self.db.execute(f'ALTER TABLE jobs ADD COLUMN {name} {sql_type}')
        # Recover parent links only from actual successful tool submissions.
        self.db.execute("""UPDATE jobs SET parent_job_id=(
            SELECT t.job_id FROM pi_tools t JOIN jobs p ON p.id=t.job_id
            WHERE t.name IN ('generate_image','generate_video') AND t.error IS NULL
            AND json_valid(t.result) AND json_extract(t.result,'$.job_id')=jobs.id
            AND p.user_id=jobs.user_id AND p.chat_id=jobs.chat_id LIMIT 1)
            WHERE kind IN ('image','video') AND parent_job_id IS NULL""")
        self.db.commit()
        self.init_runtime_schema()
        self.init_tasks()
        self.init_outbox()
        self.init_sessions()
        from .agent.roster import init_schema as init_roster
        init_roster(self.db)
        from .media_prompts import init_schema
        init_schema(self.db)
        from .video_projects import VideoProjects
        self.video_projects = VideoProjects(self)
        from .drama.service import Dramas
        self.dramas = Dramas(self)
        from .evolution import Evolution
        self.evolution = Evolution(self)

    def allowed(self, uid, configured, admin):
        if uid == admin:
            return True
        account = self.account_for_uid(uid)
        if not account['enabled']:
            return False
        row = self.db.execute("SELECT allowed FROM access WHERE user_id=?", (uid,)).fetchone()
        return bool(row[0]) if row else uid in configured

    def set_access(self, uid, allowed):
        self.account_for_uid(uid)
        self.db.execute("INSERT OR REPLACE INTO access VALUES (?,?)", (uid, int(allowed)))
        self.db.execute('UPDATE accounts SET enabled=? WHERE user_id=?',(int(allowed),uid))
        self.db.commit()

    def seen(self, update_id):
        try:
            self.db.execute("INSERT INTO seen VALUES (?)", (update_id,))
            self.db.commit()
            return False
        except sqlite3.IntegrityError:
            return True

    def enqueue(self, uid, chat_id, kind, prompt, image=None, hd=False, audio=None, parent_job_id=None, update_id=None, message_id=None, *, session_id=None, channel='telegram', request_id=None, audio_digest=None, duration_seconds=None, _project_slot=False, _project=None, _video_references=None, _unlimited=False):
        account = self.account_for_uid(uid)
        if duration_seconds is not None and (kind!='video' or isinstance(duration_seconds,bool) or not isinstance(duration_seconds,int) or not 4<=duration_seconds<=15):
            raise ValueError('单个 H3 镜头时长须为4–15秒。')
        if parent_job_id:
            parent=self.get(parent_job_id)
            if not parent or parent['account_id']!=account['id'] or (session_id and session_id!=parent['session_id']):
                raise PermissionError('子任务归属无效。')
            session_id=parent['session_id'];channel=parent['channel'];chat_id=parent['chat_id']
        session = self.require_session(account['id'],session_id) if session_id else self.telegram_session(uid,chat_id)
        if request_id is not None:
            import hashlib
            if not isinstance(request_id,str) or not 1<=len(request_id)<=128:
                raise ValueError('无效请求编号。')
            fingerprint=hashlib.sha256(json.dumps([kind,prompt,image,hd,audio_digest or audio,parent_job_id]+([duration_seconds] if duration_seconds is not None else []),ensure_ascii=False).encode()).hexdigest()
            old=self.db.execute('SELECT * FROM mobile_requests WHERE account_id=? AND request_id=?',(account['id'],request_id)).fetchone()
            if old:
                if old['session_id']!=session['id'] or old['fingerprint']!=fingerprint:
                    raise ValueError('请求编号已用于不同输入。')
                return old['job_id']
        if update_id is not None:
            old = self.db.execute('SELECT job_id FROM inbound_updates WHERE update_id=?',(update_id,)).fetchone()
            if old:
                return old[0]
        count = self.db.execute("SELECT count(*) FROM jobs j WHERE account_id=? AND status IN ('queued','running','waiting_approval') AND NOT EXISTS(SELECT 1 FROM video_shots s WHERE s.job_id=j.id) AND kind!='drama' AND NOT EXISTS(SELECT 1 FROM drama_takes t WHERE t.job_id=j.id)", (account['id'],)).fetchone()[0]
        if _project_slot:
            project=self.video_projects.get(parent_job_id) if parent_job_id else None
            if not project or kind!='video' or project['account_id']!=account['id'] or project['session_id']!=session['id']:
                raise PermissionError('无效分镜归属。')
            active=self.db.execute("SELECT count(*) FROM jobs WHERE parent_job_id=? AND status IN ('queued','running','waiting_approval')",(parent_job_id,)).fetchone()[0]
            if active>=2:raise ValueError('视频预备窗口已满。')
        if count >= 3 and not _project_slot and not _unlimited:
            raise ValueError("你已有 3 个待处理任务，请等待完成或先 /cancel。")
        job_id = uuid.uuid4().hex[:12]
        with self.db:
            self.db.execute("INSERT INTO jobs (id,user_id,chat_id,kind,prompt,image,hd,status,created,audio,parent_job_id) VALUES (?,?,?,?,?,?,?,'queued',?,?,?)",
                            (job_id, uid, chat_id, kind, prompt, image, int(hd), time.time(), audio, parent_job_id))
            self.db.execute('UPDATE jobs SET task_id=? WHERE id=?',(job_id,job_id))
            self.db.execute('UPDATE jobs SET account_id=?,session_id=?,channel=? WHERE id=?',(account['id'],session['id'],channel,job_id))
            self.db.execute('INSERT INTO tasks(id,user_id,chat_id,created) VALUES (?,?,?,?)',(job_id,uid,chat_id,time.time()))
            self.db.execute('UPDATE tasks SET account_id=?,session_id=? WHERE id=?',(account['id'],session['id'],job_id))
            self.db.execute('INSERT INTO task_inputs(job_id,task_id,text,created,consumed) VALUES (?,?,?,?,1)',(job_id,job_id,prompt,time.time()))
            if update_id is not None:
                self.db.execute('INSERT INTO inbound_updates VALUES (?,?,?,?)',(update_id,job_id,chat_id,message_id))
            if request_id is not None:
                self.db.execute('INSERT INTO mobile_requests VALUES (?,?,?,?,?)',(account['id'],request_id,session['id'],fingerprint,job_id))
            self.session_event(account['id'],session['id'],'message_created',{'job_id':job_id})
            if kind=='video':
                self.db.execute('UPDATE jobs SET duration_seconds=? WHERE id=?',(duration_seconds or 15,job_id))
            if _project_slot:
                self.db.execute('INSERT INTO task_skills SELECT ?,snapshot,? FROM task_skills WHERE task_id=?',(job_id,time.time(),parent['task_id']))
            self.capture_skills(job_id)
            self.evolution.capture(job_id)
            from .media_prompts import capture
            capture(self, self.get(job_id))
            if kind=='video' and _video_references is not None:
                from .video_review import references
                row=self.db.execute('SELECT snapshot FROM media_prompts WHERE job_id=?',(job_id,)).fetchone()
                captured=json.loads(row[0]);captured['references']=references(_video_references)
                self.db.execute('UPDATE media_prompts SET snapshot=? WHERE job_id=?',(json.dumps(captured,ensure_ascii=False),job_id))
            if _project is not None:
                self.video_projects.insert(job_id,parent_job_id,_project)
        return job_id

    def get(self, job_id):
        row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def next(self, excluded_sessions=(), excluded_kinds=()):
        if self.evolution.draining():return None
        excluded = set(excluded_sessions)
        for row in self.db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created"):
            if row['session_id'] not in excluded and (row['user_id'], row['chat_id']) not in excluded and row['kind'] not in excluded_kinds:
                return dict(row)
        return None

    def update(self, job_id, **fields):
        assert set(fields) <= {"status", "outcome", "result", "error", "delivered", "prompt", "voice_result", "text_delivered", "delivery_attempts", "delivery_error", "retry_after", "delivery_state", "attempt"}
        self.db.execute("UPDATE jobs SET " + ','.join(k+'=?' for k in fields) + " WHERE id=?", (*fields.values(), job_id))
        if 'outcome' in fields:
            self.db.execute('UPDATE tasks SET outcome=? WHERE id=(SELECT task_id FROM jobs WHERE id=?)',(fields['outcome'],job_id))
        if 'prompt' in fields:
            self.db.execute('UPDATE task_inputs SET text=? WHERE job_id=?',(fields['prompt'],job_id))
        job=self.get(job_id)
        if job and job.get('session_id'):
            payload={'job_id':job_id,**{k:v for k,v in fields.items() if k not in ('result','prompt','voice_result')}}
            if fields.get('status')=='succeeded':
                shot=self.db.execute('SELECT s.ordinal,p.target_seconds FROM video_shots s JOIN video_projects p ON p.job_id=s.project_id WHERE s.job_id=?',(job_id,)).fetchone()
                if shot:payload['video_shot']={'index':shot['ordinal']+1,'total':(shot['target_seconds']+14)//15}
            self.session_event(job['account_id'],job['session_id'],'task_updated',payload)
        self.db.commit()
        self.settle_parent(job_id)

    def settle_parent(self, child_id):
        child=self.get(child_id)
        if not child or not child.get('parent_job_id'):return
        parent=self.get(child['parent_job_id'])
        if not parent or (child['account_id'],child['session_id']) != (parent['account_id'],parent['session_id']):return
        # Segment delivery can complete a project after its summary was sent.
        if self.video_projects.get(parent['id']):
            self.settle_parent(parent['id'])
        # A child finishing cannot discharge every requirement in a parent.
        # Only the reviewer's host-verified, per-requirement watch may do that.
        watchers=[dict(r) for r in self.db.execute('''SELECT * FROM jobs
            WHERE task_id=? AND account_id=? AND session_id=? AND outcome='pending' AND kind='chat' ''',
            (parent['task_id'],parent['account_id'],parent['session_id']))]
        for watcher in watchers:
            record=self.db.execute("SELECT result FROM pi_tools WHERE job_id=? AND name='final_check' ORDER BY id DESC LIMIT 1",
                                   (watcher['id'],)).fetchone()
            try:
                proof=json.loads(record[0]) if record else {}
            except (ValueError,TypeError):
                continue
            watch=proof.get('completion_watch')
            if (proof.get('accepted') is not True or proof.get('completion_watch_only') is not True
                    or not isinstance(watch,list) or not watch or child_id not in watch):continue
            children=[]
            for watched_id in watch:
                row=self.db.execute('''SELECT c.* FROM jobs c JOIN jobs p ON p.id=c.parent_job_id
                    WHERE c.id=? AND p.task_id=? AND p.account_id=? AND p.session_id=?
                      AND c.account_id=p.account_id AND c.session_id=p.session_id''',
                    (watched_id,watcher['task_id'],watcher['account_id'],watcher['session_id'])).fetchone()
                if not row:break
                children.append(dict(row))
            if len(children)!=len(watch):continue
            if any(c['status'] in ('failed','cancelled') or c['delivery_state'] in ('failed','uncertain') for c in children):
                self.update(watcher['id'],outcome='partial')
                continue
            def ready(item):
                if item['kind'] not in ('image','video') or item['status']!='succeeded':return False
                path=Path(item['result'] or '')
                try:
                    if path.is_symlink() or not path.is_file() or path.stat().st_size==0:return False
                except OSError:
                    return False
                if item['channel']=='app':return True
                if self.video_projects.get(item['id']) and item['delivery_state']=='notice_sent':
                    summary=self.receipt(item['id'],'project_summary')
                    segments=self.db.execute('SELECT j.* FROM video_shots s JOIN jobs j ON j.id=s.job_id WHERE s.project_id=? ORDER BY s.ordinal',(item['id'],)).fetchall()
                    expected=self.video_projects.public(item['id'])['total']
                    return bool(summary and summary['chat_id']==item['chat_id'] and summary['message_id']>0
                                and len(segments)==expected and all(ready(dict(segment)) for segment in segments))
                receipt=self.receipt(item['id'],'media')
                return bool(item['delivery_state']=='delivered' and receipt
                            and receipt['chat_id']==item['chat_id'] and receipt['message_id']>0)
            if all(ready(c) for c in children):
                self.update(watcher['id'],outcome='completed')

    def messages(self, uid, prompt, *, chat_id=None, before=None, session_id=None):
        if chat_id is None and session_id is None:
            rows = self.db.execute("SELECT role,content FROM history WHERE user_id=? ORDER BY id DESC LIMIT 20", (uid,)).fetchall()
            history = [dict(x) for x in reversed(rows)]
        else:
            # Reconstruct actual order, including legacy failures omitted by remember().
            session = self.require_session(self.account_for_uid(uid)['id'],session_id) if session_id else self.telegram_session(uid,chat_id)
            rows = self.db.execute(
                "SELECT id,prompt,result,status,outcome FROM jobs WHERE account_id=? AND session_id=? AND kind='chat' "
                "AND status IN ('succeeded','failed') AND created<? AND id NOT IN (SELECT job_id FROM deleted_jobs) "
                "AND created>COALESCE((SELECT reset_at FROM session_resets WHERE session_id=?),0) "
                "ORDER BY created DESC LIMIT 10", (session['account_id'],session['id'],before or time.time(),session['id'])).fetchall()
            groups = []
            for row in reversed(rows):
                group = [{'role':'user','content':row['prompt'],'source':'user','source_job_id':row['id']}]
                answer = self.accepted_answer(dict(row))
                if answer is not None:
                    group.append({'role':'assistant','content':answer,'source':'delivered_answer','source_job_id':row['id']})
                groups.append(group)
            # Failed/absorbed inputs can be user-only, so trim whole source
            # groups rather than assuming every two messages form a turn.
            while groups and sum(len(message['content']) for group in groups for message in group) > 12000:
                groups.pop(0)
            history = [message for group in groups for message in group]
        # Bound old text, leaving room for tools and the current 32k-context run.
        while history and sum(len(x['content']) for x in history) > 12000:
            history = history[2:]
        return [{"role": "system", "content": "你是本地个人助手。使用用户的语言回答，清楚准确；不确定时说明。"}, *history, {"role": "user", "content": prompt}]

    def remember(self, uid, prompt, reply, *, session_id=None):
        if session_id:
            # jobs/checkpoints are the session history. Do not leak App text to
            # the legacy per-user history projection.
            return
        self.db.executemany("INSERT INTO history (user_id,role,content) VALUES (?,?,?)", [(uid,'user',prompt),(uid,'assistant',reply)])
        self.db.commit()

    def reset(self, uid, session_id=None):
        account=self.account_for_uid(uid)
        session=self.require_session(account['id'],session_id) if session_id else self.telegram_session(uid,uid)
        self.db.execute('INSERT OR REPLACE INTO session_resets VALUES (?,?)',(session['id'],time.time()))
        self.db.execute('DELETE FROM session_imports WHERE session_id=?',(session['id'],))
        legacy_default=self.db.execute(
            "SELECT 1 FROM channel_sessions WHERE channel='telegram' AND user_id=? AND chat_id=? AND session_id=?",
            (uid,uid,session['id'])).fetchone()
        if legacy_default:
            self.db.execute("DELETE FROM history WHERE user_id=?", (uid,))
        self.db.commit()

    def pending_delivery(self):
        return [dict(x) for x in self.db.execute("SELECT * FROM jobs WHERE channel='telegram' AND status IN ('succeeded','failed') AND delivered=0 AND delivery_state NOT IN ('uncertain','failed','suppressed') ORDER BY created")]

    def receipt(self, job_id, part):
        row = self.db.execute('SELECT * FROM delivery_receipts WHERE job_id=? AND part=?', (job_id,part)).fetchone()
        return dict(row) if row else None

    def record_receipt(self, job_id, part, chat_id, message_id):
        if type(message_id) is not int or message_id <= 0:
            raise RuntimeError('Telegram 未返回有效消息回执。')
        self.db.execute('INSERT OR REPLACE INTO delivery_receipts VALUES (?,?,?,?,?)',
                        (job_id,part,chat_id,message_id,time.time()))
        self.db.execute("UPDATE outbox SET state='delivered',updated=? WHERE job_id=? AND part=?",(time.time(),job_id,part))
        self.db.commit()
