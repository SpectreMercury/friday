"""Completed execution is authoritative over a stale model progress plan."""
import json

import pytest

from local_ai.pi_agent import task_execution_state
from local_ai.store import Store


@pytest.mark.parametrize('status,outcome,pending', [
    ('succeeded', 'completed', False),
    ('running', 'completed', True),
    ('running', None, True),
    ('succeeded', 'partial', True),
    ('succeeded', 'pending', True),
    ('succeeded', 'needs_input', True),
])
def test_plan_origin_completion_overrides_only_stale_progress(tmp_path, status, outcome, pending):
    store = Store(tmp_path/'db')
    try:
        previous = store.enqueue(1, 1, 'chat', 'Calculate and save the total')
        plan = {'goal': 'Calculate and save the total', 'mode': 'action', 'work_items': [
            {'id': 'save', 'description': 'Save the result', 'status': 'pending'}]}
        store.revise_plan(store.get(previous), plan)
        store.update(previous, status=status, outcome=outcome)
        current = store.enqueue(1, 1, 'chat', 'Explain that result')
        store.link_task(store.get(current), previous)
        # The linked job's completion must not stand in for the plan producer.
        store.update(current, status='succeeded', outcome='completed')
        job = store.get(current)
        before = store.task_facts(job)
        state = task_execution_state(store, job)
        assert state == {'task_id': previous, 'already_linked': True, 'pending_plan_work': pending}
        assert store.task_facts(job) == before
        assert before['plans'][-1]['work_items'][0]['status'] == 'pending'
        # A newer plan from another execution becomes authoritative again.
        store.update(current, status='running', outcome=None)
        store.revise_plan(store.get(current), plan)
        assert task_execution_state(store, job)['pending_plan_work'] is True
    finally:
        store.db.close()


@pytest.mark.parametrize('foreign_scope', ['account', 'session'])
def test_foreign_latest_plan_cannot_clear_owned_pending_work(tmp_path, foreign_scope):
    store = Store(tmp_path/'db')
    try:
        own = store.enqueue(1, 1, 'chat', 'Owned task')
        job = store.get(own)
        plan = {'goal': 'Owned work', 'mode': 'action', 'work_items': [
            {'id': 'remaining', 'description': 'Finish owned work', 'status': 'pending'}]}
        store.revise_plan(job, plan)
        other = store.enqueue(2, 2, 'chat', 'Foreign task')
        foreign = store.get(other)
        # Simulate an inconsistent shared task ID: every row still has to pass
        # BOTH ownership boundaries before influencing execution controls.
        with store.db:
            store.db.execute("UPDATE jobs SET task_id=?,account_id=?,session_id=?,status='succeeded',outcome='completed' WHERE id=?",
                             (own, foreign['account_id'] if foreign_scope == 'account' else job['account_id'],
                              foreign['session_id'] if foreign_scope == 'session' else job['session_id'], other))
            store.db.execute('INSERT INTO plans(task_id,job_id,content,created) VALUES (?,?,?,?)',
                             (own, other, json.dumps(plan), 0))
        assert task_execution_state(store, job)['pending_plan_work'] is True
    finally:
        store.db.close()
