"""Real local model, real Pi/SQLite/tools, synthetic inputs, no delivery transport.

Stop the resident bot before running: Engine owns the exclusive inference lock.
Reports are retained under runtime; production conversations are never copied.
"""
import asyncio
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_ai.engine import Engine, ROOT
from local_ai.pi_agent import PiAgent, UserTools
from local_ai.runtime_build import BUILD_ID, source_fingerprint
from local_ai.store import Store


CASES = ('correction_after_completed_action', 'file_observation',
         'calculation_and_file_effect', 'async_handoff_without_polling',
         'truthful_missing_access', 'advice_only_needs_no_access')


async def main(selected=None):
    folder = ROOT / 'runtime' / ('conversation-validation-' + str(time.time_ns()))
    folder.mkdir(parents=True)
    store, engine = Store(folder / 'validation.sqlite3'), Engine()
    agent = PiAgent(engine, store, lambda _: True, folder)
    results = []

    async def run(case, uid, prompt, prepare=None, verify=None):
        if selected and case not in selected:
            return
        jid = store.enqueue(uid, uid, 'chat', prompt)
        if prepare:
            prepare(jid)
        store.update(jid, status='running')
        started = time.monotonic()
        try:
            answer = await asyncio.wait_for(agent.run(store.get(jid)), 240)
            store.update(jid, status='succeeded', result=answer)
            row = store.get(jid)
            tools = [r[0] for r in store.db.execute('SELECT name FROM pi_tools WHERE job_id=?', (jid,))]
            if verify:
                verify(jid, answer, row, tools)
            result = {'case': case, 'passed': True, 'job_id': jid, 'outcome': row['outcome'],
                      'answer': answer, 'tools': tools, 'seconds': round(time.monotonic()-started, 2)}
        except Exception as exc:
            row = store.get(jid)
            result = {'case': case, 'passed': False, 'job_id': jid,
                      'error': type(exc).__name__, 'detail': str(exc)[:1200],
                      'outcome': row['outcome'], 'answer': row['result'],
                      'tools': [r[0] for r in store.db.execute('SELECT name FROM pi_tools WHERE job_id=?', (jid,))]}
        results.append(result)
        (folder / 'results.json').write_text(json.dumps({'build_id': BUILD_ID, 'results': results}, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False), flush=True)

    def completed(row, answer):
        assert row['outcome'] == 'completed', row['outcome']
        assert '可靠答复' not in answer and 'CONTROL_DRAFT' not in answer, answer

    try:
        # Seed explicitly synthetic completed history, including a rejected draft
        # and a host control message. Only the follow-up is real model execution.
        old = store.enqueue(801, 801, 'chat', '请生成一张纸船图片。')
        child = store.enqueue(801, 801, 'image', 'synthetic paper boat fixture', parent_job_id=old)
        artifact = folder / 'synthetic-artifact.txt'
        artifact.write_text('Synthetic completed artifact fixture, not a generated image.')
        store.update(child, status='succeeded', outcome='completed', result=str(artifact), delivery_state='delivered', delivered=1)
        store.record_receipt(child, 'media', 801, 1)
        store.update(old, status='succeeded', outcome='completed', result='图片已完成并交付。', delivered=1, delivery_state='delivered')
        store.save_checkpoint(old, [
            {'role':'user', 'source':'user', 'source_job_id':old, 'content':'请生成一张纸船图片。'},
            {'role':'assistant', 'content':[{'type':'text','text':'CONTROL_DRAFT_MUST_NOT_BE_REPLAYED'}]},
            {'role':'user', 'source':'harness_control', 'content':'独立核验尚有缺口，请继续查询历史任务状态。'},
        ])
        def correction(jid, answer, row, tools):
            completed(row, answer)
            assert '伦敦' in answer and '上海' in answer, answer
            assert not any(name in tools for name in ('generate_image','task_status')), tools
        await run('correction_after_completed_action', 801,
                  '更正：小林在伦敦，小周在上海。请只用一句话复述这两人的所在地。',
                  lambda jid: store.link_task(store.get(jid), old), correction)

        def prepare_file(jid):
            path = UserTools(store, store.get(jid), lambda _: True, folder/'workspaces').file_path('note.txt')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('项目代号：CEDAR-47\n验收值：26\n')
        def read_file(jid, answer, row, tools):
            completed(row, answer)
            assert 'read_file' in tools and 'CEDAR-47' in answer and '26' in answer, (answer, tools)
        await run('file_observation', 802, '请读取我的 note.txt，告诉我项目代号和验收值；如果无法读取，就说明缺少什么。', prepare_file, read_file)

        def written(jid, answer, row, tools):
            completed(row, answer)
            path = UserTools(store, store.get(jid), lambda _: True, folder/'workspaces').file_path('total.txt')
            assert path.is_file() and '391' in path.read_text(), answer
            assert tools.count('write_file') == 1 and '391' in answer, tools
        await run('calculation_and_file_effect', 803,
                  '计算17乘23，把数值保存到 total.txt，再告诉我文件名和数值；如果做不了，就说明哪一步受阻。', verify=written)

        def pending(jid, answer, row, tools):
            children = store.db.execute('SELECT kind,status FROM jobs WHERE parent_job_id=?', (jid,)).fetchall()
            assert len(children) == 1 and tuple(children[0]) == ('image','queued'), children
            assert row['outcome'] == 'pending' and tools.count('generate_image') == 1, (row['outcome'], tools)
            assert 'task_status' not in tools and '可靠答复' not in answer, answer
        await run('async_handoff_without_polling', 804, '请生成一张蓝色背景上的白色纸船图片。', verify=pending)

        def missing(jid, answer, row, tools):
            assert row['outcome'] == 'needs_input' and '可靠答复' not in answer, (row['outcome'], answer)
            assert tools.count('final_check') <= 2, tools
            review = json.loads(store.db.execute("SELECT result FROM pi_tools WHERE job_id=? AND name='final_check' ORDER BY id DESC LIMIT 1", (jid,)).fetchone()[0])
            # Independently found prerequisites must prevent false completion,
            # even when the first contract classified the mixed goal poorly.
            coverage = [entry for entry in review['request_coverage'] if entry['source_job_id'] == jid]
            assert len(coverage) == 1 and any(goal['missing_user_inputs'] for goal in coverage[0]['goals']), review
            assert review['accepted'] and not review['repair'], review
        await run('truthful_missing_access', 805,
                  '请查我公司内部系统的本月销售额。这个系统还没有接入，也没有提供数据，请说明还缺什么，不能编造数字。', verify=missing)

        def advisory(jid, answer, row, tools):
            completed(row, answer)
            assert tools.count('final_check') <= 2, tools
            assert not any(name in tools for name in ('generate_image', 'write_file', 'execute_code')), tools
        await run('advice_only_needs_no_access', 806,
                  '先不要查询系统，只告诉我以后查询公司内部月销售额通常需要准备什么。', verify=advisory)
    finally:
        await engine.stop()
        store.db.close()
    stable = source_fingerprint() == BUILD_ID
    report = {'build_id':BUILD_ID, 'source_unchanged':stable, 'results':results,
              'production_data_used':False, 'messages_sent':False}
    (folder/'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print('REPORT ' + str(folder/'results.json'), flush=True)
    return stable and len(results) == len(selected or CASES) and all(item['passed'] for item in results)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=CASES, action='append', dest='cases')
    args = parser.parse_args()
    sys.exit(0 if asyncio.run(main(set(args.cases) if args.cases else None)) else 1)
