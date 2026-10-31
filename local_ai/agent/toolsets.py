"""Toolsets: which registered capabilities the model sees, and the agent's own tools.

Only the core set plus the session's enabled sets are sent to the model. The
model can open another set with enable_toolset; the registry still enforces
access (owner-only tools stay hidden from everyone else).
"""
from __future__ import annotations

from ..capabilities import Capability, string

DEFAULT = ('core', 'web', 'files', 'media')

# Retired with the old harness: forced plans, task linking and evidence paging.
RETIRED = {'plan_task', 'continue_task', 'task_evidence',
           'generate_video_project', 'resume_video_project', 'video_capacity'}

EXPLICIT = {
    'core': {'todo', 'memory', 'session_search', 'session_read', 'schedule', 'enable_toolset', 'delegate', 'read_skill',
             'list_skills', 'list_files', 'read_file', 'write_file', 'calculate', 'task_status'},
    'web': {'web_search', 'web_read', 'api_get'},
    'files': {'list_attachments', 'read_attachment', 'send_media'},
    'media': {'generate_image', 'generate_video', 'generate_audio', 'list_media'},
    'drama': {'drama_import', 'drama_list', 'drama_get', 'drama_plan', 'drama_edit_shot', 'drama_submit', 'drama_export'},
    'admin': {'system_status', 'capabilities'},
}

DESCRIPTIONS = {
    'web': '联网搜索、读网页、调用公开 JSON API',
    'files': '读取用户上传的附件，把已生成的媒体发给用户',
    'media': '生成图片、视频、语音，查询已生成的媒体',
    'drama': '把剧本做成多镜头短剧（分镜、逐段渲染、合并）',
    'google': '读写已授权的 Google Drive / Docs',
    'computer': '操作这台 Mac：文件、终端、应用、屏幕（仅所有者，需会话开启 Mac 管理）',
    'admin': 'Mac 状态、网络诊断、服务重启、技能与 MCP 发布（仅所有者）',
    'mcp': '调用本会话绑定的 MCP 工具',
}


def toolset_of(spec):
    for name, members in EXPLICIT.items():
        if spec.name in members:
            return name
    if spec.name.startswith('google_'):
        return 'google'
    if spec.name.startswith(('computer_', 'terminal')) or spec.group in ('computer', 'files') and spec.access == 'admin':
        return 'computer'
    if spec.name.startswith('mcp_'):
        return 'mcp'
    if spec.group == 'api':
        return 'web'
    if spec.name.startswith(('mac_', 'network_', 'evolution_')) or spec.access == 'admin':
        return 'admin'
    return 'admin'


AGENT_TOOLS = (
    Capability('todo', '多步任务的进度清单，每次提交完整列表；简单任务不需要。可随时增删改步骤。清单只用于展示和恢复进度，不是完成证明。',
               {'items': {'type': 'array', 'maxItems': 20, 'items': {
                   'type': 'object', 'additionalProperties': False, 'required': ['content', 'status'],
                   'properties': {'content': string(200, minLength=1),
                                  'status': string(20, enum=['pending', 'in_progress', 'completed', 'cancelled'])}}}},
               ('items',), effect='state', group='agent'),
    Capability('memory', '长期记忆，跨会话保留。target=user 记关于用户本人的稳定信息（称呼、时区、偏好）；target=memory 记长期事实与约定。'
               '用户说“记住”时必须写入；不要写一次性信息或密码。replace/remove 用 old_text 指定原条目中的一段文字。',
               {'action': string(10, enum=['add', 'replace', 'remove']), 'target': string(10, enum=['user', 'memory']),
                'text': string(300), 'old_text': string(300)}, ('action', 'target'), effect='local_write', group='agent'),
    Capability('session_search', '全文检索用户过去所有会话（含其他会话）的消息。用户提到“之前/上次/那个”而当前对话里没有时使用。关键词至少两个字，尽量用原话里的词。',
               {'query': string(200, minLength=1)}, ('query',), group='agent'),
    Capability('session_read', '读取 session_search 命中消息的前后文。', {'message_id': {'type': 'integer', 'minimum': 1},
               'job_id': string(80)}, (), group='agent'),
    Capability('schedule', '定时任务：到点自动以 prompt 发起一次任务，结果发回本会话。action=create 需要 title、prompt、kind 和对应时间字段：'
               'once 用 at（YYYY-MM-DD HH:MM，本机时间）；hourly 用 minute；daily 用 time（HH:MM）；weekly 用 weekday（1=周一…7=周日）和 time；interval 用 minutes（≥15）。'
               '只在满足条件时才通知（如价格提醒）设 notify=when_relevant。list 查看；pause/resume/delete 需要 id。创建会请用户确认。',
               {'action': string(10, enum=['create', 'list', 'pause', 'resume', 'delete']), 'id': string(20), 'title': string(60),
                'prompt': string(2000), 'kind': string(10, enum=['once', 'hourly', 'daily', 'weekly', 'interval']),
                'at': string(16), 'time': string(5), 'minute': {'type': 'integer', 'minimum': 0, 'maximum': 59},
                'weekday': {'type': 'integer', 'minimum': 1, 'maximum': 7},
                'minutes': {'type': 'integer', 'minimum': 15, 'maximum': 10080},
                'notify': string(20, enum=['always', 'when_relevant'])}, ('action',), effect='state', replay='reconcile', group='agent'),
    Capability('delegate', '把一个独立的子任务交给一个干净上下文去做（例如读很多网页后只要结论），只带回不超过 400 字的报告。'
               '用来节省上下文，不会更快；子任务不能和用户对话，也不能再委托。toolsets 可选 web/files/media/drama/google。',
               {'task': string(3000, minLength=1), 'toolsets': {'type': 'array', 'maxItems': 4,
                'items': string(20, enum=['web', 'files', 'media', 'drama', 'google'])}}, ('task',), group='agent'),
    Capability('enable_toolset', '为本次任务打开一组额外工具。可选：' + '；'.join(f'{k}：{v}' for k, v in DESCRIPTIONS.items()) + '。',
               {'name': string(20, enum=list(DESCRIPTIONS))}, ('name',), effect='state', group='agent'),
)
