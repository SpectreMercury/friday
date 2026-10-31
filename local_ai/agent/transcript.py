"""Durable conversation transcript, per-job checkpoints and full-text session search.

The transcript keeps what a person would recognise as the conversation: their
messages, the final replies and a one-line note of the tools used. Full tool
arguments and results stay in `tool_operations`; the in-flight turn (with every
tool call and result) lives in `agent_turns` until the job settles.
"""
from __future__ import annotations

import json
import re
import time

SCHEMA = '''
CREATE TABLE IF NOT EXISTS agent_messages(
    id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT NOT NULL, session_id TEXT NOT NULL,
    job_id TEXT, role TEXT NOT NULL, content TEXT NOT NULL, tools TEXT, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS agent_messages_session ON agent_messages(session_id, id);
CREATE VIRTUAL TABLE IF NOT EXISTS agent_messages_fts USING fts5(
    content, content='agent_messages', content_rowid='id', tokenize='trigram');
CREATE TRIGGER IF NOT EXISTS agent_messages_ai AFTER INSERT ON agent_messages BEGIN
    INSERT INTO agent_messages_fts(rowid, content) VALUES (new.id, new.content); END;
CREATE TRIGGER IF NOT EXISTS agent_messages_ad AFTER DELETE ON agent_messages BEGIN
    INSERT INTO agent_messages_fts(agent_messages_fts, rowid, content) VALUES ('delete', old.id, old.content); END;
CREATE TABLE IF NOT EXISTS agent_summaries(
    session_id TEXT PRIMARY KEY, upto INTEGER NOT NULL, text TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS agent_turns(
    job_id TEXT PRIMARY KEY, messages TEXT NOT NULL, state TEXT NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS agent_toolsets(session_id TEXT PRIMARY KEY, enabled TEXT NOT NULL);
'''


