"""Account-level long-term memory: a short user profile and a list of durable facts.

Both live as plain Markdown so the owner can read and edit them directly
(runtime/memory/<account>/USER.md and MEMORY.md). Entries are one per line,
prefixed with "- ". Limits keep both inside ~1500 tokens of the system prompt.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

LIMITS = {'user': 600, 'memory': 1400}
FILES = {'user': 'USER.md', 'memory': 'MEMORY.md'}
SECRET = re.compile(r'(?i)(password|passwd|密码|口令|token|api[_ -]?key|secret|私钥|助记词|seed phrase|sk-[a-z0-9]{8,})')


class Memory:
    def __init__(self, root, account_id):
        if not re.fullmatch(r'[0-9a-f]{8,64}', account_id or ''):
            raise ValueError('无效账号。')
        self.directory = Path(root) / account_id
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    def path(self, target):
        if target not in FILES:
            raise ValueError('target 须为 user 或 memory。')
        return self.directory / FILES[target]

    def entries(self, target):
        path = self.path(target)
        if not path.is_file() or path.is_symlink():
            return []
        return [line[2:].strip() for line in path.read_text(encoding='utf-8').splitlines() if line.startswith('- ') and line[2:].strip()]

    def write(self, target, entries):
        text = ''.join('- ' + e + '\n' for e in entries)
        if len(text) > LIMITS[target]:
            raise ValueError(f'{FILES[target]} 超出 {LIMITS[target]} 字上限（当前 {len(text)} 字）。请先合并或删除旧条目再写入。')
        path = self.path(target)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(text, encoding='utf-8')
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)

    def apply(self, action, target, text=None, old_text=None):
        entries = self.entries(target)
        if action in ('add', 'replace') and (not isinstance(text, str) or not text.strip() or '\n' in text.strip()):
            raise ValueError('每条记忆须为一行非空文字。')
        if text and SECRET.search(text):
            raise ValueError('记忆里不能保存密码、令牌或密钥。')
        if action == 'add':
            if text.strip() in entries:
                return {'unchanged': True, 'entries': entries}
            entries.append(text.strip())
        elif action in ('replace', 'remove'):
            matches = [i for i, e in enumerate(entries) if old_text and old_text.strip() in e]
            if len(matches) != 1:
                raise ValueError('old_text 须唯一匹配一条已有记忆；先查看当前条目。' if matches else '没有找到 old_text 对应的记忆。')
            if action == 'remove':
                entries.pop(matches[0])
            else:
                entries[matches[0]] = text.strip()
        else:
            raise ValueError('action 须为 add、replace 或 remove。')
        self.write(target, entries)
        return {'saved': True, 'target': FILES[target], 'entries': entries,
                'used': sum(len(e) + 3 for e in entries), 'limit': LIMITS[target]}

    def prompt_block(self):
        user, memory = self.entries('user'), self.entries('memory')
        if not user and not memory:
            return '（还没有记忆。）'
        parts = []
        if user:
            parts.append('关于用户：\n' + '\n'.join('- ' + e for e in user))
        if memory:
            parts.append('长期记忆：\n' + '\n'.join('- ' + e for e in memory))
        return '\n'.join(parts)
