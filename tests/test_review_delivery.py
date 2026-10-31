"""Final review is bound to the exact answer and input revision being delivered.

These tests exercise the real Node/Python event pipe without a model, network,
Telegram, or production data. The runner deliberately sends adversarial event
sequences so a model-only acceptance test cannot hide a host protocol regression.
"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import local_ai.pi_agent as module
from local_ai.pi_agent import PiAgent
from local_ai.harness import HarnessService
from local_ai.store import Store


def verdict(answer, *, accepted=True, outcome="completed", feedback="", checks=None):
    return {
        "accepted": accepted,
        "outcome": outcome,
        "repair": not accepted,
        "feedback": feedback,
        "checks": checks or [],
        "answer": answer,
    }


@pytest.fixture
def review_protocol(tmp_path, monkeypatch):
    real_node = module.ROOT / "vendor/node"
    (tmp_path / "vendor").mkdir()
    (tmp_path / "pi").mkdir()
    (tmp_path / "vendor/node").symlink_to(real_node.resolve())
    monkeypatch.setattr(module, "ROOT", tmp_path)
    store = Store(tmp_path / "test.sqlite3")
    jid = store.enqueue(1, 1, "chat", "Explain the supplied fact in one sentence.")
    store.update(jid, status="running")
    engine = SimpleNamespace(lock=asyncio.Lock(), start=AsyncMock(), stop=AsyncMock(),
                             chat=AsyncMock(return_value='{"continue":true}'))

    def make(reviewer, checked_answer, delivered_answer=None, *, checked_generation=None, **result_flags):
        delivered_answer = checked_answer if delivered_answer is None else delivered_answer
        runner = tmp_path / "pi/runner.mjs"
        runner.write_text("""