class Transcript:
    def __init__(self, db):
        self.db = db
        db.executescript(SCHEMA)
        # `sent` is a user message exactly as the model saw it (time stamp, attachment
        # note). Replaying it verbatim keeps the prompt an append-only prefix, which
        # the hybrid model's cache needs to skip re-reading the whole conversation.
        if 'sent' not in {r[1] for r in db.execute('PRAGMA table_info(agent_messages)')}:
            db.execute('ALTER TABLE agent_messages ADD COLUMN sent TEXT')
        db.commit()

    # -- conversation -------------------------------------------------
    def append(self, job, role, content, tools=None):
        with self.db:
            return self.db.execute('INSERT INTO agent_messages(account_id,session_id,job_id,role,content,tools,created) VALUES (?,?,?,?,?,?,?)',
                                   (job['account_id'], job['session_id'], job['id'], role, content,
                                    json.dumps(tools, ensure_ascii=False) if tools else None, time.time())).lastrowid

    def set_sent(self, message_id, text):
        with self.db:
            self.db.execute('UPDATE agent_messages SET sent=? WHERE id=?', (text, message_id))

    def toolsets(self, session_id):
        row = self.db.execute('SELECT enabled FROM agent_toolsets WHERE session_id=?', (session_id,)).fetchone()
        return json.loads(row['enabled']) if row else []

    def save_toolsets(self, session_id, enabled):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO agent_toolsets VALUES (?,?)', (session_id, json.dumps(list(enabled))))

    def user_message(self, account_id, session_id, message_id):
        """Full text of one user message in this session, for tools that take long text by reference."""
        if not isinstance(message_id, int) or isinstance(message_id, bool):
            raise ValueError('source_message_id 须为消息编号（整数）。')
        row = self.db.execute("SELECT content FROM agent_messages WHERE id=? AND account_id=? AND session_id=? AND role='user'",
                              (message_id, account_id, session_id)).fetchone()
        if not row:
            raise PermissionError('只能引用当前会话里用户发过的消息。')
        return row['content']

    def recorded(self, job_id):
        return bool(self.db.execute("SELECT 1 FROM agent_messages WHERE job_id=? AND role='assistant'", (job_id,)).fetchone())

    def history(self, account_id, session_id, before_job, limit=40):
        """Earlier turns of this session, oldest first, excluding the current job."""
        # Everything from this job's own first message on (including messages
        # absorbed into it) belongs to the current turn, not to history.
        boundary = self.db.execute('SELECT min(id) FROM agent_messages WHERE job_id=?', (before_job,)).fetchone()[0]
        rows = self.db.execute('''SELECT id,role,content,tools,sent FROM agent_messages
            WHERE account_id=? AND session_id=? AND id<? ORDER BY id DESC LIMIT ?''',
                               (account_id, session_id, boundary or 2**62, limit)).fetchall()
        if rows:
            return [dict(r) for r in reversed(rows)]
        # Sessions from before this transcript existed: rebuild from finished jobs.
        legacy = self.db.execute('''SELECT id,prompt,result FROM jobs WHERE account_id=? AND session_id=? AND kind='chat'
            AND id!=? AND status='succeeded' AND result IS NOT NULL AND id NOT IN (SELECT job_id FROM deleted_jobs)
            AND NOT EXISTS (SELECT 1 FROM agent_messages m WHERE m.job_id=jobs.id) ORDER BY created DESC LIMIT ?''',
                                 (account_id, session_id, before_job, limit // 2)).fetchall()
        items = []
        for row in reversed(legacy):
            items += [{'id': None, 'role': 'user', 'content': row['prompt'], 'tools': None},
                      {'id': None, 'role': 'assistant', 'content': row['result'], 'tools': None}]
        return items

    def summary(self, session_id):
        row = self.db.execute('SELECT upto,text FROM agent_summaries WHERE session_id=?', (session_id,)).fetchone()
        return dict(row) if row else None

    def save_summary(self, session_id, upto, text):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO agent_summaries VALUES (?,?,?,?)', (session_id, upto, text, time.time()))

    # -- checkpoints ----------------------------------------------------
    def checkpoint(self, job_id):
        row = self.db.execute('SELECT messages,state FROM agent_turns WHERE job_id=?', (job_id,)).fetchone()
        return (json.loads(row['messages']), json.loads(row['state'])) if row else (None, None)

    def save_checkpoint(self, job_id, messages, state):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO agent_turns VALUES (?,?,?,?)',
                            (job_id, json.dumps(messages, ensure_ascii=False), json.dumps(state, ensure_ascii=False), time.time()))

    def drop_checkpoint(self, job_id):
        with self.db:
            self.db.execute('DELETE FROM agent_turns WHERE job_id=?', (job_id,))

    # -- search -------------------------------------------------------
    def search(self, account_id, query, limit=8):
        terms = [t for t in re.split(r'[\s,，。.、;；:：!！?？"“”\'()（）]+', query or '') if t]
        if not terms:
            raise ValueError('请提供要查找的关键词。')
        hits, seen = [], set()
        long_terms = [t for t in terms if len(t) >= 3]
        if long_terms:
            fts = ' OR '.join('"' + t.replace('"', '""') + '"' for t in long_terms)
            rows = self.db.execute('''SELECT m.id,m.session_id,m.role,m.content,m.created,s.name AS session
                FROM agent_messages_fts f JOIN agent_messages m ON m.id=f.rowid LEFT JOIN sessions s ON s.id=m.session_id
                WHERE agent_messages_fts MATCH ? AND m.account_id=? AND s.deleted IS NULL ORDER BY bm25(agent_messages_fts) LIMIT ?''',
                                   (fts, account_id, limit * 2)).fetchall()
        else:
            like = ' AND '.join('m.content LIKE ?' for _ in terms)
            rows = self.db.execute(f'''SELECT m.id,m.session_id,m.role,m.content,m.created,s.name AS session
                FROM agent_messages m LEFT JOIN sessions s ON s.id=m.session_id WHERE m.account_id=? AND s.deleted IS NULL AND {like}
                ORDER BY m.id DESC LIMIT ?''', (account_id, *('%' + t + '%' for t in terms), limit * 2)).fetchall()
        for row in rows:
            if row['id'] in seen:
                continue
            seen.add(row['id'])
            hits.append({'message_id': row['id'], 'session': row['session'], 'session_id': row['session_id'],
                         'role': row['role'], 'date': time.strftime('%Y-%m-%d %H:%M', time.localtime(row['created'])),
                         'snippet': snippet(row['content'], terms)})
        # Older conversations predate the transcript; they are still the user's history.
        if len(hits) < limit:
            like = ' OR '.join('(prompt LIKE ? OR result LIKE ?)' for _ in terms)
            args = [v for t in terms for v in ('%' + t + '%',) * 2]
            for row in self.db.execute(f'''SELECT j.id,j.session_id,j.prompt,j.result,j.created,s.name AS session FROM jobs j
                LEFT JOIN sessions s ON s.id=j.session_id WHERE j.account_id=? AND j.kind='chat' AND ({like})
                AND j.id NOT IN (SELECT job_id FROM deleted_jobs) AND s.deleted IS NULL
                AND NOT EXISTS (SELECT 1 FROM agent_messages m WHERE m.job_id=j.id) ORDER BY j.created DESC LIMIT ?''',
                                       (account_id, *args, limit - len(hits))):
                hits.append({'job_id': row['id'], 'session': row['session'], 'session_id': row['session_id'], 'role': 'legacy',
                             'date': time.strftime('%Y-%m-%d %H:%M', time.localtime(row['created'])),
                             'snippet': snippet((row['prompt'] or '') + '\n→ ' + (row['result'] or ''), terms)})
        return {'query': query, 'results': hits[:limit],
                'hint': '用 session_read 读取命中消息的前后文；结果是历史记录，不是当前事实。'}

    def read(self, account_id, message_id=None, job_id=None, around=4):
        if job_id:
            row = self.db.execute('SELECT prompt,result,session_id,created FROM jobs WHERE id=? AND account_id=?', (job_id, account_id)).fetchone()
            if not row:
                raise ValueError('未找到这条记录。')
            return {'messages': [{'role': 'user', 'content': row['prompt'][:4000]}, {'role': 'assistant', 'content': (row['result'] or '')[:4000]}]}
        row = self.db.execute('SELECT session_id FROM agent_messages WHERE id=? AND account_id=?', (message_id, account_id)).fetchone()
        if not row:
            raise ValueError('未找到这条记录。')
        rows = self.db.execute('''SELECT id,role,content,created FROM agent_messages WHERE session_id=? AND id BETWEEN ? AND ?
            ORDER BY id''', (row['session_id'], message_id - around, message_id + around)).fetchall()
        return {'session_id': row['session_id'], 'messages': [
            {'message_id': r['id'], 'role': r['role'], 'content': r['content'][:3000],
             'date': time.strftime('%Y-%m-%d %H:%M', time.localtime(r['created']))} for r in rows]}


def snippet(text, terms, width=160):
    text = text or ''
    position = min((i for i in (text.find(t) for t in terms) if i >= 0), default=0)
    start = max(0, position - width // 3)
    return ('…' if start else '') + text[start:start + width].replace('\n', ' ') + ('…' if start + width < len(text) else '')
