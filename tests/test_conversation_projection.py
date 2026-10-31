"""Cross-job dialogue is an authenticated view, not a resumed harness transcript."""
import json
import time

import pytest

from local_ai.attachments import Attachments
from local_ai.capabilities import Capability
from local_ai.store import Store


@pytest.fixture
def workspace(tmp_path):
    store = Store(tmp_path / 'db')
    account = store.create_account('Owner')
    session = store.create_session(account['id'], 'Conversation')
    yield store, account, session
    store.db.close()


def enqueue(workspace, prompt, session=None):
    store, account, current = workspace
    jid = store.enqueue(account['user_id'], None, 'chat', prompt,
                        session_id=(session or current)['id'], channel='app')
    return store.get(jid)


def text(message):
    content = message['content']
    return content if isinstance(content, str) else '\n'.join(
        part['text'] for part in content if part['type'] == 'text')


def assistant(body, **extra):
    return {'role': 'assistant', 'content': [{'type': 'text', 'text': body}], **extra}


def tool_pair(call_id='read', result=None):
    result = result or {'contents': 'VERIFIED SOURCE'}
    return [
        {'role': 'assistant', 'content': [
            {'type': 'text', 'text': 'UNVERIFIED TOOL PREFACE'},
            {'type': 'toolCall', 'id': call_id, 'name': 'read_file', 'arguments': {'name': 'source.txt'}}]},
        {'role': 'toolResult', 'toolCallId': call_id, 'toolName': 'read_file', 'isError': False,
         'content': [{'type': 'text', 'text': json.dumps(result)}]},
    ]


def review(store, job, accepted, outcome='completed'):
    store.db.execute('INSERT INTO pi_tools(job_id,user_id,name,args,result,error,created) VALUES (?,?,?,?,?,?,?)',
                     (job['id'], job['user_id'], 'final_check', '{}',
                      json.dumps({'accepted': accepted, 'outcome': outcome}), None, time.time()))
    store.db.commit()


def test_failed_checkpoint_keeps_real_input_and_evidence_without_promoting_control(workspace):
    store, _, _ = workspace
    old = enqueue(workspace, 'A real user correction')
    checkpoint = [
        {'role': 'user', 'content': [{'type': 'text', 'text': old['prompt']}]},
        *tool_pair(), assistant('UNVERIFIED CLAIM'),
        {'role': 'user', 'content': '独立核验发现以下缺口；继续修复 INTERNAL REVIEW'},
        assistant('REJECTED REPAIR DRAFT'),
        {'role': 'user', 'content': '执行预算已结束 INTERNAL FINALIZE', 'source': 'harness_control'},
        assistant('FAILED MODEL MESSAGE', stopReason='error', errorMessage='provider failure'),
    ]
    store.save_checkpoint(old['id'], checkpoint)
    store.update(old['id'], status='failed', result='HOST FAILURE NOTICE', error='fixture interruption')
    review(store, old, False)
    follow = enqueue(workspace, 'Continue the conversation')

    carried = store.conversation(follow)
    assert [text(m) for m in carried if m['role'] == 'user'] == [old['prompt']]
    assert [m['source_job_id'] for m in carried if m['role'] == 'user'] == [old['id']]
    assert [json.loads(text(m)) for m in carried if m['role'] == 'toolResult'] == [{'contents': 'VERIFIED SOURCE'}]
    serialized = json.dumps(carried)
    for contamination in ('UNVERIFIED', 'REJECTED', 'INTERNAL', 'FAILED MODEL', 'HOST FAILURE'):
        assert contamination not in serialized
    assert store.checkpoint(old['id']) == checkpoint, 'same-job recovery keeps its full durable transcript'


