import copy
import json

import pytest

from local_ai.execution import validate_plan
from local_ai.pi_agent import UserTools
from local_ai.store import Store
from local_ai.tool_runtime import ToolRuntime


def plan():
    return {'goal': 'Calculate, save, and read back the requested total', 'mode': 'action',
            'work_items': [
                {'id': 'calculate', 'description': 'Calculate the total', 'status': 'in_progress',
                 'done_when': 'The calculator returns the exact total'},
                {'id': 'save', 'description': 'Save the result', 'status': 'pending',
                 'depends_on': ['calculate'], 'done_when': 'The requested file is written'},
                {'id': 'verify', 'description': 'Read the file back', 'status': 'pending',
                 'depends_on': ['save'], 'done_when': 'The file contains the calculated total'},
            ]}


@pytest.fixture
def task(tmp_path):
    store = Store(tmp_path / 'db')
    jid = store.enqueue(1, 1, 'chat', 'Calculate 17*23.5*.85, save and verify the result.')
    job = store.get(jid)
    tools = UserTools(store, job, lambda _: True, tmp_path / 'notes')
    yield store, job, tools
    store.db.close()


async def test_plan_persists_dependency_progress_and_actual_receipts(task):
    store, job, tools = task
    runtime = ToolRuntime(store, job, tools.registry(), lambda _: True)
    first = await runtime.call('plan-1', 'plan_task', plan())
    assert first['result']['progress']['in_progress'] == ['calculate']
    assert first['result']['progress']['waiting_dependencies'] == ['save', 'verify']
    result = await runtime.call('sum', 'calculate', {'expression': '17*23.5*.85'})
    assert result['result']['value'] == '339.575'
    revised = plan()
    revised['work_items'][0].update(status='completed', evidence_refs=[f"operation:{job['id']}:sum"])
    revised['work_items'][1]['status'] = 'in_progress'
    second = await runtime.call('plan-2', 'plan_task', revised)
    assert second['result']['revision'] == 2
    assert second['result']['progress']['in_progress'] == ['save']
    assert second['result']['progress']['completed'] == ['calculate']
    assert store.task_facts(job)['user_requests'][0]['text'] == job['prompt']
    assert store.get(job['id'])['outcome'] is None, 'updating a plan must never complete the user task'
    reopened = Store(store.path)
    try:
        assert reopened.task_facts(job)['plan_progress'] == second['result']['progress']
    finally:
        reopened.db.close()


@pytest.mark.parametrize('change', ['cycle', 'missing_dependency', 'duplicate_id',
                                    'skipped_dependency', 'empty_description'])
def test_rejects_incoherent_decomposition(change):
    value = plan()
    if change == 'cycle':
        value['work_items'][0].update(status='pending', depends_on=['verify'])
    elif change == 'missing_dependency':
        value['work_items'][1]['depends_on'] = ['imaginary']
    elif change == 'duplicate_id':
        value['work_items'][1]['id'] = 'calculate'
    elif change == 'skipped_dependency':
        value['work_items'][2]['status'] = 'completed'
    else:
        value['work_items'][0]['description'] = '   '
    with pytest.raises(ValueError):
        validate_plan(value)


def test_independent_background_steps_can_be_active_together():
    value = plan()
    value['work_items'][1].update(status='in_progress', depends_on=[])
    assert len([item for item in validate_plan(value)['work_items'] if item['status'] == 'in_progress']) == 2


async def test_plan_refuses_foreign_failed_and_fabricated_evidence_atomically(task):
    store, job, tools = task
    foreign_id = store.enqueue(2, 2, 'chat', 'private')
    foreign_tools = UserTools(store, store.get(foreign_id), lambda _: True)
    foreign_runtime = ToolRuntime(store, store.get(foreign_id), foreign_tools.registry(), lambda _: True)
    await foreign_runtime.call('sum', 'calculate', {'expression': '1+1'})
    own_runtime = ToolRuntime(store, job, tools.registry(), lambda _: True)
    await own_runtime.call('bad', 'calculate', {'expression': '1/0'})
    for ref in (f'operation:{foreign_id}:sum', f"operation:{job['id']}:bad", 'artifact:invented'):
        value = plan()
        value['work_items'][0].update(status='completed', evidence_refs=[ref])
        with pytest.raises(ValueError, match='真实结果'):
            await tools.execute('plan_task', value)
    assert store.db.execute('SELECT count(*) FROM plans').fetchone()[0] == 0


async def test_legacy_plan_and_simple_task_do_not_require_new_planning_protocol(task):
    store, job, tools = task
    legacy = {'goal': 'Read and answer', 'mode': 'research', 'steps': ['Read source', 'Answer']}
    original = copy.deepcopy(legacy)
    result = await tools.execute('plan_task', legacy)
    assert legacy == original and result['steps'] == original['steps']
    assert 'progress' not in result
    assert (await tools.execute('calculate', {'expression': '1+1'}))['value'] == '2'
    assert json.loads(store.db.execute('SELECT content FROM plans').fetchone()[0]) == legacy
