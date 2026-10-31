"""Drama projects: durable data, shot states derived from real jobs, takes and exports.

A project owns its own session, so 20-minute renders never block chat in the
session where the script was imported. Planning and export run as `drama` jobs in
that session; each take is an ordinary video job, at most WINDOW queued per project.
"""
from __future__ import annotations

import copy
import json
import time
import uuid

from . import parser

WINDOW = 2
DRAFT = {'width': 256, 'height': 416, 'steps': 4}
VISUAL_FIELDS = ('action', 'visual', 'camera', 'character_ids', 'duration')
ACTIVE = ('waiting', 'queued', 'running', 'waiting_approval')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS drama_projects(
    id TEXT PRIMARY KEY, account_id TEXT NOT NULL, origin_session_id TEXT, session_id TEXT NOT NULL,
    title TEXT NOT NULL, script TEXT NOT NULL, script_format TEXT NOT NULL, style TEXT NOT NULL DEFAULT '',
    resolution TEXT NOT NULL DEFAULT 'sd', revision INTEGER NOT NULL DEFAULT 1, archived INTEGER NOT NULL DEFAULT 0,
    created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS drama_characters(
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
    appearance TEXT NOT NULL DEFAULT '', voice TEXT, UNIQUE(project_id, name));
CREATE TABLE IF NOT EXISTS drama_scenes(
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, ordinal INTEGER NOT NULL, heading TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '', time_of_day TEXT NOT NULL DEFAULT '', setting TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL, source_start INTEGER, source_end INTEGER);
CREATE TABLE IF NOT EXISTS drama_shots(
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, scene_id TEXT NOT NULL, ordinal REAL NOT NULL,
    duration INTEGER NOT NULL, action TEXT NOT NULL, visual TEXT NOT NULL DEFAULT '', camera TEXT NOT NULL DEFAULT '',
    character_ids TEXT NOT NULL DEFAULT '[]', dialogue TEXT NOT NULL DEFAULT '[]', prompt_override TEXT,
    approved INTEGER NOT NULL DEFAULT 0, draft_take_id TEXT, final_take_id TEXT, revision INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS drama_takes(
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, shot_id TEXT NOT NULL, kind TEXT NOT NULL,
    prompt TEXT NOT NULL, params TEXT NOT NULL, job_id TEXT, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS drama_takes_shot ON drama_takes(shot_id, created);
CREATE TABLE IF NOT EXISTS drama_exports(
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, kind TEXT NOT NULL, options TEXT NOT NULL,
    shot_takes TEXT NOT NULL, job_id TEXT, created REAL NOT NULL);
'''


def new_id():
    return uuid.uuid4().hex[:12]


from ..sessions import RevisionConflict as Conflict  # noqa: E402  (409 in the mobile API)


class Dramas:
    def __init__(self, store):
        self.store = store
        self.db = store.db
        self.db.executescript(SCHEMA)
        self.db.commit()

    # -- ownership ----------------------------------------------------
    def project(self, account_id, pid):
        row = self.db.execute('''SELECT p.* FROM drama_projects p JOIN sessions s ON s.id=p.session_id
            WHERE p.id=? AND p.account_id=? AND s.deleted IS NULL''', (pid, account_id)).fetchone()
        if not row:
            raise PermissionError('未找到这个短剧项目。')
        return dict(row)

    def project_for_session(self, session_id):
        row = self.db.execute('SELECT * FROM drama_projects WHERE session_id=?', (session_id,)).fetchone()
        return dict(row) if row else None

    def pump_all(self):
        for row in self.db.execute('SELECT DISTINCT project_id FROM drama_takes WHERE job_id IS NULL').fetchall():
            self.pump(row['project_id'])

    def take_for_job(self, job_id):
        row = self.db.execute('SELECT * FROM drama_takes WHERE job_id=?', (job_id,)).fetchone()
        return dict(row) if row else None

    def touch(self, pid, expected=...):
        """Bump the revision. Edits pass the client's revision; internal updates pass nothing."""
        if expected is not ... and (not isinstance(expected, int) or isinstance(expected, bool)):
            raise ValueError('需要当前的 revision。')
        with self.db:
            if expected is not ...:
                changed = self.db.execute('UPDATE drama_projects SET revision=revision+1, updated=? WHERE id=? AND revision=?',
                                          (time.time(), pid, expected)).rowcount
                if not changed:
                    raise Conflict('项目已在别处被修改，请刷新后再改。')
            else:
                self.db.execute('UPDATE drama_projects SET revision=revision+1, updated=? WHERE id=?', (time.time(), pid))
            project = dict(self.db.execute('SELECT * FROM drama_projects WHERE id=?', (pid,)).fetchone())
            self.store.session_event(project['account_id'], project['session_id'], 'drama_updated',
                                     {'project_id': pid, 'revision': project['revision']})
        return project

    # -- import -------------------------------------------------------
    def create(self, account_id, title, script, *, origin_session_id=None, resolution='sd'):
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 100:
            raise ValueError('标题须为 1–100 字。')
        if resolution not in ('sd', 'hd'):
            raise ValueError('分辨率须为 sd 或 hd。')
        parsed = parser.parse(script)
        session = self.store.create_session(account_id, '短剧 · ' + title.strip()[:40], skills=())
        pid = new_id()
        now = time.time()
        with self.db:
            self.db.execute('INSERT INTO drama_projects(id,account_id,origin_session_id,session_id,title,script,script_format,resolution,created,updated) VALUES (?,?,?,?,?,?,?,?,?,?)',
                            (pid, account_id, origin_session_id, session['id'], title.strip(), script, parsed['format'], resolution, now, now))
            for name in parsed['characters']:
                self.db.execute('INSERT INTO drama_characters(id,project_id,name) VALUES (?,?,?)', (new_id(), pid, name))
            for index, scene in enumerate(parsed['scenes']):
                self.db.execute('INSERT INTO drama_scenes VALUES (?,?,?,?,?,?,?,?,?,?)',
                                (new_id(), pid, index, scene['heading'], scene['location'], scene['time_of_day'], '',
                                 scene['text'], scene['source_start'], scene['source_end']))
        return self.view(account_id, pid)

    # -- reads ----------------------------------------------------------
    def list(self, account_id):
        items = []
        for row in self.db.execute('SELECT * FROM drama_projects WHERE account_id=? AND archived=0 AND session_id IN (SELECT id FROM sessions WHERE deleted IS NULL) ORDER BY updated DESC', (account_id,)):
            shots = self.shots(row['id'])
            items.append({'id': row['id'], 'title': row['title'], 'session_id': row['session_id'], 'stage': self.stage(shots),
                          'shots': len(shots), 'done': sum(s['status'] == 'done' for s in shots),
                          'seconds': sum(s['duration'] for s in shots), 'updated': row['updated']})
        return items

    def view(self, account_id, pid):
        project = self.project(account_id, pid)
        characters = [dict(r) for r in self.db.execute('SELECT * FROM drama_characters WHERE project_id=? ORDER BY rowid', (pid,))]
        scenes = [dict(r) for r in self.db.execute('SELECT * FROM drama_scenes WHERE project_id=? ORDER BY ordinal', (pid,))]
        shots = self.shots(pid)
        exports = [self.export_view(dict(r)) for r in self.db.execute('SELECT * FROM drama_exports WHERE project_id=? ORDER BY created DESC LIMIT 10', (pid,))]
        jobs = [dict(r) for r in self.db.execute("SELECT id,prompt,status,error FROM jobs WHERE session_id=? AND kind='drama' ORDER BY created DESC LIMIT 5", (project['session_id'],))]
        return {'project': {k: project[k] for k in ('id', 'title', 'script', 'script_format', 'style', 'resolution', 'revision', 'session_id', 'created', 'updated')},
                'stage': self.stage(shots), 'characters': characters, 'scenes': scenes, 'shots': shots, 'exports': exports,
                'planning': [{'job_id': j['id'], 'op': json.loads(j['prompt'])['op'], 'status': j['status'], 'error': j['error']} for j in jobs],
                'estimate': self.estimate(project, shots)}

    def shots(self, pid):
        rows = [dict(r) for r in self.db.execute('SELECT * FROM drama_shots WHERE project_id=? ORDER BY ordinal', (pid,))]
        takes = {}
        for take in self.db.execute('''SELECT t.*, j.status AS job_status, j.result, j.error FROM drama_takes t
                LEFT JOIN jobs j ON j.id=t.job_id WHERE t.project_id=? ORDER BY t.created''', (pid,)):
            takes.setdefault(take['shot_id'], []).append(self.take_view(dict(take)))
        for index, shot in enumerate(rows):
            shot['index'] = index + 1
            shot['character_ids'] = json.loads(shot['character_ids'])
            shot['dialogue'] = json.loads(shot['dialogue'])
            shot['takes'] = takes.get(shot['id'], [])
            shot['status'], shot['last_error'] = self.status(shot)
            shot['prompt'] = shot['prompt_override'] or None
        return rows

    def take_view(self, take):
        status = 'waiting' if not take['job_id'] else take['job_status']
        return {'id': take['id'], 'kind': take['kind'], 'status': status, 'job_id': take['job_id'],
                'has_output': bool(take.get('result')) and status == 'succeeded', 'error': take.get('error'),
                'params': json.loads(take['params']), 'created': take['created']}

    @staticmethod
    def status(shot):
        takes = shot['takes']
        final = [t for t in takes if t['kind'] == 'final']
        draft = [t for t in takes if t['kind'] == 'draft']
        failed = next((t['error'] or '生成失败' for t in reversed(takes) if t['status'] in ('failed', 'cancelled')), None)
        if final and final[-1]['status'] in ACTIVE:
            return 'finalizing', None
        if shot['final_take_id'] and any(t['id'] == shot['final_take_id'] and t['has_output'] for t in final):
            return 'done', None
        if shot['approved']:
            return 'approved', failed if final and final[-1]['status'] in ('failed', 'cancelled') else None
        if draft and draft[-1]['status'] in ACTIVE:
            return 'drafting', None
        if shot['draft_take_id'] and any(t['id'] == shot['draft_take_id'] and t['has_output'] for t in draft):
            return 'draft_review', None
        return 'planned', failed if takes and takes[-1]['status'] in ('failed', 'cancelled') else None

    @staticmethod
    def stage(shots):
        if not shots:
            return 'setup'
        states = {s['status'] for s in shots}
        if states == {'planned'}:
            return 'storyboard'
        if states == {'done'}:
            return 'ready'
        return 'producing'

    def estimate(self, project, shots):
        per_clip = self.store.video_projects.estimate(15, project['resolution'] == 'hd')['seconds_per_clip']
        remaining = [s for s in shots if s['status'] not in ('done',)]
        final = sum(per_clip * s['duration'] / 15 for s in remaining)
        draft_ratio = (DRAFT['width'] * DRAFT['height']) / (352 * 608) * DRAFT['steps'] / 8
        queued = self.db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running') AND kind IN ('image','video')").fetchone()[0]
        return {'seconds_per_15s_final': per_clip, 'final_seconds_remaining': round(final),
                'draft_seconds_remaining': round(sum(per_clip * draft_ratio * s['duration'] / 15 for s in remaining)),
                'draft_basis': 'extrapolated', 'jobs_ahead': queued}

    # -- edits ----------------------------------------------------------
    def update_project(self, account_id, pid, revision, fields):
        self.project(account_id, pid)
        allowed = {k: v for k, v in fields.items() if k in ('title', 'style', 'resolution')}
        if 'resolution' in allowed and allowed['resolution'] not in ('sd', 'hd'):
            raise ValueError('分辨率须为 sd 或 hd。')
        if 'title' in allowed and not 1 <= len(str(allowed['title']).strip()) <= 100:
            raise ValueError('标题须为 1–100 字。')
        if 'style' in allowed and len(str(allowed['style'])) > 1000:
            raise ValueError('风格描述最多 1000 字。')
        self.touch(pid, revision)
        with self.db:
            for key, value in allowed.items():
                self.db.execute(f'UPDATE drama_projects SET {key}=? WHERE id=?', (str(value).strip(), pid))
            if 'style' in allowed:
                self.reset_visuals(pid)
        return self.view(account_id, pid)

    def update_character(self, account_id, pid, revision, cid, fields):
        self.project(account_id, pid)
        values = {k: str(fields[k]).strip() for k in ('name', 'description', 'appearance', 'voice') if k in fields and fields[k] is not None}
        if any(len(v) > 1500 for v in values.values()) or ('name' in values and not values['name']):
            raise ValueError('角色字段过长或名字为空。')
        self.touch(pid, revision)
        with self.db:
            if cid == 'new':
                self.db.execute('INSERT INTO drama_characters(id,project_id,name,description,appearance,voice) VALUES (?,?,?,?,?,?)',
                                (new_id(), pid, values.get('name') or '新角色', values.get('description', ''), values.get('appearance', ''), values.get('voice')))
            else:
                for key, value in values.items():
                    self.db.execute(f'UPDATE drama_characters SET {key}=? WHERE id=? AND project_id=?', (value, cid, pid))
                if 'appearance' in values:
                    self.reset_visuals(pid, character_id=cid)
        return self.view(account_id, pid)

    def update_scene(self, account_id, pid, revision, sid, fields):
        self.project(account_id, pid)
        values = {k: str(fields[k]).strip() for k in ('heading', 'location', 'time_of_day', 'setting') if k in fields}
        if any(len(v) > 1500 for v in values.values()):
            raise ValueError('场景字段过长。')
        self.touch(pid, revision)
        with self.db:
            for key, value in values.items():
                self.db.execute(f'UPDATE drama_scenes SET {key}=? WHERE id=? AND project_id=?', (value, sid, pid))
            if 'setting' in values:
                self.reset_visuals(pid, scene_id=sid)
        return self.view(account_id, pid)

    def update_shot(self, account_id, pid, revision, shid, fields):
        self.project(account_id, pid)
        shot = self.db.execute('SELECT * FROM drama_shots WHERE id=? AND project_id=?', (shid, pid)).fetchone()
        if not shot:
            raise ValueError('未找到这个镜头。')
        values = {}
        for key in ('action', 'visual', 'camera', 'prompt_override'):
            if key in fields:
                value = fields[key]
                if value is not None and (not isinstance(value, str) or len(value) > 3000):
                    raise ValueError(f'{key} 须为 3000 字以内的文字。')
                values[key] = value.strip() if isinstance(value, str) and value.strip() else (None if key == 'prompt_override' else '')
        if 'action' in fields and 'visual' not in fields:
            values['visual'] = ''  # the English description no longer matches the edited action
        if 'duration' in fields:
            if not isinstance(fields['duration'], int) or isinstance(fields['duration'], bool) or not 4 <= fields['duration'] <= 15:
                raise ValueError('镜头时长须为 4–15 秒。')
            values['duration'] = fields['duration']
        if 'character_ids' in fields:
            known = {r[0] for r in self.db.execute('SELECT id FROM drama_characters WHERE project_id=?', (pid,))}
            if not isinstance(fields['character_ids'], list) or not set(fields['character_ids']) <= known:
                raise ValueError('出场角色无效。')
            values['character_ids'] = json.dumps(fields['character_ids'])
        if 'dialogue' in fields:
            values['dialogue'] = json.dumps(self.check_dialogue(fields['dialogue']), ensure_ascii=False)
        if 'ordinal' in fields:
            if not isinstance(fields['ordinal'], (int, float)) or isinstance(fields['ordinal'], bool):
                raise ValueError('ordinal 须为数字。')
            values['ordinal'] = float(fields['ordinal'])
        if 'approved' in fields:
            if fields['approved'] and not shot['draft_take_id'] and fields.get('skip_draft') is not True:
                raise ValueError('这个镜头还没有选定的草稿；要跳过草稿直接正式渲染，请明确确认。')
            values['approved'] = int(bool(fields['approved']))
        self.touch(pid, revision)
        # Dialogue is spoken in the generated audio, so it changes the take too.
        visual_change = any(k in values and values[k] != shot[k] for k in
                            ('action', 'visual', 'camera', 'character_ids', 'duration', 'prompt_override', 'dialogue'))
        with self.db:
            for key, value in values.items():
                self.db.execute(f'UPDATE drama_shots SET {key}=?, revision=revision+1 WHERE id=?', (value, shid))
            if visual_change:
                # Old takes stay as history; the shot itself goes back to planned.
                self.db.execute('UPDATE drama_shots SET approved=0, draft_take_id=NULL, final_take_id=NULL WHERE id=?', (shid,))
        return self.view(account_id, pid)

    def add_shot(self, account_id, pid, revision, scene_id, after=None, action='新镜头'):
        self.project(account_id, pid)
        if not self.db.execute('SELECT 1 FROM drama_scenes WHERE id=? AND project_id=?', (scene_id, pid)).fetchone():
            raise ValueError('场次无效。')
        if after:
            row = self.db.execute('SELECT ordinal FROM drama_shots WHERE id=? AND project_id=?', (after, pid)).fetchone()
            nxt = self.db.execute('SELECT min(ordinal) FROM drama_shots WHERE project_id=? AND ordinal>?', (pid, row['ordinal'])).fetchone()[0]
            ordinal = (row['ordinal'] + (nxt if nxt is not None else row['ordinal'] + 2)) / 2
        else:
            ordinal = (self.db.execute('SELECT max(ordinal) FROM drama_shots WHERE project_id=?', (pid,)).fetchone()[0] or 0) + 1
        self.touch(pid, revision)
        with self.db:
            self.db.execute('INSERT INTO drama_shots(id,project_id,scene_id,ordinal,duration,action) VALUES (?,?,?,?,?,?)',
                            (new_id(), pid, scene_id, ordinal, 6, action))
        return self.view(account_id, pid)

    def delete_shot(self, account_id, pid, revision, shid):
        self.project(account_id, pid)
        self.touch(pid, revision)
        with self.db:
            for row in self.db.execute("SELECT j.id FROM drama_takes t JOIN jobs j ON j.id=t.job_id WHERE t.shot_id=? AND j.status='queued'", (shid,)).fetchall():
                self.store.update(row['id'], status='cancelled', outcome='cancelled', delivered=1, delivery_state='suppressed')
            self.db.execute('DELETE FROM drama_takes WHERE shot_id=? AND job_id IS NULL', (shid,))
            self.db.execute('DELETE FROM drama_shots WHERE id=? AND project_id=?', (shid, pid))
        return self.view(account_id, pid)

    @staticmethod
    def check_dialogue(items):
        if not isinstance(items, list) or len(items) > 12:
            raise ValueError('台词须为列表，最多 12 句。')
        result = []
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get('line'), str) or not item['line'].strip() or len(item['line']) > 300:
                raise ValueError('每句台词需要 line（300 字以内）。')
            result.append({'name': str(item.get('name') or '')[:30], 'line': item['line'].strip()})
        return result

    def reset_visuals(self, pid, *, scene_id=None, character_id=None):
        """Changing a setting or a look makes affected shots planned again; their takes remain."""
        query = 'UPDATE drama_shots SET approved=0, draft_take_id=NULL, final_take_id=NULL WHERE project_id=?'
        args = [pid]
        if scene_id:
            query += ' AND scene_id=?'
            args.append(scene_id)
        if character_id:
            query += ' AND character_ids LIKE ?'
            args.append(f'%"{character_id}"%')
        self.db.execute(query, args)

    # -- prompt ---------------------------------------------------------
    def prompt(self, pid, shot):
        if shot.get('prompt_override'):
            return shot['prompt_override']
        project = dict(self.db.execute('SELECT * FROM drama_projects WHERE id=?', (pid,)).fetchone())
        scene = dict(self.db.execute('SELECT * FROM drama_scenes WHERE id=?', (shot['scene_id'],)).fetchone())
        ids = shot['character_ids'] if isinstance(shot['character_ids'], list) else json.loads(shot['character_ids'])
        characters = {r['id']: dict(r) for r in self.db.execute('SELECT * FROM drama_characters WHERE project_id=?', (pid,))}
        dialogue = shot['dialogue'] if isinstance(shot['dialogue'], list) else json.loads(shot['dialogue'])
        parts = []
        if project['style']:
            parts.append(project['style'].rstrip('.') + '.')
        if scene['setting']:
            parts.append(scene['setting'].rstrip('.') + '.')
        for cid in ids:
            character = characters.get(cid)
            if character and character['appearance']:
                parts.append(f"{character['name']}: {character['appearance'].rstrip('.')}.")
        if shot['camera']:
            parts.append('Camera: ' + shot['camera'].rstrip('.') + '.')
        parts.append((shot['visual'] or shot['action']).strip())
        names = {c['name'] for c in characters.values()}
        for line in dialogue:
            speaker = line['name'] if line['name'] in names else (line['name'] or 'A voice')
            parts.append(f'{speaker} says: "{line["line"]}"')
        parts.append('Single continuous shot. No on-screen text, no subtitles, no background music.')
        return ' '.join(p for p in parts if p)

    # -- planning jobs ----------------------------------------------------
    def request_plan(self, account_id, pid, op, scene_ids=None):
        project = self.project(account_id, pid)
        if op not in ('analyze', 'storyboard'):
            raise ValueError('未知操作。')
        if scene_ids is not None:
            known = {r[0] for r in self.db.execute('SELECT id FROM drama_scenes WHERE project_id=?', (pid,))}
            if not isinstance(scene_ids, list) or not scene_ids or not set(scene_ids) <= known:
                raise ValueError('场次无效。')
        active = self.db.execute("SELECT id FROM jobs WHERE session_id=? AND kind='drama' AND status IN ('queued','running')",
                                 (project['session_id'],)).fetchone()
        if active:
            raise ValueError('这个项目正在规划中，请等当前步骤完成。')
        uid = self.store.account(account_id)['user_id']
        payload = json.dumps({'op': op, 'project': pid, 'scene_ids': scene_ids}, ensure_ascii=False)
        jid = self.store.enqueue(uid, None, 'drama', payload, session_id=project['session_id'], channel='app', _unlimited=True)
        return {'job_id': jid, 'status': 'queued'}

    async def execute(self, job, engine):
        from . import planner
        data = json.loads(job['prompt'])
        project = dict(self.db.execute('SELECT * FROM drama_projects WHERE id=?', (data['project'],)).fetchone())
        if project['session_id'] != job['session_id']:
            raise PermissionError('项目归属无效。')
        if data['op'] == 'analyze':
            result = await planner.analyze(self, project, engine)
        elif data['op'] == 'storyboard':
            result = await planner.storyboard(self, project, engine, data.get('scene_ids'))
        elif data['op'] == 'export':
            result = await self.export(project, data['export'])
        else:
            raise ValueError('未知操作。')
        self.touch(project['id'])
        return result

    # -- takes ------------------------------------------------------------
    def submit(self, account_id, pid, kind, shot_ids, confirm=False):
        project = self.project(account_id, pid)
        if kind not in ('draft', 'final'):
            raise ValueError('kind 须为 draft 或 final。')
        shots = self.shots(pid)
        if shot_ids == 'all_pending':
            if kind == 'draft':
                chosen = [s for s in shots if s['status'] == 'planned']
            else:
                chosen = [s for s in shots if s['status'] == 'approved']
        else:
            wanted = set(shot_ids or [])
            chosen = [s for s in shots if s['id'] in wanted]
            if len(chosen) != len(wanted):
                raise ValueError('有镜头不属于这个项目。')
        busy = [s['index'] for s in chosen if s['status'] in ('drafting', 'finalizing')]
        if busy:
            raise ValueError(f"第 {'、'.join(map(str, busy))} 镜正在生成中。")
        if kind == 'final':
            unapproved = [s['index'] for s in chosen if not s['approved']]
            if unapproved:
                raise ValueError(f"第 {'、'.join(map(str, unapproved))} 镜还没有通过草稿审片；先审片，或在镜头上选择“跳过草稿直接正式”。")
        if not chosen:
            raise ValueError('没有需要生成的镜头。')
        estimate = self.estimate(project, chosen)
        seconds = estimate['final_seconds_remaining' if kind == 'final' else 'draft_seconds_remaining']
        summary = {'kind': kind, 'shots': [s['index'] for s in chosen], 'estimated_seconds': seconds,
                   'jobs_ahead': estimate['jobs_ahead'], 'basis': 'measured' if kind == 'final' else 'extrapolated'}
        if not confirm:
            return {'confirm_required': True, **summary}
        now = time.time()
        with self.db:
            for shot in chosen:
                params = {'duration': shot['duration'], 'hd': project['resolution'] == 'hd' and kind == 'final'}
                if kind == 'final' and shot['draft_take_id']:
                    draft = self.db.execute('SELECT params FROM drama_takes WHERE id=?', (shot['draft_take_id'],)).fetchone()
                    params['seed'] = json.loads(draft['params']).get('seed')
                self.db.execute('INSERT INTO drama_takes VALUES (?,?,?,?,?,?,?,?)',
                                (new_id(), pid, shot['id'], kind, self.prompt(pid, shot), json.dumps(params), None, now))
                now += 0.001
        self.pump(pid)
        self.touch(pid)
        return {'submitted': True, **summary}

    def pump(self, pid):
        """Keep at most WINDOW of this project's takes in the global job queue."""
        project = dict(self.db.execute('SELECT * FROM drama_projects WHERE id=?', (pid,)).fetchone())
        active = self.db.execute("""SELECT count(*) FROM drama_takes t JOIN jobs j ON j.id=t.job_id
            WHERE t.project_id=? AND j.status IN ('queued','running','waiting_approval')""", (pid,)).fetchone()[0]
        waiting = self.db.execute('SELECT * FROM drama_takes WHERE project_id=? AND job_id IS NULL ORDER BY created LIMIT ?',
                                  (pid, max(0, WINDOW - active))).fetchall()
        uid = self.store.account(project['account_id'])['user_id']
        for take in waiting:
            params = json.loads(take['params'])
            jid = self.store.enqueue(uid, None, 'video', take['prompt'], hd=params['hd'], session_id=project['session_id'],
                                     channel='app', duration_seconds=params['duration'], request_id='drama-take:' + take['id'],
                                     _unlimited=True)
            with self.db:
                row = self.db.execute('SELECT snapshot FROM media_prompts WHERE job_id=?', (jid,)).fetchone()
                snapshot = json.loads(row[0])
                graph = copy.deepcopy(snapshot['workflow'])
                seed = params.get('seed') or snapshot['seed']
                graph['noise']['inputs']['noise_seed'] = seed
                if take['kind'] == 'draft':
                    graph['text']['inputs'].update(width=DRAFT['width'], height=DRAFT['height'])
                    graph['scheduler']['inputs']['steps'] = DRAFT['steps']
                # The storyboard already wrote the prompt: render it as written.
                snapshot.update(workflow=graph, seed=seed, status='planned', rewritten_prompt=take['prompt'],
                                prepared_at=time.time(), drama_take=take['id'])
                self.db.execute('UPDATE media_prompts SET snapshot=? WHERE job_id=?', (json.dumps(snapshot, ensure_ascii=False), jid))
                params['seed'] = seed
                self.db.execute('UPDATE drama_takes SET job_id=?, params=? WHERE id=?', (jid, json.dumps(params), take['id']))

    def settle(self, job):
        """Called when a take's job ends: select it if it is the shot's newest, refill the window."""
        take = self.take_for_job(job['id'])
        if not take:
            return
        shot = self.db.execute('SELECT * FROM drama_shots WHERE id=?', (take['shot_id'],)).fetchone()
        current = self.store.get(job['id'])
        if shot and current['status'] == 'succeeded':
            column = 'draft_take_id' if take['kind'] == 'draft' else 'final_take_id'
            newest = self.db.execute('SELECT id FROM drama_takes WHERE shot_id=? AND kind=? ORDER BY created DESC LIMIT 1',
                                     (take['shot_id'], take['kind'])).fetchone()
            if newest and newest['id'] == take['id']:
                with self.db:
                    self.db.execute(f'UPDATE drama_shots SET {column}=? WHERE id=?', (take['id'], take['shot_id']))
        self.pump(take['project_id'])
        project = self.touch(take['project_id'])
        remaining = self.db.execute("""SELECT count(*) FROM drama_takes t LEFT JOIN jobs j ON j.id=t.job_id
            WHERE t.project_id=? AND (t.job_id IS NULL OR j.status IN ('queued','running','waiting_approval'))""",
                                    (take['project_id'],)).fetchone()[0]
        if not remaining:
            shots = self.shots(take['project_id'])
            failed = sum(1 for s in shots if s['last_error'])
            with self.db:
                self.store.session_event(project['account_id'], project['origin_session_id'] or project['session_id'], 'drama_batch_done',
                                         {'project_id': project['id'], 'title': project['title'], 'failed': failed})

    def select_take(self, account_id, pid, revision, shid, take_id):
        self.project(account_id, pid)
        take = self.db.execute('SELECT * FROM drama_takes WHERE id=? AND shot_id=? AND project_id=?', (take_id, shid, pid)).fetchone()
        if not take or not take['job_id'] or self.store.get(take['job_id'])['status'] != 'succeeded':
            raise ValueError('只能选择已生成完成的版本。')
        self.touch(pid, revision)
        column = 'draft_take_id' if take['kind'] == 'draft' else 'final_take_id'
        with self.db:
            self.db.execute(f'UPDATE drama_shots SET {column}=? WHERE id=?', (take_id, shid))
        return self.view(account_id, pid)

    def take_file(self, account_id, pid, take_id):
        self.project(account_id, pid)
        row = self.db.execute('''SELECT j.result FROM drama_takes t JOIN jobs j ON j.id=t.job_id
            WHERE t.id=? AND t.project_id=? AND j.status='succeeded' ''', (take_id, pid)).fetchone()
        if not row or not row['result']:
            raise FileNotFoundError(take_id)
        return row['result']

    def cancel(self, account_id, pid):
        project = self.project(account_id, pid)
        with self.db:
            self.db.execute('DELETE FROM drama_takes WHERE project_id=? AND job_id IS NULL', (pid,))
        ids = [r[0] for r in self.db.execute("SELECT id FROM jobs WHERE session_id=? AND status IN ('queued','running','waiting_approval')",
                                             (project['session_id'],))]
        self.touch(pid)
        return ids

    # -- export -----------------------------------------------------------
    def request_export(self, account_id, pid, kind, options=None):
        project = self.project(account_id, pid)
        if kind not in ('draft', 'final'):
            raise ValueError('kind 须为 draft 或 final。')
        shots = self.shots(pid)
        column = 'final_take_id' if kind == 'final' else 'draft_take_id'
        missing = [s['index'] for s in shots if not (s[column] or (kind == 'draft' and s['final_take_id']))]
        if not shots or missing:
            raise ValueError(f"第 {'、'.join(map(str, missing))} 镜还没有可用的{'正式' if kind == 'final' else ''}版本。" if missing else '还没有镜头。')
        chosen = {s['id']: (s[column] or s['final_take_id']) for s in shots}
        eid = new_id()
        uid = self.store.account(account_id)['user_id']
        with self.db:
            self.db.execute('INSERT INTO drama_exports VALUES (?,?,?,?,?,?,?)',
                            (eid, pid, kind, json.dumps(options or {}), json.dumps(chosen), None, time.time()))
        payload = json.dumps({'op': 'export', 'project': pid, 'export': eid})
        jid = self.store.enqueue(uid, None, 'drama', payload, session_id=project['session_id'], channel='app', _unlimited=True)
        with self.db:
            self.db.execute('UPDATE drama_exports SET job_id=? WHERE id=?', (jid, eid))
        return {'export_id': eid, 'job_id': jid}

    async def export(self, project, eid):
        from ..video_export import assemble_project
        export = dict(self.db.execute('SELECT * FROM drama_exports WHERE id=? AND project_id=?', (eid, project['id'])).fetchone())
        chosen = json.loads(export['shot_takes'])
        rows = []
        for index, shot in enumerate(self.db.execute('SELECT * FROM drama_shots WHERE project_id=? ORDER BY ordinal', (project['id'],))):
            take = self.db.execute('''SELECT t.params, j.result FROM drama_takes t JOIN jobs j ON j.id=t.job_id
                WHERE t.id=? AND j.status='succeeded' ''', (chosen.get(shot['id']),)).fetchone()
            if not take:
                raise RuntimeError(f'第 {index + 1} 镜的版本已不可用，请重新导出。')
            rows.append({'ordinal': index, 'duration': shot['duration'], 'result': take['result']})
        directory = self.store.workspace_directory(project['account_id'], project['session_id']) / 'exports' / eid
        directory.mkdir(parents=True, exist_ok=True)
        return str(await assemble_project(rows, directory))

    def export_view(self, export):
        job = self.store.get(export['job_id']) if export['job_id'] else None
        return {'id': export['id'], 'kind': export['kind'], 'status': job['status'] if job else 'waiting',
                'error': job.get('error') if job else None, 'created': export['created'],
                'ready': bool(job and job['status'] == 'succeeded' and job.get('result'))}

    def export_file(self, account_id, pid, eid):
        self.project(account_id, pid)
        row = self.db.execute('''SELECT j.result FROM drama_exports e JOIN jobs j ON j.id=e.job_id
            WHERE e.id=? AND e.project_id=? AND j.status='succeeded' ''', (eid, pid)).fetchone()
        if not row or not row['result']:
            raise FileNotFoundError(eid)
        return row['result']



# -- agent tools ------------------------------------------------------------
from ..capabilities import Capability, string  # noqa: E402
from ..agent.transcript import Transcript  # noqa: E402

DRAMA_TOOLS = (
    Capability('drama_import', '把剧本导入为短剧项目（只建项目和场次，不渲染）。用户在对话里发过的剧本用 source_message_id 引用，'
               '上传的文件用 attachment_id，只有很短的正文才直接写 script；三者选一个。返回项目编号和场次、角色。',
               {'title': string(100, minLength=1), 'script': string(30000), 'attachment_id': string(80),
                'source_message_id': {'type': 'integer', 'minimum': 1},
                'resolution': string(2, enum=['sd', 'hd'])}, ('title',), effect='local_write', group='drama'),
    Capability('drama_list', '列出用户的短剧项目。', {}, (), group='drama'),
    Capability('drama_get', '查看一个短剧项目：设定、场次、每个镜头的编号/动作/时长/状态。', {'project_id': string(20, minLength=1)},
               ('project_id',), group='drama'),
    Capability('drama_plan', '让本机模型规划：op=analyze 补全角色外观和场景设定；op=storyboard 生成分镜（可只重做部分 scene_ids）。在后台运行，几分钟后用 drama_get 查看。',
               {'project_id': string(20, minLength=1), 'op': string(12, enum=['analyze', 'storyboard']),
                'scene_ids': {'type': 'array', 'maxItems': 50, 'items': string(20)}}, ('project_id', 'op'), effect='state', group='drama'),
    Capability('drama_edit_shot', '修改一个镜头：action（中文动作）、visual（英文画面）、camera、duration（4–15）、dialogue、approved（草稿通过）。改动画面会让镜头回到“已规划”。',
               {'project_id': string(20, minLength=1), 'shot_id': string(20, minLength=1), 'action': string(300), 'visual': string(1200),
                'camera': string(120), 'duration': {'type': 'integer', 'minimum': 4, 'maximum': 15}, 'approved': {'type': 'boolean'},
                'dialogue': {'type': 'array', 'maxItems': 12, 'items': {'type': 'object', 'additionalProperties': False, 'required': ['line'],
                             'properties': {'name': string(30), 'line': string(300, minLength=1)}}}},
               ('project_id', 'shot_id'), effect='state', group='drama'),
    Capability('drama_submit', '提交渲染：kind=draft（低成本草稿）或 final（正式，只限草稿已通过的镜头）。shot_ids 为镜头 id 列表；'
               'all_pending=true 表示所有待处理镜头（草稿：已规划的；正式：已通过的）。'
               '先不带 confirm 调用拿到预计耗时，告诉用户并得到同意后，再带 confirm=true 提交。',
               {'project_id': string(20, minLength=1), 'kind': string(5, enum=['draft', 'final']),
                'shot_ids': {'type': 'array', 'maxItems': 200, 'items': string(20)}, 'all_pending': {'type': 'boolean'},
                'confirm': {'type': 'boolean'}}, ('project_id', 'kind'), effect='submit', replay='reconcile', group='drama'),
    Capability('drama_export', '把所有镜头按顺序合成为一部片子：kind=draft 用草稿（已有正式版的用正式版），final 只用正式版。', {'project_id': string(20, minLength=1),
               'kind': string(5, enum=['draft', 'final'])}, ('project_id', 'kind'), effect='submit', replay='reconcile', group='drama'),
)


def compact(view):
    names = {c['id']: c['name'] for c in view['characters']}
    return {'project': {k: view['project'][k] for k in ('id', 'title', 'style', 'resolution')}, 'stage': view['stage'],
            'characters': [{'id': c['id'], 'name': c['name'], 'appearance': c['appearance'][:200]} for c in view['characters']],
            'scenes': [{'id': s['id'], 'heading': s['heading'], 'has_setting': bool(s['setting'])} for s in view['scenes']],
            'shots': [{'index': s['index'], 'id': s['id'], 'scene_id': s['scene_id'], 'action': s['action'], 'duration': s['duration'],
                       'characters': [names.get(i, '?') for i in s['character_ids']], 'status': s['status'], 'error': s['last_error']}
                      for s in view['shots'][:120]],
            'planning': view['planning'][:2], 'exports': view['exports'][:3], 'estimate': view['estimate'],
            'page': f"/app/dramas/{view['project']['id']}"}


def register(dramas, registry, job, attachments):
    account = job['account_id']

    async def handler(name, args):
        if name == 'drama_list':
            return {'projects': dramas.list(account)}
        if name == 'drama_import':
            sources = [k for k in ('script', 'attachment_id', 'source_message_id') if args.get(k)]
            if len(sources) > 1:
                raise ValueError('script、attachment_id、source_message_id 只能选一个。')
            script = args.get('script')
            if args.get('source_message_id'):
                script = Transcript(dramas.db).user_message(account, job['session_id'], args['source_message_id'])
            elif args.get('attachment_id'):
                chunks, offset = [], 0
                while True:
                    part = await attachments.read(args['attachment_id'], offset)
                    chunks.append(part.get('text', ''))
                    offset = part.get('next_offset')
                    if not offset or sum(map(len, chunks)) > 30000:
                        break
                script = ''.join(chunks)
            if not script:
                raise ValueError('需要 source_message_id、attachment_id 或 script 正文。')
            view = dramas.create(account, args['title'], script, origin_session_id=job['session_id'], resolution=args.get('resolution', 'sd'))
            return {**compact(view), 'next': '可以先 drama_plan(op=analyze) 补全设定，再 drama_plan(op=storyboard) 出分镜。'}
        project_id = args['project_id']
        if name == 'drama_get':
            return compact(dramas.view(account, project_id))
        if name == 'drama_plan':
            return dramas.request_plan(account, project_id, args['op'], args.get('scene_ids'))
        if name == 'drama_edit_shot':
            view = dramas.view(account, project_id)
            fields = {k: args[k] for k in ('action', 'visual', 'camera', 'duration', 'dialogue', 'approved') if k in args}
            if fields.get('approved'):
                fields['skip_draft'] = True
            view = dramas.update_shot(account, project_id, view['project']['revision'], args['shot_id'], fields)
            return next(s for s in compact(view)['shots'] if s['id'] == args['shot_id'])
        if name == 'drama_submit':
            shots = 'all_pending' if args.get('all_pending') else args.get('shot_ids')
            if not shots:
                raise ValueError('需要 shot_ids 或 all_pending=true。')
            return dramas.submit(account, project_id, args['kind'], shots, bool(args.get('confirm')))
        if name == 'drama_export':
            return dramas.request_export(account, project_id, args['kind'])
        raise ValueError('未知工具。')

    for spec in DRAMA_TOOLS:
        registry.register(spec, handler)
