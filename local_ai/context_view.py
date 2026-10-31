"""Bounded, explicitly incomplete model views of durable task material.

These helpers never write storage. Their previews are not complete evidence or
requirements. Callers must retain the full objects for authority and validation.
Character limits describe compact JSON, not tokenizer counts: a caller must also
budget the complete request, including system instructions, tools, and output.
"""
from __future__ import annotations

import copy
import json


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _budget(max_chars):
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 256:
        raise ValueError('A model view needs a character budget of at least 256.')
    return max_chars


def task_reference(job_id):
    """The registered tool retrieves the original task, including user requests."""
    return {'tool': 'task_evidence', 'arguments': {'job_id': job_id, 'offset': 0}}


def bounded_json_view(value, reference=None, max_chars=10000):
    """Return a strict-size envelope; an excerpt is never presented as full JSON."""
    budget = _budget(max_chars)
    serialized = _json(value)

    def envelope(visible, complete):
        return {'value': visible, 'complete': complete, 'overall_complete': complete,
                'can_verify_all_requirements': complete, 'total_characters': len(serialized),
                'reference': copy.deepcopy(reference), 'budget_characters': budget}

    complete = envelope(copy.deepcopy(value), True)
    if len(_json(complete)) <= budget:
        return complete
    empty = envelope({'excerpt': '', 'truncated': True}, False)
    if len(_json(empty)) > budget:
        raise ValueError('The budget cannot hold the original-source reference and completeness metadata.')
    low, high = 0, len(serialized)
    while low < high:
        middle = (low + high + 1) // 2
        if len(_json(envelope({'excerpt': serialized[:middle], 'truncated': True}, False))) <= budget:
            low = middle
        else:
            high = middle - 1
    return envelope({'excerpt': serialized[:low], 'truncated': True}, False)


def bounded_task_facts(facts, job_id, current_input_ids=(), max_chars=10000):
    """Prioritize current requests and expose exact coverage of the model view.

    A single latest current input of up to 12,000 characters may expand the
    requested budget, up to 14,000 serialized characters, to preserve the normal
    inbound request allowance. ``budget_characters`` always states the actual
    bound. Multiple current inputs are not silently promoted to full coverage.

    ``can_verify_all_requirements`` is deliberately conservative: every source
    user request must be complete. Callers with a validated narrower contract
    may separately check its source IDs against each row's ``complete`` flag.
    """
    budget = _budget(max_chars)
    requests = facts.get('user_requests', [])
    plans = facts.get('plans', [])
    if not isinstance(requests, list) or not isinstance(plans, list):
        raise ValueError('Task facts must contain lists of user requests and plans.')
    current = {job_id, *current_input_ids}
    reference = task_reference(job_id)

    def envelope(visible, visible_plans):
        full_ids = {row['job_id'] for row in visible if row['complete']}
        all_requests = len(visible) == len(requests) and all(row['complete'] for row in visible)
        current_complete = current <= full_ids
        return {'task_id': facts.get('task_id'), 'user_requests': visible, 'plans': visible_plans,
                'overall_complete': all_requests and len(visible_plans) == len(plans),
                'current_inputs_complete': current_complete,
                'can_verify_all_requirements': all_requests and current_complete,
                'coverage': {'current_input_count': len(current), 'complete_current_inputs': len(current & full_ids),
                             'total_user_requests': len(requests), 'omitted_user_requests': len(requests) - len(visible),
                             'truncated_user_requests': sum(not row['complete'] for row in visible),
                             'omitted_plans': len(plans) - len(visible_plans)},
                'original': reference, 'budget_characters': budget}

    def request_view(row, prefix=None):
        body = row.get('text')
        if not isinstance(body, str):
            raise ValueError('A user request must contain its original text.')
        complete = prefix is None or prefix >= len(body)
        original = {**task_reference(row['job_id']), 'source_job_id': row['job_id'],
                    'section': 'task.user_requests'}
        return {**copy.deepcopy(row), 'text': body if complete else body[:prefix],
                'source_job_id': row['job_id'], 'complete': complete,
                'text_characters': len(body), 'original': original}

    # Recent corrections take priority over older requests. Output order is
    # restored afterward so that temporal relationships remain explicit.
    indexed = list(enumerate(requests))
    priority = sorted(indexed, key=lambda item: (item[1].get('job_id') in current, item[0]), reverse=True)
    newest_current = next((row for _, row in priority if row.get('job_id') in current), None)
    if newest_current is not None and isinstance(newest_current.get('text'), str) and len(newest_current['text']) <= 12000:
        needed = len(_json(envelope([request_view(newest_current)], [])))
        if needed <= 14000:
            # A larger declared budget has the same digit width in this range.
            budget = max(budget, min(14000, needed + 4))
    if len(_json(envelope([], []))) > budget:
        raise ValueError('The budget cannot hold task coverage and original-source metadata.')

    selected = {}

    def ordered(extra=None):
        rows = {**selected, **(extra or {})}
        return [rows[index] for index in sorted(rows)]

    for index, row in priority:
        full = request_view(row)
        if len(_json(envelope(ordered({index: full}), []))) <= budget:
            selected[index] = full
            continue
        empty = request_view(row, 0)
        if len(_json(envelope(ordered({index: empty}), []))) > budget:
            continue
        low, high = 0, len(row['text'])
        while low < high:
            middle = (low + high + 1) // 2
            if len(_json(envelope(ordered({index: request_view(row, middle)}), []))) <= budget:
                low = middle
            else:
                high = middle - 1
        selected[index] = request_view(row, low)

    visible_plans = []
    for plan in reversed(plans):
        candidate = [copy.deepcopy(plan), *visible_plans]
        if len(_json(envelope(ordered(), candidate))) <= budget:
            visible_plans = candidate
    return envelope(ordered(), visible_plans)


def read_task_input(text, job_id, offset=0, limit=4000):
    """Pure character paging for an already authorized original user input.

    This helper is not a registered tool. A host exposing it must validate the
    job's account and session before supplying text. Current model references
    intentionally use the existing task_evidence tool instead.
    """
    if not isinstance(text, str):
        raise ValueError('The original input must be text.')
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError('offset must be a nonnegative character position.')
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 12000:
        raise ValueError('limit must be between 1 and 12000 characters.')
    end = min(offset + limit, len(text))
    return {'job_id': job_id, 'offset': offset, 'next_offset': end if end < len(text) else None,
            'total_characters': len(text), 'text': text[offset:end],
            'complete_input': offset == 0 and end == len(text), 'untrusted_content': True}
