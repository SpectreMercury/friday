"""A concrete, durable user review is required before any video GPU work."""
import hashlib
import json
import re
import unicodedata


REFERENCE_FIELDS = {
    key: {'type': 'string', 'minLength': 1, 'maxLength': 2000, 'description': description}
    for key, description in (
        ('character_photos', '与用户讨论后的角色照片选择（附件编号及用途）；不用照片须明确写用户选择不用，不能自行决定。'),
        ('environment_photos', '与用户讨论后的环境照片选择（附件编号及用途）；不用照片须明确写用户选择不用，不能自行决定。'),
    )
}

PROGRESSION_FIELDS = ('start_state', 'action', 'end_state')
PROGRESSION_SCHEMA = {
    'type': 'object',
    'properties': {key: {'type': 'string', 'minLength': 1, 'maxLength': 500}
                   for key in PROGRESSION_FIELDS},
    'required': list(PROGRESSION_FIELDS), 'additionalProperties': False,
}


def references(args):
    """Optional notes about reference photos; the model no longer has to invent a 'discussion'."""
    result = {}
    for key in REFERENCE_FIELDS:
        value = args.get(key)
        result[key] = value.strip()[:2000] if isinstance(value, str) and value.strip() else '未指定'
    return result


def readable(document):
    """What the person approves, in sentences. The exact JSON stays in the database for the digest."""
    lines = []
    settings = document.get('render_settings') or {}
    if 'prompt' in document:
        size = f"{settings.get('width')}×{settings.get('height')}" if settings.get('width') else ''
        seconds = document.get('duration_seconds') or ''
        lines.append(f"将生成一段{f' {seconds} 秒' if seconds else ''}视频{f'（{size}）' if size else ''}，在本机排队渲染。")
        lines.append('')
        lines.append('画面描述：')
        lines.append(document['prompt'][:1500] + ('…' if len(document['prompt']) > 1500 else ''))
        lines.append('')
        lines.append('首帧图片：' + ('使用你上传的图片' if document.get('first_frame') else '无'))
    else:
        lines.append(f"短剧「{document.get('title', '')}」，共 {len(document.get('shots') or [])} 个镜头，约 {document.get('target_seconds', '')} 秒。")
        for index, shot in enumerate((document.get('shots') or [])[:40]):
            lines.append(f"{index + 1}. {shot.get('summary') or shot.get('prompt', '')[:80]}")
    for key, label in (('character_photos', '角色参考'), ('environment_photos', '场景参考')):
        value = document.get(key)
        if value and value != '未指定':
            lines.append(f'{label}：{value[:200]}')
    return '\n'.join(lines)


def normalized_scene(text):
    text = unicodedata.normalize('NFKC', text).casefold()
    # Ordinal labels and punctuation do not make a different scene.
    text = re.sub(r'\b(?:shot|scene)\s*#?\s*\d+\b|第?\s*[\d零一二三四五六七八九十百]+\s*(?:镜头|镜|幕|段)', '', text)
    return ''.join(char for char in text if char.isalnum())


def check_distinct(shots):
    seen = {}
    progress = {}
    for shot in shots:
        prompt = normalized_scene(shot['prompt'])
        if not prompt:
            raise RuntimeError('分镜提示词为空，尚未提交渲染。')
        if prompt in seen:
            raise RuntimeError(f'第{seen[prompt]+1}与第{shot["ordinal"]+1}镜头提示词重复。请返回分镜讨论并修订；尚未提交渲染。')
        seen[prompt] = shot['ordinal']
        # Existing approved plans remain readable. New plans require these
        # fields through the planner schema, and bind them to the real prompt.
        if 'progression' not in shot:
            continue
        from .capabilities import validate
        validate(shot['progression'], PROGRESSION_SCHEMA)
        beat = {key: normalized_scene(shot['progression'][key]) for key in PROGRESSION_FIELDS}
        if any(not value or value not in prompt for value in beat.values()):
            raise RuntimeError(f'第{shot["ordinal"]+1}镜头的初始状态、动作或结束状态未落实到实际prompt。请修订后再提交渲染。')
        key = (beat['action'], beat['end_state'])
        if key in progress:
            raise RuntimeError(f'第{progress[key]+1}与第{shot["ordinal"]+1}镜头剧情进展重复；改变机位、描述或编号不能代替新的动作和结果。尚未提交渲染。')
        progress[key] = shot['ordinal']


async def require_review(store, job, approve, document):
    """Only the host approval callback can grant review, never a model boolean."""
    body = json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2)
    digest = hashlib.sha256(body.encode()).hexdigest()
    store.db.execute('''CREATE TABLE IF NOT EXISTS video_reviews (
        job_id TEXT NOT NULL, digest TEXT NOT NULL, document TEXT NOT NULL,
        PRIMARY KEY(job_id,digest))''')
    store.db.commit()
    if store.db.execute('SELECT 1 FROM video_reviews WHERE job_id=? AND digest=?', (job['id'], digest)).fetchone():
        return
    description = readable(document) + '\n\n确认后开始渲染；想修改就拒绝，在聊天里说明要改什么。\n版本 ' + digest[:12]
    if not approve or not await approve(job, description, purpose='video'):
        raise RuntimeError('视频方案尚未获用户确认，未开始渲染。请回到聊天讨论剧本、角色照片和环境照片。')
    with store.db:
        store.db.execute('INSERT OR IGNORE INTO video_reviews VALUES (?,?,?)', (job['id'], digest, body))


async def review_single(store, job, approve, workflow):
    row = store.db.execute('SELECT snapshot FROM media_prompts WHERE job_id=?', (job['id'],)).fetchone()
    snapshot = json.loads(row[0]) if row else {}
    refs = references(snapshot.get('references', {}))
    await require_review(store, job, approve, {
        'script': job['prompt'], **refs, 'prompt': workflow['text']['inputs']['prompt'],
        'render_settings': {key: value for key, value in workflow['text']['inputs'].items()
                            if key in ('width', 'height', 'length')},
        'workflow_sha256': hashlib.sha256(json.dumps(workflow, sort_keys=True).encode()).hexdigest(),
        'first_frame': job.get('image'), 'duration_seconds': job.get('duration_seconds'),
    })
