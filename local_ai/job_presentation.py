"""Stable task timestamps and owned desktop artifacts for client presentation."""
import json
from pathlib import Path


def management_action(store, job):
    if job.get('outcome') not in ('blocked', 'needs_input'):
        return False
    account = store.account(job['account_id'])
    if not account or not account['is_admin']:
        return False
    current = store.require_session(job['account_id'], job['session_id'])
    if current['management']:
        return False
    for row in store.db.execute("SELECT arguments,result FROM tool_operations WHERE job_id=? AND name='capabilities' AND status='succeeded'", (job['id'],)):
        args, result = json.loads(row['arguments']), json.loads(row['result'] or '{}')
        if args.get('group') in ('computer', 'network', 'maintenance', 'mac') and not result.get('capabilities'):
            return True
    return False


def screen_artifact_path(store, job):
    for row in store.db.execute("SELECT result FROM tool_operations WHERE job_id=? AND name='computer_screen' AND status='succeeded' ORDER BY ended_at DESC", (job['id'],)):
        result = json.loads(row['result'] or '{}')
        if not result.get('captured'):
            continue
        attachment = store.db.execute('SELECT path FROM attachments WHERE id=? AND job_id=? AND session_id=? AND kind=\'image\'',
            (result.get('attachment_id'), job['id'], job['session_id'])).fetchone()
        if attachment:
            path = Path(attachment['path'])
            if path.is_file() and not path.is_symlink():
                return path
    return None


def add_job_timing(store, jobs):
    # No computed "now" in the payload: unchanged conversations retain revisions.
    indexed = {job['id']: job for job in jobs if job.get('kind') != 'imported'}
    ids = list(indexed)
    for offset in range(0, len(ids), 400):
        batch = ids[offset:offset + 400]
        placeholders = ','.join('?' for _ in batch)
        rows = store.db.execute(f'''SELECT job_id,MIN(started_at) AS started_at,MAX(ended_at) AS ended_at,
            SUM(CASE WHEN ended_at IS NOT NULL THEN MAX(0,ended_at-started_at) ELSE 0 END) AS run_seconds,
            MIN(CASE WHEN ended_at IS NULL THEN started_at END) AS running_since,COUNT(*) AS attempts
            FROM task_runs WHERE job_id IN ({placeholders}) GROUP BY job_id''', batch)
        for row in rows:
            job = indexed[row['job_id']]
            terminal = job['status'] in ('succeeded', 'failed', 'cancelled')
            job['timing'] = {'started_at': row['started_at'], 'finished_at': row['ended_at'] if terminal and row['running_since'] is None else None,
                             'run_seconds': round(row['run_seconds'], 3), 'running_since': row['running_since'], 'attempts': row['attempts']}
    # Jobs cancelled before execution and older jobs can still have an exact
    # terminal event. Never substitute the current time for missing history.
    fallback = [jid for jid, job in indexed.items() if job['status'] in ('succeeded', 'failed', 'cancelled')
                and (not job.get('timing') or job['status'] == 'cancelled')]
    for offset in range(0, len(fallback), 400):
        batch = fallback[offset:offset + 400]
        placeholders = ','.join('?' for _ in batch)
        for row in store.db.execute(f"""SELECT json_extract(payload,'$.job_id') AS job_id,MAX(created) AS finished_at
            FROM session_events WHERE kind='task_updated'
            AND json_extract(payload,'$.status') IN ('succeeded','failed','cancelled')
            AND json_extract(payload,'$.job_id') IN ({placeholders}) GROUP BY job_id""", batch):
            job = indexed[row['job_id']]
            timing = job.setdefault('timing', {'started_at': None, 'run_seconds': None, 'running_since': None, 'attempts': 0})
            timing['finished_at'] = max(row['finished_at'], timing.get('finished_at') or 0)
    return jobs


# -- titles and status words shared by the PWA and the iOS app ---------------
OUTCOME_TEXT = {'needs_input': '需要补充', 'blocked': '未完成', 'partial': '部分完成', 'paused': '已暂停',
                'continued': '已并入上一条', 'silent': '无需提醒', 'pending': '后台生成中'}
STATUS_TEXT = {'queued': '排队中', 'running': '进行中', 'waiting_approval': '等待确认', 'succeeded': '已完成',
               'failed': '失败', 'cancelled': '已停止'}
DRAMA_OPS = {'analyze': '补全设定', 'storyboard': '生成分镜', 'export': '合成全片'}


def status_text(job):
    if job.get('status') == 'succeeded' and job.get('outcome') in OUTCOME_TEXT:
        return OUTCOME_TEXT[job['outcome']]
    return STATUS_TEXT.get(job.get('status'), job.get('status') or '')


def first_line(text, limit=60):
    text = ' '.join((text or '').split())
    return text if len(text) <= limit else text[:limit - 1] + '…'


def job_title(store, job):
    """A short human title. Raw prompts can be JSON, 1000-character English or schedule envelopes."""
    prompt = job.get('prompt') or ''
    kind = job.get('kind')
    if kind == 'chat':
        if prompt.startswith('[定时任务「') and '」' in prompt:
            return '定时 · ' + prompt[len('[定时任务「'):prompt.index('」')]
        return first_line(prompt)
    if kind == 'drama' and store:
        try:
            data = json.loads(prompt)
            project = store.db.execute('SELECT title FROM drama_projects WHERE id=?', (data.get('project'),)).fetchone()
            return f"短剧「{project['title'] if project else ''}」· {DRAMA_OPS.get(data.get('op'), '处理')}"
        except (ValueError, TypeError):
            return '短剧 · 处理中'
    if kind in ('image', 'video'):
        label = '图片' if kind == 'image' else '视频'
        if store:
            take = store.db.execute('''SELECT t.kind, p.title FROM drama_takes t JOIN drama_projects p ON p.id=t.project_id
                WHERE t.job_id=?''', (job['id'],)).fetchone()
            if take:
                return f"短剧「{take['title']}」· {'草稿' if take['kind'] == 'draft' else '正式'}镜头"
            parent = store.get(job['parent_job_id']) if job.get('parent_job_id') else None
            if parent:
                return f'生成{label} · ' + first_line(parent.get('prompt'), 30)
        return f'生成{label} · ' + first_line(prompt, 40)
    return first_line(prompt)


def hidden_sessions_sql(column='session_id'):
    """Drama projects keep their render jobs in their own session, shown on the drama page instead."""
    return f"({column} IS NULL OR {column} NOT IN (SELECT session_id FROM drama_projects))"