@pytest.mark.parametrize('outcome', ['completed', 'partial', 'needs_input'])
def test_reviewed_delivered_answer_survives_even_when_checkpoint_only_has_draft(workspace, outcome):
    store, _, _ = workspace
    old = enqueue(workspace, 'A request')
    store.save_checkpoint(old['id'], [{'role': 'user', 'content': old['prompt']}, assistant('DRAFT ONLY')])
    store.update(old['id'], status='succeeded', outcome=outcome, result='Delivered and reviewed answer')
    review(store, old, True, outcome)
    carried = store.conversation(enqueue(workspace, 'Next request'))
    answers = [m for m in carried if m['role'] == 'assistant']
    assert [text(m) for m in answers] == ['Delivered and reviewed answer']
    assert answers[0]['source'] == 'delivered_answer'


def test_rejected_success_notice_is_not_imported_as_assistant_answer(workspace):
    store, _, _ = workspace
    old = enqueue(workspace, 'A request')
    store.save_checkpoint(old['id'], [{'role': 'user', 'content': old['prompt']}, assistant('DRAFT')])
    store.update(old['id'], status='succeeded', outcome='partial', result='本次结果尚未通过核验：HOST CONTROL')
    review(store, old, False, 'partial')
    carried = store.conversation(enqueue(workspace, 'Next request'))
    assert [m['role'] for m in carried] == ['user']


def test_real_user_can_quote_control_words_and_marked_control_cannot_claim_that_role(workspace):
    store, _, _ = workspace
    old = enqueue(workspace, '执行预算已结束，请向用户交付已有结果。')
    store.save_checkpoint(old['id'], [
        {'role': 'user', 'content': old['prompt'], 'source': 'harness_control'},
        {'role': 'user', 'content': old['prompt'], 'source': 'reference'},
        {'role': 'user', 'content': old['prompt'], 'source': 'user', 'source_job_id': old['id']},
        assistant('Legacy delivered response'),
    ])
    store.update(old['id'], status='succeeded', result='Legacy delivered response')
    carried = store.conversation(enqueue(workspace, 'Next'))
    assert [text(m) for m in carried if m['role'] == 'user'] == [old['prompt']]
    assert [text(m) for m in carried if m['role'] == 'assistant'] == ['Legacy delivered response']


def test_user_attachments_and_validated_legacy_steering_survive(workspace, tmp_path):
    store, _, session = workspace
    old = enqueue(workspace, 'Please inspect the attachment')
    correction = enqueue(workspace, 'Use this attached correction')
    attachment_file = tmp_path / 'correction.txt'
    attachment_file.write_text('Attachment contents')
    aid = Attachments(store, correction, tmp_path).add(attachment_file, 'correction.txt')
    metadata = [dict(row) for row in store.db.execute(
        'SELECT id,name,kind FROM attachments WHERE job_id=? AND session_id=?', (correction['id'], session['id']))]
    image_part = {'type': 'image', 'mimeType': 'image/png', 'data': 'fixture'}
    initial = {'role': 'user', 'content': [{'type': 'text', 'text': old['prompt']}, image_part],
               'source': 'user', 'source_job_id': old['id']}
    steering = {'role': 'user', 'content': f"[用户补充 {correction['id']}] {correction['prompt']}\n新附件（元数据）：" + json.dumps(metadata, ensure_ascii=False)}
    store.absorb_input(old, correction)
    store.save_checkpoint(old['id'], [initial, steering])
    store.update(old['id'], status='failed')
    carried = store.conversation(enqueue(workspace, 'Continue'))
    users = [m for m in carried if m['role'] == 'user']
    assert [m['source_job_id'] for m in users] == [old['id'], correction['id']]
    assert users[0]['content'][1] == image_part
    assert aid in text(users[1])
    assert store.checkpoint(old['id']) == [initial, steering]


