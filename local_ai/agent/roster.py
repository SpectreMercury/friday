"""Agents the user can bring into a conversation, per-message options, and titles.

An "agent" here is a capability Friday gains for one task when the user
invites it (an @ mention in the app). Inviting opens its toolset; Friday never
opens it silently for an app conversation, it asks through the UI instead.
"""
from __future__ import annotations

import json
import re
import time

AGENTS = (
    {'id': 'drama', 'name': '短剧助手', 'toolset': 'drama', 'symbol': 'film',
     'description': '把剧本做成多镜头短剧：设定、分镜、草稿与正式渲染、合成成片'},
)
BY_ID = {a['id']: a for a in AGENTS}
BY_TOOLSET = {a['toolset']: a for a in AGENTS}

DEFAULT_TITLE = '新对话'

# Strong signals only: a wrong suggestion costs the user a tap to dismiss.
_DRAMA = re.compile(r'短剧|微短剧|剧本|分镜|拍成(视频|短片|片子)|做成(视频|短片|片子|短剧)|成片|镜头脚本|'
                    r'第\s*[一二三四五六七八九十0-9]+\s*[场集幕]|[内外]景\s*[·・.]')
_DIALOGUE = re.compile(r'^\s*[一-鿿A-Za-z]{1,8}\s*[：:]\s*\S', re.M)


def init_schema(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS job_options(job_id TEXT PRIMARY KEY, thinking INTEGER, agents TEXT NOT NULL DEFAULT '[]',
            suggested TEXT);
    ''')
    columns = {r[1] for r in db.execute('PRAGMA table_info(sessions)')}
    if 'auto_title' not in columns:
        db.execute('ALTER TABLE sessions ADD COLUMN auto_title INTEGER NOT NULL DEFAULT 0')


def public_agents():
    return [{k: a[k] for k in ('id', 'name', 'description', 'symbol')} for a in AGENTS]


def validate_agents(value):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > len(AGENTS) or any(v not in BY_ID for v in value):
        raise ValueError('未知的助手。')
    return list(dict.fromkeys(value))


def suggest(text, invited=()):
    """The agent that suits this text and is not already invited, or None."""
    if not isinstance(text, str):
        return None
    text = text[:12000]
    if 'drama' not in invited and (_DRAMA.search(text) or len(_DIALOGUE.findall(text)) >= 3):
        agent = BY_ID['drama']
        return {'id': agent['id'], 'name': agent['name'], 'symbol': agent['symbol'],
                'reason': '这看起来是剧本或短剧制作'}
    return None


def save_options(db, job_id, thinking=None, agents=()):
    if thinking is not None and not isinstance(thinking, bool):
        raise ValueError('thinking 须为 true 或 false。')
    with db:
        db.execute('INSERT OR IGNORE INTO job_options(job_id,thinking,agents) VALUES (?,?,?)',
                   (job_id, None if thinking is None else int(thinking), json.dumps(list(agents))))


def options(db, job_id):
    row = db.execute('SELECT * FROM job_options WHERE job_id=?', (job_id,)).fetchone()
    if not row:
        return {'thinking': None, 'agents': [], 'suggested': None}
    return {'thinking': None if row['thinking'] is None else bool(row['thinking']),
            'agents': json.loads(row['agents']), 'suggested': row['suggested']}


def mark_suggested(db, job_id, agent_id):
    with db:
        db.execute('INSERT INTO job_options(job_id,agents,suggested) VALUES (?,?,?) '
                   'ON CONFLICT(job_id) DO UPDATE SET suggested=excluded.suggested', (job_id, '[]', agent_id))


def options_for(db, job_ids):
    if not job_ids:
        return {}
    rows = db.execute(f"SELECT * FROM job_options WHERE job_id IN ({','.join('?' * len(job_ids))})", list(job_ids))
    return {r['job_id']: {'thinking': None if r['thinking'] is None else bool(r['thinking']),
                          'agents': json.loads(r['agents']), 'suggested': r['suggested']} for r in rows}


# -- titles ------------------------------------------------------------------

def provisional_title(prompt):
    line = next((l.strip() for l in (prompt or '').splitlines() if l.strip()), '')
    line = re.sub(r'\s+', ' ', line)
    return (line[:18] + '…') if len(line) > 18 else (line or DEFAULT_TITLE)


def clean_title(text):
    text = re.sub(r'<think>.*?</think>', '', text or '', flags=re.S)
    text = text.strip().splitlines()[0] if text.strip() else ''
    text = re.sub(r'^(标题[:：]\s*)', '', text).strip(' "“”「」《》。.')
    return text[:24]


def set_title(store, account_id, session_id, name, *, final):
    """Rename an auto-titled session unless the user renamed it meanwhile."""
    with store.db:
        changed = store.db.execute('UPDATE sessions SET name=?,auto_title=?,revision=revision+1 WHERE id=? AND account_id=? AND auto_title=1',
                                   (name, 0 if final else 1, session_id, account_id)).rowcount
        if changed:
            store.session_event(account_id, session_id, 'session_updated', {'title': name, 'at': time.time()})
    return bool(changed)
