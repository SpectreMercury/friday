"""Durable attempts keep task identity, skill snapshots, and side-effect receipts."""
import asyncio
import contextlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from local_ai.capabilities import Capability, CapabilityRegistry
from local_ai.harness import HarnessService
from local_ai.pi_agent import acquire_inference_lease
from local_ai.store import Store
from local_ai.tool_runtime import ToolRuntime


@pytest.fixture
def setup(tmp_path):
    skill = tmp_path / 'skills/research'
    skill.mkdir(parents=True)
    (skill / 'SKILL.md').write_text('Research\nVERSION ONE')
    store = Store(tmp_path / 'db', skills_root=skill.parent)
    account = store.create_account('Owner', is_admin=True)
    session = store.create_session(account['id'], 'Managed', skills=['research'])
    session = store.update_session(account['id'], session['id'], session['revision'], management=True)
    yield store, account, session, skill
    store.db.close()


def make_service(store):
    engine = SimpleNamespace(speech_worker=None, lock=asyncio.Lock(), process=None,
                             voice_reserved=False, stop=AsyncMock())
    return HarnessService(store.path.parent, store=store, engine=engine)


async def test_gpu_queue_wait_does_not_consume_execution_deadline():
    lock=asyncio.Lock()
    await lock.acquire()
    async def release_gpu():
        await asyncio.sleep(.05)
        lock.release()
    releaser=asyncio.create_task(release_gpu())
    async with asyncio.timeout(.015) as execution_timeout:
        await acquire_inference_lease(lock,execution_timeout)
        assert lock.locked()
    lock.release()
    await releaser


def enqueue(store, account, session, prompt='request'):
    return store.enqueue(account['user_id'], None, 'chat', prompt, session_id=session['id'], channel='app')


async def wait_settled(service, jid, status):
    async with asyncio.timeout(3):
        while service.store.get(jid)['status'] != status or jid in service.active_runs:
            await asyncio.sleep(.005)


async def stop_worker(worker):
    worker.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await worker


async def test_actual_approval_resume_has_distinct_runs_same_task_and_pinned_skills(setup, tmp_path):
    store, account, session, skill = setup
    service = make_service(store)
    jid = enqueue(store, account, session)
    snapshot = store.capture_skills(jid)
    writes = tmp_path / 'side-effects.txt'
    registry = CapabilityRegistry()

    async def mutation(name, args):
        if name == 'approved_write':
            assert await service.approvals.request(store.get(jid), 'Append protected output')
        with writes.open('a') as target:
            target.write(name + '\n')
        return {'written': name}
    for name in ('first_write', 'approved_write'):
        registry.register(Capability(name, name, effect='local_write', replay='reconcile'), mutation)

    async def execute(job):
        runtime = ToolRuntime(store, job, registry, service.is_allowed)
        for name in ('first_write', 'approved_write'):
            outcome = await runtime.call(name, name, {})
            assert not outcome['failure']
        return 'Both durable operations completed.'
    service.execute = execute
    worker = asyncio.create_task(service.worker())
    try:
        await wait_settled(service, jid, 'waiting_approval')
        first = store.session_runs(account['id'], session['id'], jid)
        assert len(first) == 1 and first[0]['status'] == 'waiting_approval'
        assert first[0]['attempt'] == 1 and first[0]['ended_at'] >= first[0]['started_at']
        assert writes.read_text() == 'first_write\n'
        (skill / 'SKILL.md').write_text('Research\nCHANGED DURING WAIT')
        token = service.approvals.list_for_session(account['id'], session['id'])[0]['token']
        service.approvals.decide(account['id'], session['id'], token, 'approved')
        await wait_settled(service, jid, 'succeeded')
        runs = store.session_runs(account['id'], session['id'], jid)
        assert [r['attempt'] for r in runs] == [1, 2]
        assert [r['status'] for r in runs] == ['waiting_approval', 'succeeded']
        assert len({r['id'] for r in runs}) == 2
        assert all((r['job_id'], r['task_id'], r['account_id'], r['session_id']) ==
                   (jid, jid, account['id'], session['id']) for r in runs)
        assert all(r['ended_at'] is not None for r in runs)
        assert writes.read_text() == 'first_write\napproved_write\n'
        assert store.capture_skills(jid) == snapshot
        assert store.operation(jid, 'first_write')['status'] == 'succeeded'
    finally:
        await stop_worker(worker)


