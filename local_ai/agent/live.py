"""Live, in-memory progress of running agent jobs, fanned out to app streams.

Nothing here is durable or reviewed: the final answer is still the job result.
Each running job keeps one state (thinking, reply text, tool steps); every
change is also pushed as a small event to subscribers of that session.
"""
from __future__ import annotations

import asyncio
import time

TEXT_LIMIT = 24000
THINKING_LIMIT = 12000
TOOL_LIMIT = 40

# Short, user-facing names for tool steps.
TOOL_LABELS = {
    'web_search': '搜索网页', 'web_read': '阅读网页', 'api_get': '查询数据', 'read_file': '读取文件', 'write_file': '写入文件',
    'list_files': '查看文件', 'read_attachment': '读取附件', 'list_attachments': '查看附件', 'send_media': '发送媒体',
    'generate_image': '生成图片', 'generate_video': '生成视频', 'generate_audio': '生成语音', 'calculate': '计算',
    'memory': '更新记忆', 'session_search': '检索历史对话', 'session_read': '读取历史对话', 'todo': '更新进度',
    'schedule': '定时任务', 'enable_toolset': '打开工具', 'delegate': '委托子任务', 'read_skill': '读取技能',
    'drama_import': '短剧 · 导入剧本', 'drama_list': '短剧 · 查看项目', 'drama_get': '短剧 · 读取项目',
    'drama_plan': '短剧 · 设定与分镜', 'drama_edit_shot': '短剧 · 修改镜头', 'drama_submit': '短剧 · 提交渲染',
    'drama_export': '短剧 · 合成成片',
}


def tool_label(name):
    if name in TOOL_LABELS:
        return TOOL_LABELS[name]
    if name.startswith('google_'):
        return 'Google 文档'
    if name.startswith(('computer_', 'terminal', 'mac_')):
        return '操作 Mac'
    return name


class LiveHub:
    def __init__(self):
        self.jobs = {}
        self.listeners = {}

    # -- producers --------------------------------------------------------
    def start(self, job):
        state = self.jobs.get(job['id'])
        if not state:
            state = {'job_id': job['id'], 'session_id': job['session_id'], 'account_id': job['account_id'],
                     'thinking': '', 'text': '', 'tools': [], 'phase': 'thinking', 'seq': 0, 'started': time.time()}
            self.jobs[job['id']] = state
        self._push(state, {'type': 'start'})
        return state

    def thinking(self, job_id, delta):
        state = self.jobs.get(job_id)
        if state and delta:
            state['thinking'] = (state['thinking'] + delta)[-THINKING_LIMIT:]
            state['phase'] = 'thinking'
            self._push(state, {'type': 'thinking', 'delta': delta})

    def text(self, job_id, delta):
        state = self.jobs.get(job_id)
        if state and delta:
            state['text'] = (state['text'] + delta)[:TEXT_LIMIT]
            state['phase'] = 'answering'
            self._push(state, {'type': 'text', 'delta': delta})

    def step(self, job_id):
        """The model's step ended in tool calls: its text so far was not the answer."""
        state = self.jobs.get(job_id)
        if state:
            state['text'] = ''
            self._push(state, {'type': 'step'})

    def tool(self, job_id, call_id, name, status, note=''):
        state = self.jobs.get(job_id)
        if not state:
            return
        entry = next((t for t in state['tools'] if t['id'] == call_id), None)
        if entry is None:
            entry = {'id': call_id, 'name': name, 'label': tool_label(name), 'note': note[:60], 'status': status}
            state['tools'] = (state['tools'] + [entry])[-TOOL_LIMIT:]
        else:
            entry['status'] = status
        state['phase'] = 'tools' if status == 'running' else state['phase']
        self._push(state, {'type': 'tool', 'tool': dict(entry)})

    def status(self, job_id, text):
        """Why nothing is streaming yet (model loading, GPU busy); shown on the waiting row."""
        state = self.jobs.get(job_id)
        if state and state.get('status') != text:
            state['status'] = text
            self._push(state, {'type': 'status', 'text': text})

    def suggest(self, job_id, agent):
        state = self.jobs.get(job_id)
        if state:
            state['suggested'] = agent
            self._push(state, {'type': 'suggest', 'agent': agent})

    def finish(self, job_id):
        state = self.jobs.pop(job_id, None)
        if state:
            self._push(state, {'type': 'done'})

    # -- consumers --------------------------------------------------------
    def snapshot(self, job_id):
        state = self.jobs.get(job_id)
        if not state:
            return None
        return {k: (list(map(dict, v)) if k == 'tools' else v) for k, v in state.items() if k not in ('account_id', 'session_id')}

    def running(self, session_id):
        return [self.snapshot(j) for j, s in list(self.jobs.items()) if s['session_id'] == session_id]

    def subscribe(self, account_id, session_id):
        queue = asyncio.Queue(maxsize=2000)
        self.listeners.setdefault(session_id, set()).add((account_id, queue))
        return queue

    def unsubscribe(self, account_id, session_id, queue):
        listeners = self.listeners.get(session_id)
        if listeners:
            listeners.discard((account_id, queue))
            if not listeners:
                self.listeners.pop(session_id, None)

    def _push(self, state, event):
        state['seq'] += 1
        event = {'job_id': state['job_id'], 'seq': state['seq'], **event}
        for account_id, queue in list(self.listeners.get(state['session_id'], ())):
            if account_id != state['account_id']:
                continue
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A stalled reader resynchronises from a snapshot on reconnect.
                pass
