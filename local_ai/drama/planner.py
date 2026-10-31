"""Model-assisted planning: fill character/scene settings, then storyboard scene by scene.

Each call carries one bounded piece of the script so a 30,000-character script
never has to fit into one 32K context. The model writes descriptions; the code
assembles the final render prompt, so a character's look is written once and
reused verbatim in every shot.
"""
from __future__ import annotations

import json

from ..capabilities import validate
from ..engine import OutputTruncated
from .parser import estimate_duration
from .service import new_id

TEXT = {'type': 'string', 'maxLength': 1500}

ANALYZE_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['characters', 'scenes'],
    'properties': {
        'style': TEXT,
        'characters': {'type': 'array', 'maxItems': 30, 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['name', 'description', 'appearance'],
            'properties': {'name': {'type': 'string', 'maxLength': 30}, 'description': TEXT, 'appearance': TEXT}}},
        'scenes': {'type': 'array', 'maxItems': 40, 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['index', 'setting'],
            'properties': {'index': {'type': 'integer', 'minimum': 0}, 'setting': TEXT,
                           'location': {'type': 'string', 'maxLength': 60}, 'time_of_day': {'type': 'string', 'maxLength': 20}}}},
    },
}

SHOTS_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['shots'],
    'properties': {'shots': {'type': 'array', 'minItems': 1, 'maxItems': 12, 'items': {
        'type': 'object', 'additionalProperties': False, 'required': ['action', 'visual', 'camera', 'characters', 'dialogue'],
        'properties': {
            'action': {'type': 'string', 'maxLength': 300}, 'visual': {'type': 'string', 'maxLength': 1200},
            'camera': {'type': 'string', 'maxLength': 120},
            'characters': {'type': 'array', 'maxItems': 6, 'items': {'type': 'string', 'maxLength': 30}},
            'dialogue': {'type': 'array', 'maxItems': 6, 'items': {
                'type': 'object', 'additionalProperties': False, 'required': ['name', 'line'],
                'properties': {'name': {'type': 'string', 'maxLength': 30}, 'line': {'type': 'string', 'maxLength': 300}}}},
            'duration': {'type': 'integer', 'minimum': 4, 'maximum': 15}}}}},
}


async def ask(engine, system, payload, schema, max_tokens):
    """One structured answer. A cut-off or invalid reply is retried once with twice the room."""
    messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}]
    for attempt, budget in enumerate((max_tokens, max_tokens * 2)):
        try:
            value = json.loads(await engine.chat(messages, schema=schema, max_tokens=budget))
            validate(value, schema)
            return value
        except (OutputTruncated, ValueError) as exc:
            if attempt:
                raise RuntimeError(f'本机模型没能给出完整的结构化结果（{type(exc).__name__}），可以减少一次规划的场次后重试。') from exc


async def analyze(dramas, project, engine):
    """Fill empty character looks, scene settings and an overall style. Never overwrite edits."""
    db = dramas.db
    characters = [dict(r) for r in db.execute('SELECT * FROM drama_characters WHERE project_id=?', (project['id'],))]
    scenes = [dict(r) for r in db.execute('SELECT * FROM drama_scenes WHERE project_id=? ORDER BY ordinal', (project['id'],))]
    system = ('你是短剧美术指导。根据剧本为每个角色写设定，为每个场次写场景描述，并给全剧一个统一的视觉风格。'
              'description 用中文写身份、年龄段、性格和人物关系；appearance 用英文写稳定的外观：年龄段、发型、脸型特征、体型、服装和颜色，'
              '一两句、不写动作和情绪，便于在每个镜头原样复用。setting 用英文写地点、时间、光线和陈设。style 用英文写画面风格、色调、镜头质感。'
              '忠于剧本，不添加剧本里没有的人物和情节。所有角色都是虚构人物，不写真实人物的名字或长相。只返回 JSON。')
    batch_size = 8
    filled = 0
    for start in range(0, max(1, len(scenes)), batch_size):
        part = scenes[start:start + batch_size]
        payload = {'characters': [c['name'] for c in characters],
                   'scenes': [{'index': s['ordinal'], 'heading': s['heading'], 'text': s['text'][:2500]} for s in part],
                   'need_style': not project['style'] and start == 0}
        result = await ask(engine, system, payload, ANALYZE_SCHEMA, 3000)
        with db:
            if payload['need_style'] and result.get('style'):
                db.execute("UPDATE drama_projects SET style=? WHERE id=? AND style=''", (result['style'].strip(), project['id']))
            known = {c['name']: c for c in characters}
            for item in result['characters']:
                name = item['name'].strip()
                if name in known:
                    db.execute("UPDATE drama_characters SET description=CASE WHEN description='' THEN ? ELSE description END,"
                               " appearance=CASE WHEN appearance='' THEN ? ELSE appearance END WHERE id=?",
                               (item['description'].strip(), item['appearance'].strip(), known[name]['id']))
                elif name and len(known) < 30:
                    cid = new_id()
                    db.execute('INSERT INTO drama_characters(id,project_id,name,description,appearance) VALUES (?,?,?,?,?)',
                               (cid, project['id'], name, item['description'].strip(), item['appearance'].strip()))
                    known[name] = {'id': cid, 'name': name}
            characters = list(known.values())
            by_index = {s['ordinal']: s for s in part}
            for item in result['scenes']:
                scene = by_index.get(item['index'])
                if scene and not scene['setting']:
                    db.execute("UPDATE drama_scenes SET setting=?, location=CASE WHEN location='' THEN ? ELSE location END,"
                               " time_of_day=CASE WHEN time_of_day='' THEN ? ELSE time_of_day END WHERE id=?",
                               (item['setting'].strip(), (item.get('location') or '').strip(), (item.get('time_of_day') or '').strip(), scene['id']))
                    filled += 1
    return f'已补全角色与场景设定（{filled} 个场次）。'