async def test_media_approval_resume_settles_provisional_outcome(setup, tmp_path):
    store, account, session, _ = setup
    service = make_service(store)
    jid = store.enqueue(account['user_id'], None, 'video', 'A blue circle crosses a white room.',
                        session_id=session['id'], channel='app')
    artifact = tmp_path / 'approved-output.mp4'

    async def execute(job):
        assert await service.approvals.request(job, 'Confirm the prepared video plan.', purpose='video')
        artifact.write_bytes(b'isolated renderer fixture')
        return str(artifact)

    service.execute = execute
    worker = asyncio.create_task(service.worker())
    try:
        await wait_settled(service, jid, 'waiting_approval')
        assert store.get(jid)['outcome'] == 'needs_input'
        token = service.approvals.list_for_session(account['id'], session['id'])[0]['token']
        service.approvals.decide(account['id'], session['id'], token, 'approved')
        assert store.get(jid)['outcome'] == 'pending'
        await wait_settled(service, jid, 'succeeded')
        completed = store.get(jid)
        assert completed['result'] == str(artifact) and artifact.is_file()
        assert completed['outcome'] == 'completed'
        assert store.db.execute('SELECT outcome FROM tasks WHERE id=?', (jid,)).fetchone()[0] == 'completed'
        assert [run['status'] for run in store.session_runs(account['id'], session['id'], jid)] == [
            'waiting_approval', 'succeeded']
    finally:
        await stop_worker(worker)


@pytest.mark.parametrize('outcome', ['pending', 'partial', 'blocked', 'needs_input'])
async def test_successful_chat_keeps_reviewed_outcome(setup, outcome):
    store, account, session, _ = setup
    service = make_service(store)
    jid = enqueue(store, account, session)

    async def execute(job):
        store.update(job['id'], outcome=outcome)
        return 'Accurate status reply, without claiming the entire task is complete.'

    service.execute = execute
    worker = asyncio.create_task(service.worker())
    try:
        await wait_settled(service, jid, 'succeeded')
        assert store.get(jid)['outcome'] == outcome
    finally:
        await stop_worker(worker)


def test_restart_marks_open_runs_interrupted_only_when_owner_recovers(setup):
    store, account, session, skill = setup
    jid = enqueue(store, account, session)
    old = store.start_run(jid)
    snapshot = store.capture_skills(jid)
    store.save_checkpoint(jid, [{'role': 'user', 'content': 'preserved pending request'}])
    reopened = Store(store.path, skills_root=skill.parent)
    try:
        live = reopened.session_runs(account['id'], session['id'], jid)
        assert live[0]['status'] == 'running' and live[0]['ended_at'] is None
        assert reopened.get(jid)['status'] == 'running'
        assert reopened.recover_interrupted() == [jid]
        recovered = reopened.session_runs(account['id'], session['id'], jid)
        assert recovered[0]['id'] == old['id'] and recovered[0]['status'] == 'interrupted'
        assert recovered[0]['ended_at'] is not None and reopened.get(jid)['status'] == 'queued'
        second = reopened.start_run(jid)
        assert second['id'] != old['id'] and second['attempt'] == 2
        assert second['task_id'] == old['task_id']
        reopened.update(jid, status='succeeded', result='finished')
        reopened.finish_run(second['id'])
        assert reopened.capture_skills(jid) == snapshot
        assert reopened.checkpoint(jid) == [{'role': 'user', 'content': 'preserved pending request'}]
        final = reopened.finish_run(second['id'])
        assert final == reopened.session_runs(account['id'], session['id'], jid)[1]
        assert reopened.recover_interrupted() == []
    finally:
        reopened.db.close()


async def test_graceful_shutdown_records_interruption_and_keeps_job_queued(setup):
    store, account, session, _ = setup
    service = make_service(store)
    jid = enqueue(store, account, session)
    entered = asyncio.Event()
    async def execute(job):
        entered.set()
        await asyncio.Event().wait()
    service.execute = execute
    worker = asyncio.create_task(service.worker())
    try:
        await asyncio.wait_for(entered.wait(), 2)
    finally:
        await stop_worker(worker)
    assert store.get(jid)['status'] == 'queued'
    runs = store.session_runs(account['id'], session['id'], jid)
    assert len(runs) == 1 and runs[0]['status'] == 'interrupted'
    assert runs[0]['ended_at'] is not None


async def test_cancel_before_run_coroutine_enters_does_not_leave_an_open_run(setup, monkeypatch):
    store, account, session, _ = setup
    service = make_service(store)
    jid = enqueue(store, account, session)
    service.execute = AsyncMock(return_value='resumed after early cancellation')
    create_task = asyncio.create_task
    cancelled_first = False
    def cancel_first_admitted_run(coroutine, *args, **kwargs):
        nonlocal cancelled_first
        task = create_task(coroutine, *args, **kwargs)
        if not cancelled_first and getattr(getattr(coroutine, 'cr_code', None), 'co_name', None) == 'run_job':
            cancelled_first = True
            task.cancel()  # Python will not enter this coroutine's finally block.
        return task
    monkeypatch.setattr(asyncio, 'create_task', cancel_first_admitted_run)
    worker = asyncio.create_task(service.worker())
    try:
        await wait_settled(service, jid, 'succeeded')
        runs = store.session_runs(account['id'], session['id'], jid)
        assert [r['status'] for r in runs] == ['interrupted', 'succeeded']
        assert [r['attempt'] for r in runs] == [1, 2]
        assert all(r['ended_at'] is not None for r in runs)
        service.execute.assert_awaited_once()
    finally:
        await stop_worker(worker)


