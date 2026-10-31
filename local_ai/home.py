"""One summary for the app's first screen: what the Mac is doing and what waits on the user.

The GPU renders one image or video at a time for every account, so queue
positions count everyone's queued renders while only the caller's own jobs are
described. Times are estimates from recent runs of the same kind; nothing here
is a promise, and the client labels them as such.
"""
from __future__ import annotations

import time

from .job_presentation import job_title

GPU_KINDS = ('image', 'video')
DEFAULT_SECONDS = {'image': 60, 'video': 600}


def typical_seconds(store, kind):
    rows = store.db.execute('''SELECT SUM(MAX(0,r.ended_at-r.started_at)) AS seconds FROM task_runs r JOIN jobs j ON j.id=r.job_id
        WHERE j.kind=? AND j.status='succeeded' AND r.ended_at IS NOT NULL GROUP BY r.job_id ORDER BY MAX(r.ended_at) DESC LIMIT 20''',
        (kind,)).fetchall()
    values = sorted(r['seconds'] for r in rows if r['seconds'])
    return values[len(values) // 2] if values else DEFAULT_SECONDS.get(kind, 120)


def running_since(store, job_id):
    row = store.db.execute('SELECT MIN(started_at) AS since FROM task_runs WHERE job_id=? AND ended_at IS NULL', (job_id,)).fetchone()
    return row['since'] if row and row['since'] else None


def session_names(store, account_id):
    return {r['id']: r['name'] for r in store.db.execute('SELECT id,name FROM sessions WHERE account_id=?', (account_id,))}


def summary(store, account_id, engine=None, schedules=None, now=None):
    now = now or time.time()
    names = session_names(store, account_id)
    typical = {kind: typical_seconds(store, kind) for kind in GPU_KINDS}
    hidden = 'id NOT IN (SELECT job_id FROM deleted_jobs) AND (session_id IS NULL OR session_id NOT IN (SELECT id FROM sessions WHERE deleted IS NOT NULL))'

    def describe(job):
        return {'id': job['id'], 'kind': job['kind'], 'title': job_title(store, job), 'session_id': job['session_id'],
                'session_name': names.get(job['session_id'], '')}

    current = None
    remaining = 0.0
    running = [dict(r) for r in store.db.execute(
        f"SELECT * FROM jobs WHERE status='running' AND kind IN ('image','video') AND {hidden} ORDER BY created")]
    for job in running:
        since = running_since(store, job['id']) or job['created']
        elapsed = max(0.0, now - since)
        left = max(0.0, typical[job['kind']] - elapsed)
        remaining += left
        if current is None:
            current = {'elapsed': round(elapsed), 'eta_seconds': round(left),
                       'progress': round(min(0.95, elapsed / max(1, typical[job['kind']])), 2)}
            current.update(describe(job) if job['account_id'] == account_id else {'id': None, 'kind': job['kind'], 'title': '另一位用户的任务'})
            current['mine'] = job['account_id'] == account_id

    queue = []
    queued = store.db.execute(
        f"SELECT * FROM jobs WHERE status='queued' AND kind IN ('image','video') AND {hidden} ORDER BY created").fetchall()
    for position, row in enumerate(queued, 1):
        job = dict(row)
        remaining += typical[job['kind']]
        if job['account_id'] == account_id:
            queue.append({**describe(job), 'position': position, 'eta_seconds': round(remaining)})

    active = [{**describe(dict(r)), 'status': r['status']} for r in store.db.execute(
        f"""SELECT * FROM jobs WHERE account_id=? AND status IN ('queued','running') AND kind NOT IN ('image','video','imported')
            AND parent_job_id IS NULL AND {hidden} ORDER BY created DESC LIMIT 10""", (account_id,))]

    attention = []
    for r in store.db.execute(f"""SELECT * FROM jobs WHERE account_id=? AND parent_job_id IS NULL AND {hidden} AND (status='waiting_approval'
            OR (status='succeeded' AND outcome IN ('paused','needs_input') AND created>?)) ORDER BY created DESC LIMIT 20""",
            (account_id, now - 7 * 86400)):
        job = dict(r)
        later = store.db.execute('SELECT 1 FROM jobs WHERE session_id=? AND created>? AND parent_job_id IS NULL LIMIT 1',
                                 (job['session_id'], job['created'])).fetchone()
        if job['status'] != 'waiting_approval' and later:
            continue  # The user already answered in that conversation.
        reason = 'approval' if job['status'] == 'waiting_approval' else job['outcome']
        attention.append({**describe(job), 'reason': reason})

    upcoming = None
    for item in schedules or ():
        if item.get('enabled') and item.get('next_run') and (upcoming is None or item['next_run'] < upcoming['next_run']):
            upcoming = {k: item.get(k) for k in ('id', 'title', 'when', 'next_run')}

    midnight = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
    done_today = store.db.execute(f"SELECT count(*) FROM jobs WHERE account_id=? AND status='succeeded' AND parent_job_id IS NULL AND created>=? AND {hidden}",
                                  (account_id, midnight)).fetchone()[0]

    loaded = getattr(engine, 'kind', None) if getattr(engine, 'process', None) else None
    return {'mac': {'online': True, 'busy': bool(running), 'loaded': loaded, 'voice': bool(getattr(engine, 'voice_reserved', False)),
                    'queued': len(queued), 'done_today': done_today},
            'now': current, 'queue': queue, 'active': active, 'attention': attention, 'next_schedule': upcoming}
