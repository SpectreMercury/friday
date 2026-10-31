import pytest

from local_ai.pi_agent import UserTools
from local_ai.store import Store
from local_ai.tool_runtime import ToolRuntime


@pytest.mark.asyncio
async def test_current_execution_status_cannot_be_waited_on(tmp_path):
    store = Store(tmp_path / 'db')
    try:
        previous = store.enqueue(1, 1, 'chat', 'Previous request')
        store.update(previous, status='succeeded', outcome='completed')
        jid = store.enqueue(1, 1, 'chat', 'Correcting the context')
        store.update(jid, status='running')
        tools = UserTools(store, store.get(jid), lambda _: True, tmp_path / 'workspace')
        for args in ({}, {'job_id': jid}):
            status = await tools.execute('task_status', args)
            assert status['is_current_execution'] is True
            assert status['awaitable'] is False
            assert '不要等待自己完成' in status['hint']
        historical = await tools.execute('task_status', {'job_id': previous})
        assert historical['status'] == 'succeeded'
        assert not historical.get('is_current_execution')
        runtime = ToolRuntime(store, store.get(jid), tools.registry(), lambda _: True)
        responses = [(await runtime.call(f'status-{i}', 'task_status', {}))['result'] for i in range(4)]
        assert all(response == responses[0] for response in responses)
        assert store.db.execute("SELECT count(*) FROM tool_operations WHERE job_id=? AND name='task_status'", (jid,)).fetchone()[0] == 4
    finally:
        store.db.close()
