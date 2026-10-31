"""Scheduled agent tasks: structured recurrence, local time, at most one catch-up run.

The model fills structured fields (kind + time) instead of writing cron syntax.
When due, a schedule becomes an ordinary chat job in its session. A schedule with
notify='when_relevant' tells the agent to answer exactly SILENT when there is
nothing worth reporting; the harness then suppresses delivery of that reply.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timedelta

SILENT = '【静默】'
KINDS = ('once', 'hourly', 'daily', 'weekly', 'interval')
WEEKDAYS = '一二三四五六日'

SCHEMA = '''
CREATE TABLE IF NOT EXISTS scheduled_tasks(
    id TEXT PRIMARY KEY, account_id TEXT NOT NULL, session_id TEXT NOT NULL, title TEXT NOT NULL,
    prompt TEXT NOT NULL, kind TEXT NOT NULL, spec TEXT NOT NULL, notify TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1, next_run REAL, last_run REAL, last_job_id TEXT, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS scheduled_due ON scheduled_tasks(enabled, next_run);
'''


def clock(value):
    if not isinstance(value, str) or not re.fullmatch(r'([01]?\d|2[0-3]):[0-5]\d', value):
        raise ValueError('time 须为 24 小时制 HH:MM，例如 08:00。')
    h, m = value.split(':')
    return int(h), int(m)


def normalize(kind, spec):
    if kind not in KINDS:
        raise ValueError('kind 须为 once、hourly、daily、weekly 或 interval。')
    spec = dict(spec or {})
    if kind == 'once':
        try:
            at = datetime.strptime(spec.get('at', ''), '%Y-%m-%d %H:%M')
        except ValueError:
            raise ValueError('once 需要 at，格式 YYYY-MM-DD HH:MM（本机时间）。') from None
        if at.timestamp() <= time.time():
            raise ValueError('at 必须是将来的时间。')
        return {'at': spec['at']}
    if kind == 'hourly':
        minute = spec.get('minute', 0)
        if not isinstance(minute, int) or not 0 <= minute <= 59:
            raise ValueError('hourly 的 minute 须为 0–59。')
        return {'minute': minute}
    if kind == 'daily':
        clock(spec.get('time'))
        return {'time': spec['time']}
    if kind == 'weekly':
        day = spec.get('weekday')
        if not isinstance(day, int) or not 1 <= day <= 7:
            raise ValueError('weekly 的 weekday 须为 1–7（周一到周日）。')
        clock(spec.get('time'))
        return {'weekday': day, 'time': spec['time']}
    minutes = spec.get('minutes')
    if not isinstance(minutes, int) or not 15 <= minutes <= 7 * 24 * 60:
        raise ValueError('interval 的 minutes 须为 15 到 10080。')
    return {'minutes': minutes}


def next_run(kind, spec, after):
    now = datetime.fromtimestamp(after)
    if kind == 'once':
        at = datetime.strptime(spec['at'], '%Y-%m-%d %H:%M')
        return at.timestamp() if at.timestamp() > after else None
    if kind == 'interval':
        return after + spec['minutes'] * 60
    if kind == 'hourly':
        candidate = now.replace(minute=spec['minute'], second=0, microsecond=0)
        return (candidate if candidate > now else candidate + timedelta(hours=1)).timestamp()
    h, m = clock(spec['time'])
    candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if kind == 'daily':
        return (candidate if candidate > now else candidate + timedelta(days=1)).timestamp()
    days = (spec['weekday'] - 1 - now.weekday()) % 7
    candidate += timedelta(days=days)
    return (candidate if candidate > now else candidate + timedelta(days=7)).timestamp()


def describe(kind, spec):
    if kind == 'once':
        return '在 ' + spec['at']
    if kind == 'hourly':
        return f"每小时第 {spec['minute']} 分"
    if kind == 'daily':
        return '每天 ' + spec['time']
    if kind == 'weekly':
        return f"每周{WEEKDAYS[spec['weekday'] - 1]} {spec['time']}"
    return f"每 {spec['minutes']} 分钟"


class Schedules:
    def __init__(self, store):
        self.store = store
        store.db.executescript(SCHEMA)
        store.db.commit()

    def public(self, row):
        spec = json.loads(row['spec'])
        return {'id': row['id'], 'title': row['title'], 'prompt': row['prompt'], 'when': describe(row['kind'], spec),
                'kind': row['kind'], 'spec': spec, 'notify': row['notify'], 'enabled': bool(row['enabled']),
                'next_run': time.strftime('%Y-%m-%d %H:%M', time.localtime(row['next_run'])) if row['next_run'] else None,
                'last_job_id': row['last_job_id']}

    def create(self, account_id, session_id, title, prompt, kind, spec, notify='always'):
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 60:
            raise ValueError('title 须为 1–60 字。')
        if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 2000:
            raise ValueError('prompt 须为 1–2000 字。')
        if notify not in ('always', 'when_relevant'):
            raise ValueError('notify 须为 always 或 when_relevant。')
        if self.store.db.execute('SELECT count(*) FROM scheduled_tasks WHERE account_id=? AND enabled=1', (account_id,)).fetchone()[0] >= 30:
            raise ValueError('启用中的定时任务已达 30 个，请先删除不需要的。')
        spec = normalize(kind, spec)
        sid = uuid.uuid4().hex[:12]
        with self.store.db:
            self.store.db.execute('INSERT INTO scheduled_tasks VALUES (?,?,?,?,?,?,?,?,1,?,NULL,NULL,?)',
                                  (sid, account_id, session_id, title.strip(), prompt.strip(), kind, json.dumps(spec),
                                   notify, next_run(kind, spec, time.time()), time.time()))
        return self.get(account_id, sid)

    def get(self, account_id, sid):
        row = self.store.db.execute('SELECT * FROM scheduled_tasks WHERE id=? AND account_id=?', (sid, account_id)).fetchone()
        if not row:
            raise ValueError('未找到这个定时任务。')
        return self.public(row)

    def list(self, account_id):
        return [self.public(r) for r in self.store.db.execute(
            'SELECT * FROM scheduled_tasks WHERE account_id=? ORDER BY enabled DESC, next_run', (account_id,))]

    def set_enabled(self, account_id, sid, enabled):
        row = self.store.db.execute('SELECT * FROM scheduled_tasks WHERE id=? AND account_id=?', (sid, account_id)).fetchone()
        if not row:
            raise ValueError('未找到这个定时任务。')
        upcoming = next_run(row['kind'], json.loads(row['spec']), time.time()) if enabled else row['next_run']
        with self.store.db:
            self.store.db.execute('UPDATE scheduled_tasks SET enabled=?, next_run=? WHERE id=?', (int(enabled), upcoming, sid))
        return self.get(account_id, sid)

    def delete(self, account_id, sid):
        with self.store.db:
            changed = self.store.db.execute('DELETE FROM scheduled_tasks WHERE id=? AND account_id=?', (sid, account_id)).rowcount
        if not changed:
            raise ValueError('未找到这个定时任务。')
        return {'deleted': sid}

    def due(self, now=None):
        now = now or time.time()
        return [dict(r) for r in self.store.db.execute(
            'SELECT * FROM scheduled_tasks WHERE enabled=1 AND next_run IS NOT NULL AND next_run<=? ORDER BY next_run', (now,))]

    def fire(self, row, now=None):
        """Enqueue one run; missed occurrences collapse into this single catch-up."""
        now = now or time.time()
        account = self.store.account(row['account_id'])
        if not account or not account.get('enabled', True):
            return None
        prompt = f"[定时任务「{row['title']}」，{describe(row['kind'], json.loads(row['spec']))}] {row['prompt']}"
        if row['notify'] == 'when_relevant':
            prompt += f'\n\n如果这次没有需要告诉用户的情况，最终回复只写 {SILENT}，不要写别的。'
        jid = self.store.enqueue(account['user_id'], None, 'chat', prompt, session_id=row['session_id'], channel='app',
                                 request_id=f"schedule:{row['id']}:{int(row['next_run'])}")
        upcoming = next_run(row['kind'], json.loads(row['spec']), now)
        with self.store.db:
            self.store.db.execute('UPDATE scheduled_tasks SET last_run=?, last_job_id=?, next_run=?, enabled=? WHERE id=?',
                                  (now, jid, upcoming, int(upcoming is not None), row['id']))
        return jid
