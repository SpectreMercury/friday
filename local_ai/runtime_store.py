"""Durable conversation checkpoints and tool-operation journal.

Opening a database never changes live jobs. Only the service owner may invoke
restart recovery after it has acquired the single-service lock.
"""
import hashlib
import json
import time
import uuid

from .tool_errors import ToolFailure


MAX_CHECKPOINT_BYTES = 4_000_000


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


class RuntimeStore:
    def init_runtime_schema(self):
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS agent_checkpoints (
                job_id TEXT PRIMARY KEY REFERENCES jobs(id), revision INTEGER NOT NULL,
                messages TEXT NOT NULL, saved_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tool_operations (
                job_id TEXT NOT NULL REFERENCES jobs(id), call_id TEXT NOT NULL,
                name TEXT NOT NULL, arguments TEXT NOT NULL, fingerprint TEXT NOT NULL,
                effect TEXT NOT NULL, replay TEXT NOT NULL, status TEXT NOT NULL,
                result TEXT, failure TEXT, started_at REAL NOT NULL, ended_at REAL,
                PRIMARY KEY(job_id, call_id)
            );
            CREATE TABLE IF NOT EXISTS task_contracts (
                job_id TEXT, input_hash TEXT, contract TEXT, created REAL,
                PRIMARY KEY(job_id,input_hash)
            );
            CREATE TABLE IF NOT EXISTS runtime_events (
                sequence INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id),
                kind TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS task_runs (
                id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id),
                task_id TEXT NOT NULL, account_id TEXT NOT NULL, session_id TEXT NOT NULL,
                attempt INTEGER NOT NULL CHECK(attempt>0), status TEXT NOT NULL,
                started_at REAL NOT NULL, ended_at REAL,
                UNIQUE(job_id,attempt)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS task_runs_active ON task_runs(job_id) WHERE ended_at IS NULL;
            CREATE INDEX IF NOT EXISTS task_runs_session ON task_runs(account_id,session_id,task_id,started_at);
        ''')
        columns = {r[1] for r in self.db.execute('PRAGMA table_info(jobs)')}
        for name, kind in [('attempt', 'INTEGER DEFAULT 0'), ('restart_count', 'INTEGER DEFAULT 0'),
                           ('delivery_state', "TEXT DEFAULT 'pending'")]:
            if name not in columns:
                self.db.execute(f'ALTER TABLE jobs ADD COLUMN {name} {kind}')
        self.db.commit()
        from .attachments import Attachments
        Attachments.init_schema(self.db)

    def event(self, job_id, kind, payload):
        self.db.execute('INSERT INTO runtime_events(job_id,kind,payload,created) VALUES (?,?,?,?)',
                        (job_id, kind, encode(payload), time.time()))
        job=self.get(job_id)
        if job and job.get('session_id'):
            self.session_event(job['account_id'],job['session_id'],kind,{'job_id':job_id,**payload})

    def save_checkpoint(self, job_id, messages):
        if not self.get(job_id):
            raise ValueError('Unknown checkpoint owner')
        if not isinstance(messages, list) or any(not isinstance(m, dict) or m.get('role') not in
                ('user', 'assistant', 'toolResult') for m in messages):
            raise ValueError('Invalid conversation checkpoint')
        data = encode(messages)
        if len(data.encode('utf-8')) > MAX_CHECKPOINT_BYTES:
            raise RuntimeError('会话保存内容超过 4 MB 上限；此前已保存的请求和执行记录仍然保留。')
        with self.db:
            self.db.execute('''INSERT INTO agent_checkpoints VALUES (?,1,?,?)
                ON CONFLICT(job_id) DO UPDATE SET revision=revision+1,
                messages=excluded.messages,saved_at=excluded.saved_at''', (job_id, data, time.time()))
        return self.db.execute('SELECT revision FROM agent_checkpoints WHERE job_id=?', (job_id,)).fetchone()[0]

    def checkpoint(self, job_id):
        row = self.db.execute('SELECT messages FROM agent_checkpoints WHERE job_id=?', (job_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def operation(self, job_id, call_id):
        row = self.db.execute('SELECT * FROM tool_operations WHERE job_id=? AND call_id=?', (job_id, call_id)).fetchone()
        return dict(row) if row else None

    def begin_operation(self, job_id, call_id, spec, arguments):
        if not isinstance(call_id, str) or not 1 <= len(call_id) <= 200:
            raise ValueError('Invalid tool call id')
        args = encode(arguments)
        fingerprint = hashlib.sha256((spec.name+'\n'+args).encode()).hexdigest()
        with self.db:
            old = self.operation(job_id, call_id)
            pending_effect = self.db.execute('''SELECT call_id FROM tool_operations
                WHERE job_id IN (SELECT id FROM jobs WHERE task_id=(SELECT task_id FROM jobs WHERE id=?))
                AND fingerprint=? AND replay!='safe' AND status IN ('started','uncertain')
                AND NOT (job_id=? AND call_id=?) LIMIT 1''', (job_id, fingerprint, job_id, call_id)).fetchone()
            if pending_effect:
                raise ToolFailure('effect_uncertain', '同一操作已有未核实的执行记录；更换调用编号也不能重复执行。', uncertain=True)
            if old:
                if old['fingerprint'] != fingerprint:
                    raise ToolFailure('call_id_conflict', '同一个操作编号不能更改工具或参数。')
                if old['status'] in ('succeeded', 'failed'):
                    return {'cached': True, 'result': json.loads(old['result']) if old['result'] else None,
                            'failure': json.loads(old['failure']) if old['failure'] else None}
                if old['replay'] != 'safe' and old['status'] != 'waiting_approval':
                    failure = ToolFailure('effect_uncertain', '上次操作可能已经生效，但没有完整回执。先核对实际结果，不得直接重复。', uncertain=True)
                    self.db.execute("UPDATE tool_operations SET status='uncertain',failure=? WHERE job_id=? AND call_id=?",
                                    (encode(failure.payload()), job_id, call_id))
                    self.event(job_id, 'operation_uncertain', {'call_id': call_id, 'name': spec.name})
                    return {'cached': True, 'result': None, 'failure': failure.payload()}
                self.db.execute("UPDATE tool_operations SET status='started' WHERE job_id=? AND call_id=?",(job_id,call_id))
                # A pending approval has not started the operation; reads also replay safely.
                self.event(job_id, 'operation_read_retry', {'call_id': call_id, 'name': spec.name})
                return {'cached': False}
            self.db.execute('''INSERT INTO tool_operations
                (job_id,call_id,name,arguments,fingerprint,effect,replay,status,started_at)
                VALUES (?,?,?,?,?,?,?,'started',?)''',
                (job_id, call_id, spec.name, args, fingerprint, spec.effect, spec.replay, time.time()))
            self.event(job_id, 'operation_started', {'call_id': call_id, 'name': spec.name, 'effect': spec.effect})
        return {'cached': False}

    def finish_operation(self, job_id, call_id, *, result=None, failure=None):
        with self.db:
            row = self.operation(job_id, call_id)
            if not row or row['status'] != 'started':
                raise ValueError('Operation was not admitted or has already ended')
            self.db.execute('''UPDATE tool_operations SET status=?,result=?,failure=?,ended_at=?
                WHERE job_id=? AND call_id=?''',
                ('uncertain' if failure and failure.get('uncertain') else 'failed' if failure else 'succeeded', encode(result), encode(failure) if failure else None,
                 time.time(), job_id, call_id))
            self.event(job_id, 'operation_finished', {'call_id': call_id, 'name': row['name'], 'failed': bool(failure)})

    def settle_verified_operation(self, job_id, call_id, result):
        with self.db:
            row=self.operation(job_id,call_id)
            if not row or row['status'] not in ('started','uncertain'):
                raise ValueError('Operation is not awaiting reconciliation')
            self.db.execute("UPDATE tool_operations SET status='succeeded',result=?,failure=NULL,ended_at=? WHERE job_id=? AND call_id=?",
                (encode(result),time.time(),job_id,call_id))
            self.event(job_id,'operation_reconciled',{'call_id':call_id,'name':row['name'],'basis':'host_verified_receipt_or_file'})

    def start_run(self, job_id):
        """Atomically claim a queued job and record its next real execution.

        Old attempt counters are retained; no historical runs are invented.
        The task's skill snapshot and tool-call journal are deliberately untouched.
        """
        with self.db:
            job = self.get(job_id)
            if not job or job['status'] != 'queued':
                raise ValueError('Only a queued job can start a run')
            self.require_session(job['account_id'], job['session_id'])
            if self.db.execute('SELECT 1 FROM task_runs WHERE job_id=? AND ended_at IS NULL', (job_id,)).fetchone():
                raise ValueError('Job already has an unfinished run; recover it before retrying')
            run_id, attempt, now = uuid.uuid4().hex, (job.get('attempt') or 0) + 1, time.time()
            changed = self.db.execute("UPDATE jobs SET status='running',attempt=? WHERE id=? AND status='queued'",
                                      (attempt, job_id))
            if changed.rowcount != 1:
                raise ValueError('Job was claimed or cancelled before run admission')
            self.db.execute('INSERT INTO task_runs VALUES (?,?,?,?,?,?,\'running\',?,NULL)',
                            (run_id, job_id, job['task_id'], job['account_id'], job['session_id'], attempt, now))
            self.event(job_id, 'run_started', {'run_id': run_id, 'task_id': job['task_id'], 'attempt': attempt})
            self.session_event(job['account_id'], job['session_id'], 'task_updated',
                               {'job_id': job_id, 'status': 'running', 'attempt': attempt})
        return dict(self.db.execute('SELECT * FROM task_runs WHERE id=?', (run_id,)).fetchone())

    def finish_run(self, run_id):
        """Settle this attempt once; a queued job means the attempt was interrupted."""
        with self.db:
            run = self.db.execute('SELECT * FROM task_runs WHERE id=?', (run_id,)).fetchone()
            if not run:
                raise ValueError('Unknown execution run')
            if run['ended_at'] is not None:
                return dict(run)
            job = self.get(run['job_id'])
            status = job['status'] if job['status'] in ('succeeded', 'failed', 'cancelled', 'waiting_approval') else 'interrupted'
            self.db.execute('UPDATE task_runs SET status=?,task_id=?,ended_at=? WHERE id=? AND ended_at IS NULL',
                            (status, job['task_id'], time.time(), run_id))
            self.event(job['id'], 'run_finished', {'run_id': run_id, 'task_id': job['task_id'],
                                                  'attempt': run['attempt'], 'status': status})
        return dict(self.db.execute('SELECT * FROM task_runs WHERE id=?', (run_id,)).fetchone())

    def session_runs(self, account_id, session_id, job_id):
        """Return all execution attempts for this owned job's current logical task."""
        job = self.get_session_job(account_id, session_id, job_id)
        return [dict(r) for r in self.db.execute('''SELECT r.* FROM task_runs r JOIN jobs j ON j.id=r.job_id
            WHERE r.account_id=? AND r.session_id=? AND j.account_id=? AND j.session_id=? AND j.task_id=?
            ORDER BY r.started_at,r.id''', (account_id, session_id, account_id, session_id, job['task_id']))]

    def recover_interrupted(self):
        """Called once by the exclusive service owner, not by readers/test clients."""
        recovered = []
        with self.db:
            for run in self.db.execute('SELECT * FROM task_runs WHERE ended_at IS NULL').fetchall():
                self.db.execute("UPDATE task_runs SET status='interrupted',ended_at=? WHERE id=? AND ended_at IS NULL",
                                (time.time(), run['id']))
                self.event(run['job_id'], 'run_interrupted', {'run_id': run['id'], 'attempt': run['attempt']})
            for row in self.db.execute("SELECT * FROM jobs WHERE status='running'").fetchall():
                job = dict(row)
                # Chat resumes its durable transcript. Local rendering may regenerate
                # an unreturned file; it never sends from the render worker itself.
                self.db.execute("UPDATE jobs SET status='queued',error=NULL,restart_count=restart_count+1 WHERE id=?", (job['id'],))
                self.event(job['id'], 'restart_recovery', {'kind': job['kind'], 'checkpoint': bool(self.checkpoint(job['id']))})
                recovered.append(job['id'])
        return recovered