def test_foreign_session_source_and_reset_history_cannot_be_imported(workspace):
    store, account, session = workspace
    foreign_session = store.create_session(account['id'], 'Foreign')
    foreign = enqueue(workspace, 'PRIVATE FOREIGN INPUT', foreign_session)
    old = enqueue(workspace, 'Local input')
    store.save_checkpoint(old['id'], [
        {'role': 'user', 'content': old['prompt']},
        {'role': 'user', 'content': foreign['prompt']},
        {'role': 'user', 'content': foreign['prompt'], 'source': 'user', 'source_job_id': foreign['id']},
    ])
    store.update(old['id'], status='succeeded', result='Local answer')
    carried = store.conversation(enqueue(workspace, 'Next local input'))
    assert 'PRIVATE FOREIGN' not in json.dumps(carried)
    assert [text(m) for m in carried if m['role'] == 'user'] == [old['prompt']]
    store.reset(account['user_id'], session_id=session['id'])
    assert store.conversation(enqueue(workspace, 'After reset')) == []


def test_multiple_jobs_preserve_authentic_history_and_tool_sources(workspace):
    store, _, _ = workspace
    old = enqueue(workspace, 'First real request')
    store.save_checkpoint(old['id'], [{'role': 'user', 'content': old['prompt']}, *tool_pair(), assistant('First delivered answer')])
    store.update(old['id'], status='succeeded', result='First delivered answer')
    second = enqueue(workspace, 'Second real request')
    prior = store.conversation(second)
    store.save_checkpoint(second['id'], [*prior, {'role': 'user', 'content': second['prompt']},
                                        {'role': 'user', 'content': 'REPAIR CONTROL'}, assistant('Second delivered answer')])
    store.update(second['id'], status='succeeded', result='Second delivered answer')
    carried = store.conversation(enqueue(workspace, 'Third real request'))
    assert [text(m) for m in carried if m['role'] == 'user'] == [old['prompt'], second['prompt']]
    assert [text(m) for m in carried if m.get('source') == 'delivered_answer'] == ['First delivered answer', 'Second delivered answer']
    evidence = next(m for m in carried if m['role'] == 'toolResult')
    assert evidence['source_job_id'] == old['id']
    assert evidence['source_call_id'] == 'read'
    call = next(m for m in carried if m.get('source') == 'historical_evidence' and m['role'] == 'assistant')
    assert call['content'][0]['id'] == evidence['toolCallId']


def test_long_single_turn_keeps_user_and_evidence_without_mutating_recovery(workspace):
    store, _, _ = workspace
    old = enqueue(workspace, 'Preserve this original request')
    checkpoint = [{'role': 'user', 'content': old['prompt']},
                  *tool_pair('large', {'contents': 'x' * 40000}), *tool_pair('latest', {'contents': 'LATEST RECEIPT'})]
    store.save_checkpoint(old['id'], checkpoint)
    store.update(old['id'], status='failed')
    carried = store.conversation(enqueue(workspace, 'Continue'))
    assert [text(m) for m in carried if m['role'] == 'user'] == [old['prompt']]
    receipts = [m for m in carried if m['role'] == 'toolResult']
    preview = json.loads(text(receipts[0]))
    assert preview['historical_preview'] is True and preview['source_call_id'] == 'large'
    assert preview['source_job_id'] == old['id']
    assert json.loads(text(receipts[-1])) == {'contents': 'LATEST RECEIPT'}
    assert len(json.dumps(carried)) < 28000
    assert store.checkpoint(old['id']) == checkpoint


def test_interrupted_tool_request_uses_original_receipt_without_reexecution(workspace):
    store, _, _ = workspace
    old = enqueue(workspace, 'Save a note')
    spec = Capability('write_file', 'Write', effect='local_write', replay='reconcile')
    store.begin_operation(old['id'], 'saved', spec, {'name': 'note.txt', 'content': 'saved bytes'})
    store.finish_operation(old['id'], 'saved', result={'saved': 'note.txt'})
    checkpoint = [{'role': 'user', 'content': old['prompt']}, {'role': 'assistant', 'content': [
        {'type': 'toolCall', 'id': 'saved', 'name': 'write_file', 'arguments': {'name': 'note.txt', 'content': 'saved bytes'}}]}]
    store.save_checkpoint(old['id'], checkpoint)
    store.update(old['id'], status='failed')
    follow = enqueue(workspace, 'Was it saved?')
    carried = store.conversation(follow)
    assert carried[-1]['role'] == 'toolResult' and not carried[-1]['isError']
    assert json.loads(text(carried[-1])) == {'saved': 'note.txt'}
    assert store.operation(follow['id'], 'saved') is None
    assert store.checkpoint(old['id']) == checkpoint


