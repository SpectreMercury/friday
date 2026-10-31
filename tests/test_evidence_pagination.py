"""Regression coverage for evidence reads feeding their own output back in."""
import json
import subprocess

import pytest

from local_ai.capabilities import BUILTINS
from local_ai.engine import ROOT
from local_ai.pi_agent import UserTools
from local_ai.store import Store
from local_ai.tool_runtime import ToolRuntime


@pytest.mark.asyncio
async def test_evidence_pages_finish_without_embedding_their_own_journal(tmp_path):
    store = Store(tmp_path / 'db')
    try:
        jid = store.enqueue(11, 11, 'chat', 'Read the recorded evidence.')
        job = store.get(jid)
        tools = UserTools(store, job, lambda uid: uid == 11, tmp_path / 'users')
        runtime = ToolRuntime(store, job, tools.registry(), lambda uid: uid == 11)
        spec = next(spec for spec in BUILTINS if spec.name == 'web_read')
        store.begin_operation(jid, 'source-read', spec, {'url': 'https://example.test/source'})
        source_result = {'text': 'SOURCE START ' + 'synthetic evidence ' * 1600 + ' SOURCE END'}
        store.finish_operation(jid, 'source-read', result=source_result)

        pages = []
        offsets = []
        offset = 0
        for index in range(8):
            outcome = await runtime.call(f'evidence-{index}', 'task_evidence', {'job_id': jid, 'offset': offset})
            assert outcome['failure'] is None
            page = outcome['result']
            assert page['job_id'] == jid
            assert page['offset'] == offset
            offsets.append(offset)
            pages.append(page)
            if page['next_offset'] is None:
                break
            assert page['next_offset'] > offset
            offset = page['next_offset']
        else:
            pytest.fail('evidence pagination never reached the end')

        assert len(pages) >= 3
        assert len({page['total_characters'] for page in pages}) == 1
        assert len(offsets) == len(set(offsets))
        complete = ''.join(page['text'] for page in pages)
        assert len(complete) == pages[0]['total_characters']
        exported = json.loads(complete)
        assert len(exported['operations']) == 1
        assert exported['operations'][0]['name'] == 'web_read'
        assert json.loads(exported['operations'][0]['result']) == source_result
        reread = await runtime.call('evidence-reread', 'task_evidence', {'job_id': jid, 'offset': 0})
        assert reread['result'] == pages[0], 'reading evidence must not change the evidence source'
        count = store.db.execute("SELECT count(*) FROM tool_operations WHERE job_id=? AND name='task_evidence'", (jid,)).fetchone()[0]
        assert count == len(pages) + 1, 'the complete read audit remains in the journal'
    finally:
        store.db.close()


def test_node_context_preserves_evidence_pagination():
    result = subprocess.run([str(ROOT / 'vendor/node'), '--test', str(ROOT / 'pi/context.test.mjs'), str(ROOT / 'pi/progress.test.mjs')],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
