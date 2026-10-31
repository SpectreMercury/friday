"""Rule-based script parsing: scene headings, speaker lines, action lines.

Rules cover the usual Chinese and English screenplay conventions; prose that has
no scene headings is cut into paragraph groups so the model can analyse it in
pieces instead of filling the whole 32K context at once.
"""
from __future__ import annotations

import math
import re
from collections import Counter

MAX_SCRIPT = 30000
HEADING = re.compile(
    r'^\s*(?:#+\s*)?(?:'
    r'第[一二三四五六七八九十百零〇\d]+[场幕集]'
    r'|场景?\s*[一二三四五六七八九十\d]+'
    r'|\d+\s*[.、]\s*(?:内|外|内外)景'
    r'|(?:内|外|内外)景\s*[·・:：\-—]'
    r'|(?:INT|EXT|INT\./EXT)\.'
    r')', re.I)
SPEAKER = re.compile(r'^\s*([^\s：:（(「“"]{1,12})\s*(?:[（(]([^）)]{0,20})[）)])?\s*[：:]\s*(.+)$')
NOT_NAMES = {'场景', '时间', '地点', '人物', '旁白提示', '备注', '注', '画面', '镜头', '字幕', '音效', 'INT', 'EXT'}
TIME_WORDS = (('夜', 'night'), ('晚', 'night'), ('深夜', 'night'), ('晨', 'morning'), ('早', 'morning'),
              ('黄昏', 'dusk'), ('傍晚', 'dusk'), ('午', 'day'), ('日', 'day'), ('白天', 'day'))


def parse(script):
    if not isinstance(script, str) or not script.strip():
        raise ValueError('剧本是空的。')
    if len(script) > MAX_SCRIPT:
        raise ValueError(f'剧本有 {len(script)} 字，超过单个项目 {MAX_SCRIPT} 字上限。请按集拆开导入。'
                         + suggest_cut(script))
    lines = script.replace('\r\n', '\n').split('\n')
    scenes, current, offset = [], None, 0
    for line in lines:
        start = offset
        offset += len(line) + 1
        if HEADING.match(line):
            current = {'heading': line.strip().lstrip('#').strip(), 'start': start, 'end': offset, 'lines': []}
            scenes.append(current)
            continue
        if current is None:
            if not line.strip():
                continue
            current = {'heading': '', 'start': start, 'end': offset, 'lines': []}
            scenes.append(current)
        current['end'] = offset
        if line.strip():
            current['lines'].append(line.strip())
    format_ = 'screenplay' if any(s['heading'] for s in scenes) else 'plain'
    if format_ == 'plain':
        scenes = paragraphs(script)
    speakers = Counter()
    result = []
    for index, scene in enumerate(scenes):
        dialogue, action = [], []
        for line in scene['lines']:
            match = SPEAKER.match(line)
            if match and match.group(1) not in NOT_NAMES and not match.group(1).isdigit():
                dialogue.append({'name': match.group(1), 'cue': match.group(2) or '', 'line': match.group(3).strip()})
                speakers[match.group(1)] += 1
            else:
                action.append(line)
        heading = scene['heading'] or f'第 {index + 1} 段'
        result.append({'heading': heading, 'location': location(heading), 'time_of_day': time_of_day(heading),
                       'source_start': scene['start'], 'source_end': scene['end'],
                       'text': script[scene['start']:scene['end']].strip(),
                       'dialogue': dialogue, 'action': action})
    return {'format': format_, 'scenes': result, 'characters': [name for name, _ in speakers.most_common(30)]}


def paragraphs(script, size=900):
    """Group prose into pieces of roughly `size` characters.

    Cuts prefer blank lines, then single line breaks, then sentence ends, so prose
    written without blank lines (or as one long line) still splits instead of
    becoming one oversized piece.
    """
    groups, current = [], []
    for start, end in units(script, 0, len(script), size):
        current.append((start, end))
        if current[-1][1] - current[0][0] >= size:
            groups.append(group(script, current))
            current = []
    if current:
        groups.append(group(script, current))
    return groups


def units(script, start, end, size, separators=(r'\n\s*\n', r'\n', r'(?<=[。！？!?…])[”」』"]?')):
    """Non-blank spans of script[start:end], each under `size` where some separator allows it."""
    text = script[start:end]
    if not text.strip():
        return []
    if len(text) <= size or not separators:
        return [(start, end)]
    spans, last = [], 0
    for match in re.finditer(separators[0], text):
        spans.append((last, match.end()))
        last = match.end()
    spans.append((last, len(text)))
    if len(spans) == 1:
        return units(script, start, end, size, separators[1:])
    return [u for a, b in spans for u in units(script, start + a, start + b, size, separators[1:])]


def group(script, spans):
    start, end = spans[0][0], spans[-1][1]
    return {'heading': '', 'start': start, 'end': end, 'lines': lines_of([script[start:end]])}


def lines_of(blocks):
    return [line.strip() for block in blocks for line in block.split('\n') if line.strip()]


def location(heading):
    parts = [p.strip() for p in re.split(r'[·・\-—/|，,]', re.sub(r'^.*?(?:内景|外景|INT\.|EXT\.)', '', heading, flags=re.I)) if p.strip()]
    return parts[0][:40] if parts else ''


def time_of_day(heading):
    for word, value in TIME_WORDS:
        if word in heading:
            return value
    match = re.search(r'\b(DAY|NIGHT|MORNING|DUSK|EVENING)\b', heading, re.I)
    return match.group(1).lower() if match else ''


def suggest_cut(script):
    cuts = [m.start() for m in re.finditer(r'(?m)^\s*第[一二三四五六七八九十百\d]+集', script)]
    if len(cuts) > 1:
        return f'检测到 {len(cuts)} 个「第X集」标题，可以按集分别导入。'
    return '建议在场次或段落边界处切开。'


def estimate_duration(dialogue, action_text=''):
    """Seconds for one shot: ~4 Chinese characters per second of speech plus room for action."""
    speech = sum(len(re.sub(r'\s', '', d.get('line', ''))) for d in dialogue) / 4
    seconds = math.ceil(speech + (3 if action_text else 2)) if speech else 6
    return max(4, min(15, seconds))