def test_release_pending_inputs_requeues_only_unconsumed_absorbed_jobs(workspace, tmp_path):
    store, account, session = workspace
    old = enqueue(workspace, 'Original active request')
    store.update(old['id'], status='running')
    consumed = enqueue(workspace, 'An already handled correction')
    store.absorb_input(old, consumed)
    pending = enqueue(workspace, 'An unhandled correction')
    store.absorb_input(old, pending)
    another = enqueue(workspace, 'A second unhandled correction')
    store.absorb_input(old, another)
    unrelated = enqueue(workspace, 'An independent queued request')
    store.consume_inputs(old, [consumed['id']])
    attachment_file = tmp_path / 'pending.txt'
    attachment_file.write_text('Preserved correction attachment')
    aid = Attachments(store, pending, tmp_path).add(attachment_file, 'pending.txt')
    checkpoint = [{'role': 'user', 'content': old['prompt']}]
    store.save_checkpoint(old['id'], checkpoint)
    before = {item['id']: store.get(item['id']) for item in (old, consumed, unrelated)}
    pending_requests = {item['job_id']: item['text'] for item in store.pending_inputs(old)}

    assert store.release_pending_inputs(old) == [pending['id'], another['id']]
    for item in (pending, another):
        released = store.get(item['id'])
        assert released['status'] == 'queued'
        assert released['outcome'] is None and released['result'] is None and released['error'] is None
        assert released['delivery_state'] == 'pending' and released['delivered'] == 0
        assert released['prompt'] == item['prompt'] == pending_requests[item['id']]
        assert released['task_id'] == old['task_id']
        assert store.db.execute('SELECT consumed FROM task_inputs WHERE job_id=?', (item['id'],)).fetchone()[0] == 1
    assert store.pending_inputs(old) == []
    assert store.release_pending_inputs(old) == [], 'release is idempotent'
    assert store.checkpoint(old['id']) == checkpoint
    assert Attachments(store, pending, tmp_path).owned(aid)['job_id'] == pending['id']
    for jid, original in before.items():
        assert store.get(jid) == original


def test_release_pending_inputs_respects_session_task_and_queued_state(workspace):
    store, account, session = workspace
    old = enqueue(workspace, 'Original')
    pending = enqueue(workspace, 'Pending correction')
    queued = enqueue(workspace, 'Still queued in the same task')
    store.absorb_input(old, pending)
    store.link_task(queued, old['id'])
    store.db.execute('UPDATE task_inputs SET consumed=0 WHERE job_id=?', (queued['id'],))
    foreign_session = store.create_session(account['id'], 'Foreign')
    foreign = enqueue(workspace, 'Foreign correction', foreign_session)
    # Even a corrupt cross-session task association must not release that job.
    store.db.execute("UPDATE jobs SET task_id=?,status='succeeded',outcome='continued',delivery_state='suppressed',delivered=1 WHERE id=?",
                     (old['task_id'], foreign['id']))
    store.db.execute('UPDATE task_inputs SET task_id=?,consumed=0 WHERE job_id=?', (old['task_id'], foreign['id']))
    store.db.commit()
    originals = {item['id']: store.get(item['id']) for item in (queued, foreign)}
    assert store.release_pending_inputs(old) == [pending['id']]
    for jid, original in originals.items():
        assert store.get(jid) == original
        assert store.db.execute('SELECT consumed FROM task_inputs WHERE job_id=?', (jid,)).fetchone()[0] == 0
    with pytest.raises(PermissionError):
        store.release_pending_inputs({**old, 'session_id': foreign_session['id']})