async def storyboard(dramas, project, engine, scene_ids=None):
    """(Re)plan shots for the chosen scenes; shots with a hand-written prompt are kept."""
    db = dramas.db
    scenes = [dict(r) for r in db.execute('SELECT * FROM drama_scenes WHERE project_id=? ORDER BY ordinal', (project['id'],))]
    if scene_ids:
        scenes = [s for s in scenes if s['id'] in set(scene_ids)]
    characters = [dict(r) for r in db.execute('SELECT * FROM drama_characters WHERE project_id=?', (project['id'],))]
    by_name = {c['name']: c['id'] for c in characters}
    system = ('你是短剧分镜师。把这一场拆成连续的镜头，每个镜头 4–15 秒，是一次连续拍摄。'
              'action 用中文一句话写这个镜头里发生什么；visual 用英文写画面：谁在哪里、做什么动作、动作带来的可见结果，'
              '不要重复角色外观和场景描述（系统会自动加上）；camera 用英文写景别和运镜，如 "medium shot, slow push-in"。'
              'characters 写出场角色名字（必须用给出的名字）；dialogue 原样保留剧本台词，不改写、不增加；duration 按台词长度和动作估计秒数。'
              '相邻镜头要有剧情推进，不要重复同一个动作。台词超过 15 秒就拆成正反打的多个镜头。只返回 JSON。')
    total = 0
    for scene in scenes:
        previous = db.execute('''SELECT action FROM drama_shots WHERE project_id=? AND ordinal<(
                SELECT COALESCE(min(ordinal), 1e9) FROM drama_shots WHERE scene_id=?) ORDER BY ordinal DESC LIMIT 2''',
                              (project['id'], scene['id'])).fetchall()
        payload = {'scene': {'heading': scene['heading'], 'text': scene['text'][:6000]},
                   'characters': [c['name'] for c in characters],
                   'previous_shots': [r['action'] for r in reversed(previous)]}
        result = await ask(engine, system, payload, SHOTS_SCHEMA, 4000)
        with db:
            kept = [dict(r) for r in db.execute('SELECT * FROM drama_shots WHERE scene_id=? AND prompt_override IS NOT NULL ORDER BY ordinal', (scene['id'],))]
            db.execute('DELETE FROM drama_shots WHERE scene_id=? AND prompt_override IS NULL', (scene['id'],))
            base = scene['ordinal'] * 1000.0
            for index, shot in enumerate(result['shots']):
                dialogue = [{'name': d['name'].strip(), 'line': d['line'].strip()} for d in shot['dialogue'] if d['line'].strip()]
                duration = max(shot.get('duration') or 0, estimate_duration(dialogue, shot['visual']))
                ids = [by_name[n] for n in shot['characters'] if n in by_name]
                db.execute('INSERT INTO drama_shots(id,project_id,scene_id,ordinal,duration,action,visual,camera,character_ids,dialogue) VALUES (?,?,?,?,?,?,?,?,?,?)',
                           (new_id(), project['id'], scene['id'], base + index + 1, min(15, duration), shot['action'].strip(),
                            shot['visual'].strip(), shot['camera'].strip(), json.dumps(ids), json.dumps(dialogue, ensure_ascii=False)))
                total += 1
            # Hand-written shots keep their place at the end of their scene.
            for offset, shot in enumerate(kept):
                db.execute('UPDATE drama_shots SET ordinal=? WHERE id=?', (base + 900 + offset, shot['id']))
    return f'已生成 {total} 个镜头（{len(scenes)} 个场次）。'