import {createInterface} from 'node:readline';
const lines = createInterface({input: process.stdin});
const checked = CHECKED;
const delivered = DELIVERED;
const flags = FLAGS;
const checkedGeneration = GENERATION;
lines.on('line', line => {
  const message = JSON.parse(line);
  if (message.type === 'init') {
    process.stdout.write(JSON.stringify({type: 'final_check', protocol: 2,
      id: 'review-1', answer: checked, ...(checkedGeneration === null ? {} : {generation: checkedGeneration})})+'\\n');
  } else if (message.type === 'review_result') {
    process.stdout.write(JSON.stringify({type: 'result', protocol: 2,
      answer: delivered, calls: 0, turns: 1, limited: false, ...flags})+'\\n');
    lines.close();
  }
});
""".replace("CHECKED", json.dumps(checked_answer)).replace("DELIVERED", json.dumps(delivered_answer)).replace("FLAGS", json.dumps(result_flags)).replace("GENERATION", json.dumps(checked_generation)))
        return PiAgent(engine, store, lambda _: True, tmp_path, runner=runner, reviewer=reviewer)

    yield store, jid, make
    store.db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('reason', ['length', 'aborted', 'error', 'empty'])
async def test_incomplete_generation_cannot_be_marked_completed_even_if_review_accepts(review_protocol, reason):
    store, jid, make = review_protocol
    draft = 'The first section explains how rain forms.'
    reviewer = SimpleNamespace(review=AsyncMock(return_value=verdict(draft)))
    generation = {'complete': False, 'stop_reason': reason, 'reason': reason}
    # Result deliberately omits metadata: the checked incomplete state must
    # survive the protocol boundary and cannot be replaced by a model verdict.
    answer = await asyncio.wait_for(make(reviewer, draft, checked_generation=generation).run(store.get(jid)), 10)
    assert answer.startswith(draft)
    assert '全文尚未完成' in answer
    assert store.get(jid)['outcome'] == 'partial'
    saved = json.loads(store.db.execute("SELECT result FROM pi_tools WHERE job_id=? AND name='final_check'", (jid,)).fetchone()[0])
    assert saved['outcome'] == 'partial' and saved['repair'] is False
    assert saved['generation_incomplete'] is True
    summary = json.loads(store.db.execute("SELECT result FROM pi_tools WHERE job_id=? AND name='run_summary'", (jid,)).fetchone()[0])
    assert summary['generation'] == generation
    store.update(jid, status='succeeded', result=answer)
    assert store.accepted_answer(store.get(jid)) == answer
    assert saved['answer'] == answer
    assert reviewer.review.await_args.args[1] == answer


@pytest.mark.asyncio
async def test_durable_length_stop_overrides_claimed_complete_metadata(review_protocol):
    store, jid, make = review_protocol
    draft = 'The next section starts with'
    store.save_checkpoint(jid, [{'role': 'assistant', 'source_job_id': jid,
        'content': [{'type': 'text', 'text': draft}], 'stopReason': 'length'}])
    reviewer = SimpleNamespace(review=AsyncMock(return_value=verdict(draft)))
    answer = await asyncio.wait_for(make(reviewer, draft,
        generation={'complete': True, 'stop_reason': 'stop', 'reason': ''}).run(store.get(jid)), 10)
    assert answer.startswith(draft)
    assert store.get(jid)['outcome'] == 'partial'
    store.update(jid, status='succeeded', result=answer)
    assert store.accepted_answer(store.get(jid)) == answer


@pytest.mark.asyncio
async def test_complete_continuation_can_be_delivered_as_complete(review_protocol):
    store, jid, make = review_protocol
    draft = 'Rain forms in clouds. The water returns to rivers.'
    reviewer = SimpleNamespace(review=AsyncMock(return_value=verdict(draft)))
    generation = {'complete': True, 'stop_reason': 'stop', 'reason': ''}
    answer = await asyncio.wait_for(make(reviewer, draft, checked_generation=generation,
        generation=generation).run(store.get(jid)), 10)
    assert answer == draft
    assert store.get(jid)['outcome'] == 'completed'


@pytest.mark.asyncio
async def test_truncation_does_not_override_rejected_content_review(review_protocol):
    store, jid, make = review_protocol
    draft = 'UNVERIFIED_CANDIDATE_CANARY'
    reviewer = SimpleNamespace(review=AsyncMock(return_value=verdict(draft, accepted=False, outcome='partial')))
    answer = await asyncio.wait_for(make(reviewer, draft,
        checked_generation={'complete': False, 'stop_reason': 'length', 'reason': 'length'}).run(store.get(jid)), 10)
    assert draft not in answer
    assert store.get(jid)['outcome'] == 'partial'


@pytest.mark.parametrize('outcome', ['pending', 'needs_input', 'blocked', 'partial'])
def test_incomplete_reply_preserves_evidence_backed_task_state(outcome):
    checked = {**verdict('Partial acknowledgement.', outcome=outcome),
               'completion_watch': ['child-1'], 'completion_watch_only': outcome == 'pending'}
    updated = module.generation_verdict(checked, {'complete': False, 'stop_reason': 'length', 'reason': 'length'})
    assert updated['outcome'] == outcome
    assert updated['completion_watch'] == checked['completion_watch']
    assert updated['completion_watch_only'] == checked['completion_watch_only']


@pytest.mark.asyncio
async def test_result_cannot_reuse_acceptance_for_a_different_answer(review_protocol):
    store, jid, make = review_protocol
    checked = "The provided value is four."
    changed = "The provided value is nine."
    reviewer = SimpleNamespace(review=AsyncMock(side_effect=[
        verdict(checked),
        verdict(changed, accepted=False, outcome="partial", feedback="The value conflicts with the supplied fact."),
    ]))
    answer = await asyncio.wait_for(make(reviewer, checked, changed).run(store.get(jid)), 10)
    assert [call.args[1] for call in reviewer.review.await_args_list] == [checked, changed]
    assert store.get(jid)["outcome"] != "completed"
    assert answer != changed


@pytest.mark.asyncio
@pytest.mark.parametrize('already_absorbed',[False,True])
async def test_review_input_snapshot_cannot_be_reused_after_new_user_correction(review_protocol,already_absorbed):
    store, jid, make = review_protocol
    checked = "The provided value is four."
    calls = []
    correction = None

    async def review(job, answer):
        nonlocal correction
        calls.append([item["text"] for item in store.task_facts(job)["user_requests"]])
        if len(calls) == 1:
            # Simulate a correction arriving while the independent review is in
            # progress. Its source request must remain durable and unanswered.
            correction = store.enqueue(1, 1, "chat", "Correction: the provided value is nine.")
            if already_absorbed:
                store.absorb_input(store.get(jid), store.get(correction))
            return verdict(answer)
        return verdict(answer, accepted=False, outcome="partial",
                       feedback="The latest correction has not been addressed.")

    # A source correction already absorbed into this run must be consumed by a
    # resumed model turn, not silently covered by the previous acceptance.
    with pytest.raises(module.ResumeRequired):
        await asyncio.wait_for(make(SimpleNamespace(review=review), checked).run(store.get(jid)), 10)
    assert store.get(jid)["outcome"] != "completed"
    assert store.get(correction) is not None
    assert [item["job_id"] for item in store.pending_inputs(store.get(jid))] == [correction]


@pytest.mark.asyncio
async def test_rejected_review_diagnostics_are_not_presented_as_the_user_answer(review_protocol):
    store, jid, make = review_protocol
    internal = "INTERNAL_REVIEW_DIAGNOSTIC_CANARY"
    reviewer = SimpleNamespace(review=AsyncMock(return_value=verdict(
        "A draft that has not been verified.", accepted=False, outcome="partial",
        feedback=internal,
        checks=[{"requirement": "explanation", "satisfied": True,
                 "evidence": "Candidate answer contains " + internal}],
    )))
    answer = await asyncio.wait_for(make(reviewer, "A draft that has not been verified.").run(store.get(jid)), 10)
    assert answer.strip()
    assert internal not in answer
    assert "Candidate answer contains" not in answer
    assert store.get(jid)["outcome"] == "partial"


@pytest.mark.asyncio
async def test_truthful_accepted_partial_answer_is_delivered_without_internal_rewrite(review_protocol):
    store, jid, make = review_protocol
    partial = "The first supplied fact is confirmed; the second source did not respond."
    reviewer = SimpleNamespace(review=AsyncMock(return_value=verdict(
        partial, outcome="partial", feedback="Internal note: one source remains unavailable.")))
    answer = await asyncio.wait_for(make(reviewer, partial).run(store.get(jid)), 10)
    assert answer == partial
    assert store.get(jid)["outcome"] == "partial"
    assert reviewer.review.await_count == 1


@pytest.mark.asyncio
async def test_resume_for_new_input_keeps_checkpoint_and_does_not_create_failure_reply(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    jid = store.enqueue(1, 1, "chat", "Explain the supplied fact.")
    checkpoint = [{"role": "user", "content": "Explain the supplied fact."}]
    store.save_checkpoint(jid, checkpoint)
    run = store.start_run(jid)
    engine = SimpleNamespace(lock=asyncio.Lock(), stop=AsyncMock(), process=None,
                             speech_worker=None, voice_reserved=False)
    service = HarnessService(tmp_path, store=store, engine=engine)
    service.execute = AsyncMock(side_effect=module.ResumeRequired())
    try:
        await service.run_job({**store.get(jid), "run_id": run["id"]})
        saved = store.get(jid)
        assert saved["status"] == "queued"
        assert saved["outcome"] is None
        assert saved["result"] is None and saved["error"] is None
        assert store.checkpoint(jid) == checkpoint
        assert store.pending_delivery() == []
        assert store.db.execute("SELECT count(*) FROM history").fetchone()[0] == 0
        assert store.db.execute("SELECT count(*) FROM runtime_events WHERE kind='run_error'").fetchone()[0] == 0
        assert store.db.execute("SELECT status FROM task_runs WHERE id=?", (run["id"],)).fetchone()[0] == "interrupted"
    finally:
        store.db.close()


@pytest.mark.asyncio
async def test_exhausted_run_releases_correction_without_sending_the_old_answer(review_protocol):
    store,jid,make=review_protocol
    correction=None
    async def review(job,answer):
        nonlocal correction
        correction=store.enqueue(1,1,'chat','New input that needs its own bounded run.')
        store.absorb_input(store.get(jid),store.get(correction))
        return verdict(answer)
    agent=make(SimpleNamespace(review=review),'Outdated answer.',
               budget_limited=True,deferred_inputs=True)
    service=HarnessService(store.path.parent,store=store,engine=agent.engine)
    service.execute=agent.run
    store.update(jid,status='queued')
    run=store.start_run(jid)
    await asyncio.wait_for(service.run_job({**store.get(jid),'run_id':run['id']}),10)
    assert store.get(jid)['outcome']=='continued'
    assert store.get(jid)['delivery_state']=='suppressed'
    assert store.get(jid)['result'] is None
    assert store.get(correction)['status']=='queued'
    assert store.pending_inputs(store.get(jid))==[]
    assert store.pending_delivery()==[]


@pytest.mark.asyncio
@pytest.mark.parametrize('continually_changing', [False, True])
async def test_child_evidence_changes_only_recheck_the_same_candidate(review_protocol, continually_changing):
    store, jid, make = review_protocol
    child = store.enqueue(1, 1, 'image', 'Fixture image', parent_job_id=jid)
    store.update(child, status='succeeded', outcome='completed', delivery_state='pending')
    checked = 'The explanation remains valid while image delivery is settling.'
    candidates = []

    async def review(job, answer):
        candidates.append(answer)
        if len(candidates) == 1:
            store.update(child, delivery_state='sending')
        elif continually_changing:
            store.update(child, delivery_state='delivered')
        return verdict(answer)

    answer = await asyncio.wait_for(make(SimpleNamespace(review=review), checked).run(store.get(jid)), 10)
    assert candidates == [checked, checked], 'Evidence churn must not restart tools or a new model draft.'
    assert not store.pending_inputs(store.get(jid))
    if continually_changing:
        assert answer != checked
        assert store.get(jid)['outcome'] == 'partial'
        saved = json.loads(store.db.execute("SELECT result FROM pi_tools WHERE job_id=? AND name='final_check' ORDER BY id DESC LIMIT 1", (jid,)).fetchone()[0])
        assert saved['evidence_unstable'] is True
        assert saved['repair'] is False and not saved.get('inputs_changed')
    else:
        assert answer == checked
        assert store.get(jid)['outcome'] == 'completed'


@pytest.mark.asyncio
async def test_user_correction_during_evidence_recheck_still_requires_resume(review_protocol):
    store, jid, make = review_protocol
    child = store.enqueue(1, 1, 'image', 'Fixture image', parent_job_id=jid)
    store.update(child, status='succeeded', outcome='completed', delivery_state='pending')
    calls, correction = 0, None

    async def review(job, answer):
        nonlocal calls, correction
        calls += 1
        if calls == 1:
            store.update(child, delivery_state='sending')
        else:
            correction = store.enqueue(1, 1, 'chat', 'Use the corrected value instead.')
            store.absorb_input(store.get(jid), store.get(correction))
        return verdict(answer)

    with pytest.raises(module.ResumeRequired):
        await asyncio.wait_for(make(SimpleNamespace(review=review), 'Old candidate.').run(store.get(jid)), 10)
    assert calls == 2
    assert [item['job_id'] for item in store.pending_inputs(store.get(jid))] == [correction]
    assert store.get(jid)['outcome'] != 'completed'
