"""Checkpoint-free history follows the same answer authority as projection."""
import json

import pytest

from local_ai.store import Store


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / 'history.sqlite3')
    yield value
    value.db.close()


def record_review(store, jid, *, accepted, answer=None):
    verdict = {'accepted': accepted}
    if answer is not None:
        verdict['answer'] = answer
    store.db.execute("INSERT INTO pi_tools(job_id,user_id,name,args,result,created) VALUES (?,1,'final_check','{}',?,1)",
                     (jid, json.dumps(verdict)))
    store.db.commit()


def fallback(store, current):
    job = store.get(current)
    # This is the actual PiAgent fallback condition, not a fabricated helper
    # invocation: the previous run never saved a model checkpoint.
    assert store.conversation(job) == []
    return store.messages(job['user_id'], job['prompt'], session_id=job['session_id'], before=job['created'])


@pytest.mark.parametrize('status,outcome,result,accepted,expected', [
    ('succeeded', 'partial', 'INTERNAL_REJECTED_NOTICE', False, []),
    ('succeeded', 'needs_input', 'Please supply the source document.', True, ['Please supply the source document.']),
    ('succeeded', 'partial', 'The first step is complete; the source is unavailable.', True,
     ['The first step is complete; the source is unavailable.']),
    ('succeeded', 'continued', None, None, []),
    ('succeeded', 'completed', None, None, []),
    ('failed', 'blocked', 'HOST_FAILURE_NOTICE', True, []),
    ('succeeded', 'completed', 'Legacy answer.', None, ['Legacy answer.']),
])
def test_no_checkpoint_fallback_preserves_only_real_accepted_answers(store, status, outcome, result, accepted, expected):
    old = store.enqueue(1, 1, 'chat', 'Original real user message.')
    store.update(old, status=status, outcome=outcome, result=result)
    if accepted is not None:
        record_review(store, old, accepted=accepted)
    current = store.enqueue(1, 1, 'chat', 'Follow-up message.')
    messages = fallback(store, current)
    assert [m['content'] for m in messages if m['role'] == 'assistant'] == expected
    assert [m['content'] for m in messages if m['role'] == 'user'] == [
        'Original real user message.', 'Follow-up message.']
    historical = next(m for m in messages if m['role'] == 'user')
    assert historical['source'] == 'user' and historical['source_job_id'] == old


def test_latest_rejection_and_changed_answer_cannot_inherit_acceptance(store):
    rejected = store.enqueue(1, 1, 'chat', 'First input.')
    store.update(rejected, status='succeeded', outcome='partial', result='REJECTED_NOTICE')
    record_review(store, rejected, accepted=True)
    record_review(store, rejected, accepted=False)
    changed = store.enqueue(1, 1, 'chat', 'Second input.')
    store.update(changed, status='succeeded', outcome='partial', result='LATER_HOST_NOTICE')
    record_review(store, changed, accepted=True, answer='The checked original answer.')
    current = store.enqueue(1, 1, 'chat', 'Follow-up.')
    assert not any(m['role'] == 'assistant' for m in fallback(store, current))


def test_fallback_trims_whole_source_groups_with_user_only_failures(store):
    failed = store.enqueue(1, 1, 'chat', 'x' * 11995)
    store.update(failed, status='failed', outcome='blocked')
    answered = store.enqueue(1, 1, 'chat', 'Keep this question.')
    store.update(answered, status='succeeded', outcome='completed', result='Keep this answer.')
    current = store.enqueue(1, 1, 'chat', 'Next.')
    messages = fallback(store, current)
    assert [(m['role'], m['content']) for m in messages if m['role'] != 'system'] == [
        ('user', 'Keep this question.'), ('assistant', 'Keep this answer.'), ('user', 'Next.')]


def test_fallback_does_not_import_another_session_or_pre_reset_answer(store):
    old = store.enqueue(1, 1, 'chat', 'Old input.')
    store.update(old, status='succeeded', outcome='completed', result='OLD_ANSWER')
    store.reset(1)
    other = store.enqueue(2, 2, 'chat', 'Other session input.')
    store.update(other, status='succeeded', outcome='completed', result='FOREIGN_ANSWER')
    current = store.enqueue(1, 1, 'chat', 'After reset.')
    messages = fallback(store, current)
    assert [(m['role'], m['content']) for m in messages if m['role'] != 'system'] == [('user', 'After reset.')]