def test_malformed_shared_task_ids_cannot_read_consume_or_move_foreign_inputs_and_plans(workspace):
    store, account, session = workspace
    target = enqueue(workspace, 'Earlier target request')
    store.update(target['id'], status='succeeded')
    old = enqueue(workspace, 'Current owned request')
    store.update(old['id'], status='running')
    correction = enqueue(workspace, 'Owned pending correction')
    store.absorb_input(old, correction)
    unrelated = enqueue(workspace, 'Owned but unrelated request')
    store.update(unrelated['id'], status='succeeded')
    foreign_session = store.create_session(account['id'], 'Other workspace')
    foreign = enqueue(workspace, 'Foreign session request', foreign_session)
    store.update(foreign['id'], status='succeeded')
    other_account = store.create_account('Other owner')
    other_session = store.create_session(other_account['id'], 'Other account workspace')
    other_id = store.enqueue(other_account['user_id'], None, 'chat', 'Foreign account request',
                             session_id=other_session['id'], channel='app')
    other = store.get(other_id)
    store.revise_plan(old, {'goal': 'Owned plan'})
    outsiders = [foreign, other, unrelated]
    for outsider in outsiders:
        if outsider['id'] != unrelated['id']:
            store.db.execute('UPDATE jobs SET task_id=? WHERE id=?', (old['task_id'], outsider['id']))
        # The unrelated job also has a corrupt input/plan association, while its
        # owning jobs row still points at its genuinely independent task.
        store.db.execute('UPDATE task_inputs SET task_id=?,consumed=0 WHERE job_id=?', (old['task_id'], outsider['id']))
        store.db.execute('INSERT INTO plans(task_id,job_id,content,created) VALUES (?,?,?,?)',
                         (old['task_id'], outsider['id'], json.dumps({'goal': 'PRIVATE OUTSIDER PLAN'}), time.time()))
    store.db.commit()
    originals = {item['id']: store.get(item['id']) for item in outsiders}

    facts = store.task_facts(old)
    assert {item['job_id'] for item in facts['user_requests']} == {old['id'], correction['id']}
    assert facts['plans'] == [{'goal': 'Owned plan'}]
    assert store.pending_inputs(old) == [{'job_id': correction['id'], 'text': correction['prompt']}]
    store.consume_inputs(old, [correction['id'], *(item['id'] for item in outsiders)])
    assert store.pending_inputs(old) == []
    for outsider in outsiders:
        assert store.db.execute('SELECT consumed FROM task_inputs WHERE job_id=?', (outsider['id'],)).fetchone()[0] == 0

    store.link_task(old, target['id'])
    for jid in (old['id'], correction['id']):
        assert store.get(jid)['task_id'] == target['task_id']
        assert store.db.execute('SELECT task_id FROM task_inputs WHERE job_id=?', (jid,)).fetchone()[0] == target['task_id']
    assert store.db.execute('SELECT task_id FROM plans WHERE job_id=?', (old['id'],)).fetchone()[0] == target['task_id']
    for outsider in outsiders:
        assert store.get(outsider['id']) == originals[outsider['id']]
        assert store.db.execute('SELECT task_id FROM task_inputs WHERE job_id=?', (outsider['id'],)).fetchone()[0] == old['task_id']
        assert store.db.execute('SELECT task_id FROM plans WHERE job_id=?', (outsider['id'],)).fetchone()[0] == old['task_id']
    forged = {**old, 'session_id': foreign_session['id']}
    for operation in (lambda: store.task_facts(forged),
                      lambda: store.pending_inputs(forged),
                      lambda: store.consume_inputs(forged, [foreign['id']]),
                      lambda: store.link_task(forged, target['id'])):
        with pytest.raises(PermissionError):
            operation()