@pytest.mark.parametrize('finish', ['succeeded', 'failed', 'cancelled'])
async def test_worker_records_terminal_run_outcome(setup, finish):
    store, account, session, _ = setup
    service = make_service(store)
    jid = enqueue(store, account, session)
    entered = asyncio.Event()
    async def execute(job):
        entered.set()
        if finish == 'failed':
            raise RuntimeError('controlled failure')
        if finish == 'cancelled':
            await asyncio.Event().wait()
        return 'finished'
    service.execute = execute
    worker = asyncio.create_task(service.worker())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        if finish == 'cancelled':
            await service.cancel_job(jid)
        await wait_settled(service, jid, finish)
        runs = store.session_runs(account['id'], session['id'], jid)
        assert len(runs) == 1 and runs[0]['status'] == finish
        assert runs[0]['ended_at'] >= runs[0]['started_at']
    finally:
        await stop_worker(worker)


async def test_unexpected_failure_preserves_code_locations_without_private_body(setup, caplog):
    store, account, session, _ = setup
    service = make_service(store)
    jid = enqueue(store, account, session)
    private_body = 'private-prompt-and-credential-should-never-be-logged'
    store.db.execute('INSERT INTO pi_tools(job_id,user_id,name,args,result,error,created) VALUES (?,?,?,?,?,?,?)',
                     (jid, account['user_id'], 'web_read', '{}', 'null', 'earlier handled tool error', 0))
    store.db.commit()

    async def failing_operation(job):
        raise ValueError(private_body)

    service.execute = failing_operation
    run = store.start_run(jid)
    job = {**store.get(jid), 'run_id': run['id']}
    await service.run_job(job)
    event = json.loads(store.db.execute(
        "SELECT payload FROM runtime_events WHERE job_id=? AND kind='run_error'", (jid,)).fetchone()[0])
    assert event['type'] == 'ValueError'
    assert event['frames'][-1]['function'] == 'failing_operation'
    assert event['frames'][-1]['file'] == 'test_task_runs.py'
    assert event['frames'][-1]['line'] > 0
    assert 'failing_operation' in caplog.text
    assert private_body not in caplog.text + json.dumps(event) + store.get(jid)['result']
    assert 'ValueError' in store.get(jid)['result']
    assert 'earlier handled tool error' not in store.get(jid)['result']
    assert store.get(jid)['status'] == 'failed'
    assert store.session_runs(account['id'], session['id'], jid)[0]['status'] == 'failed'


def test_run_lookup_is_account_and_session_scoped_and_duplicate_admission_rejected(setup):
    store, account, session, _ = setup
    jid = enqueue(store, account, session)
    run = store.start_run(jid)
    with pytest.raises(ValueError):
        store.start_run(jid)
    assert store.get(jid)['attempt'] == 1
    other = store.create_session(account['id'], 'Other')
    friend = store.create_account('Friend')
    foreign = store.create_session(friend['id'], 'Foreign')
    for aid, sid in ((account['id'], other['id']), (friend['id'], session['id']), (friend['id'], foreign['id'])):
        with pytest.raises(PermissionError):
            store.session_runs(aid, sid, jid)
    assert store.session_runs(account['id'], session['id'], jid)[0]['id'] == run['id']


def test_task_link_and_absorbed_input_keep_existing_run_ownership_current(setup):
    store, account, session, _ = setup
    first = enqueue(store, account, session, 'initial request')
    one = store.start_run(first)
    store.update(first, status='succeeded', result='first result')
    store.finish_run(one['id'])
    second = enqueue(store, account, session, 'follow-up')
    two = store.start_run(second)
    store.link_task(store.get(second), first)
    assert store.session_runs(account['id'], session['id'], first)[1]['task_id'] == first
    store.update(second, status='succeeded', result='follow-up result')
    store.finish_run(two['id'])
    third = enqueue(store, account, session, 'supplement')
    three = store.start_run(third)
    store.update(third, status='queued')
    store.finish_run(three['id'])
    store.absorb_input(store.get(second), store.get(third))
    runs = store.session_runs(account['id'], session['id'], first)
    assert {r['id'] for r in runs} == {one['id'], two['id'], three['id']}
    assert all(r['task_id'] == first and r['session_id'] == session['id'] for r in runs)
    assert [r['attempt'] for r in runs] == [1, 1, 1]  # Attempts belong to each job.


def test_legacy_attempt_counts_do_not_create_fictional_history(setup):
    store, account, session, _ = setup
    jid = enqueue(store, account, session)
    store.update(jid, status='running', attempt=7)
    assert store.session_runs(account['id'], session['id'], jid) == []
    assert store.recover_interrupted() == [jid]
    assert store.session_runs(account['id'], session['id'], jid) == []
    real = store.start_run(jid)
    assert real['attempt'] == 8
    assert len(store.session_runs(account['id'], session['id'], jid)) == 1
