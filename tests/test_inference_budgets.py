"""Cross-runtime budget contracts for measured local generation and prefill.

These validate deadline wiring, not a throughput guarantee or a promise that
every optional repair can fit inside the host's total execution allowance.
"""
import ast
import asyncio
import inspect
import json
import re
import textwrap
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import local_ai.pi_agent as pi_module
from local_ai import outcomes
from local_ai.engine import Engine, ROOT
from local_ai.pi_agent import PiAgent
from local_ai.store import Store


def runner_budgets():
    source = (ROOT/'pi/runner-v2.mjs').read_text()
    return source, {name: int(value) for name, value in re.findall(r'\b(MAX_\w+)\s*=\s*(\d+)', source)}


def host_timeout():
    tree = ast.parse(textwrap.dedent(inspect.getsource(PiAgent.run)))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == 'timeout'
             and isinstance(node.func.value, ast.Name) and node.func.value.id == 'asyncio']
    assert len(calls) == 1, 'One host deadline must govern the child process'
    return ast.literal_eval(calls[0].args[0])


def test_output_budgets_fit_local_throughput_and_provider_deadlines():
    source, budgets = runner_budgets()
    # Both the model descriptor and actual provider request must use the cap.
    assert set(map(int, re.findall(r'\bmaxTokens\s*:\s*(\d+)', source))) == {3072}
    stages = [
        (3072, budgets['MAX_MODEL_MS']/1000),
        (outcomes.CONTRACT_MAX_TOKENS, outcomes.CONTRACT_TIMEOUT_SECONDS),
        (outcomes.REVIEW_MAX_TOKENS, outcomes.REVIEW_TIMEOUT_SECONDS),
    ]
    assert stages == [(3072, 300), (2048, 180), (4096, 300)]
    transport = ast.parse(textwrap.dedent(inspect.getsource(Engine.chat)))
    ceilings = [ast.literal_eval(keyword.value) for node in ast.walk(transport)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'ClientTimeout' for keyword in node.keywords
                if keyword.arg == 'total']
    assert ceilings == [900]
    for tokens, timeout in stages:
        assert tokens/15 + 20 <= timeout
        assert timeout < ceilings[0]
    # Recent production generation was 14.2 tokens/s with 38s of prefill.
    # The former 240s abort discarded a response still making normal progress.
    assert 3072/14.2 + 38 < budgets['MAX_MODEL_MS']/1000
    assert 'AbortSignal.timeout(MAX_MODEL_MS)' in source


def test_host_total_preserves_its_ceiling_and_existing_operation_retry_limits():
    _, budgets = runner_budgets()
    assert budgets == {'MAX_WORK_MS': 900000, 'MAX_MODEL_MS': 300000,
                       'MAX_CALLS': 48, 'MAX_TURNS': 24, 'MAX_REVIEWS': 3,
                       'MAX_RECOVERIES': 2}
    # Work may cross its boundary inside a model turn. A contract and one
    # review still fit afterwards. Optional finalization/review retries all
    # remain subject to the host's single ceiling; they are not guaranteed.
    reserved = (budgets['MAX_WORK_MS']/1000 + budgets['MAX_MODEL_MS']/1000
                + outcomes.CONTRACT_TIMEOUT_SECONDS + outcomes.REVIEW_TIMEOUT_SECONDS)
    assert reserved == 1680
    assert host_timeout() == 1800
    assert host_timeout()-reserved == 120


@pytest.mark.asyncio
async def test_actual_contract_and_review_use_their_output_and_wait_budgets(tmp_path, monkeypatch):
    store = Store(tmp_path/'db')
    jid = store.enqueue(1, 1, 'chat', '解释一个概念')
    job = store.get(jid)
    raw_contract = {'deliverables': [{'description': '解释概念', 'evidence': 'reasoning',
                     'proof_kind': 'reasoning', 'source_job_ids': [jid]}], 'known_missing_inputs': []}
    # Deliberately stop at review output validation: the two real model-call
    # sites still execute, while semantic verdict tests live in their own suite.
    engine = SimpleNamespace(chat=AsyncMock(side_effect=[json.dumps(raw_contract), ValueError('test review output')]))
    waits = []
    async def capture_wait(awaitable, timeout):
        waits.append(timeout)
        return await awaitable
    monkeypatch.setattr(asyncio, 'wait_for', capture_wait)
    try:
        result = await outcomes.OutcomeReviewer(engine, store).review(job, '已有候选正文')
        assert waits == [180, 300]
        assert [call.kwargs['max_tokens'] for call in engine.chat.await_args_list] == [2048, 4096]
        assert result['review_stage'] == 'review'
        assert result['outcome'] == 'partial' and result['missing_user_inputs'] == []
        assert result['answer'] == '已有候选正文'
    finally:
        store.db.close()


@pytest.mark.asyncio
async def test_host_execution_deadline_reaps_node_and_preserves_checkpoint(tmp_path, monkeypatch):
    (tmp_path/'vendor').mkdir()
    (tmp_path/'pi').mkdir()
    (tmp_path/'vendor/node').symlink_to((ROOT/'vendor/node').resolve())
    runner = tmp_path/'pi/runner.mjs'
    runner.write_text('process.stdin.resume(); setInterval(()=>{},1000);')
    monkeypatch.setattr(pi_module, 'ROOT', tmp_path)
    store = Store(tmp_path/'db')
    jid = store.enqueue(1, 1, 'chat', '保留已经取得的事实')
    checkpoint = [{'role': 'user', 'content': '已经保存的输入'}]
    store.save_checkpoint(jid, checkpoint)
    engine = SimpleNamespace(lock=asyncio.Lock(), start=AsyncMock(), stop=AsyncMock())
    original_spawn, original_timeout = asyncio.create_subprocess_exec, asyncio.timeout
    children, timeouts = [], []
    async def capture_spawn(*args, **kwargs):
        child = await original_spawn(*args, **kwargs)
        children.append(child)
        return child
    def accelerated_timeout(seconds):
        timeouts.append(seconds)
        return original_timeout(.05)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', capture_spawn)
    monkeypatch.setattr(asyncio, 'timeout', accelerated_timeout)
    try:
        agent = PiAgent(engine, store, lambda _: True, tmp_path, runner=runner)
        with pytest.raises(TimeoutError):
            await agent.run(store.get(jid))
        assert timeouts == [1800]
        assert len(children) == 1 and children[0].returncode is not None
        assert store.checkpoint(jid) == checkpoint
        assert store.get(jid)['outcome'] != 'needs_input'
        engine.start.assert_not_awaited()
        engine.stop.assert_not_awaited()
    finally:
        store.db.close()
