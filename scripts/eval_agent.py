"""Run the agent evaluation set against an isolated database.

    .venv/bin/python scripts/eval_agent.py --agent v1            # current Pi harness
    .venv/bin/python scripts/eval_agent.py --agent v2 --tier all  # new agent loop
    .venv/bin/python scripts/eval_agent.py --case weather_lookup

Stop the resident service first: this starts its own local model server and the
GPU lock is exclusive. Nothing is sent to Telegram or the App. Approvals are
denied (v2 only confirms schedule creation, which just writes a row here), so no
tool can act on this Mac outside the evaluation workspace.
Results land in runtime/evals/<agent>-<timestamp>/ (results.json + summary.md).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.agent_cases import CASES  # noqa: E402
from local_ai.attachments import Attachments  # noqa: E402
from local_ai.engine import Engine, ROOT, RUNTIME  # noqa: E402
from local_ai.store import Store  # noqa: E402

PIXEL_PNG = bytes.fromhex(
    '89504e470d0a1a0a0000000d4948445200000001000000010806000000'
    '1f15c4890000000d49444154789c6360f8cfc0000003010100c9fe92ef0000000049454e44ae426082')


class Turn:
    def __init__(self, store, job, answer, seconds, account, sessions):
        self.store, self.job, self.answer, self.seconds = store, job, answer or '', seconds
        self.account, self.sessions = account, sessions
        self.calls = [{'name': row['name'], 'arguments': json.loads(row['arguments']), 'status': row['status']}
                      for row in store.db.execute(
                          'SELECT name,arguments,status FROM tool_operations WHERE job_id=? ORDER BY started_at',
                          (job['id'],))]

    def file(self, name):
        path = self.store.workspace_directory(self.account['id'], self.job['session_id']) / name
        return path.read_text(errors='replace') if path.is_file() else ''

    def jobs(self, kind):
        ids = [s['id'] for s in self.sessions.values()]
        return [dict(row) for row in self.store.db.execute(
            f"SELECT * FROM jobs WHERE kind=? AND session_id IN ({','.join('?' * len(ids))})", (kind, *ids))]


def make_agent(name, engine, store, root):
    deny = lambda *args, **kwargs: asyncio.sleep(0, result=False)  # noqa: E731
    if name == 'v1':
        from local_ai.pi_agent import PiAgent
        return PiAgent(engine, store, lambda _: True, root, approve=deny)
    from local_ai.agent.loop import Agent

    async def schedules_only(job, description, scope=None, *, purpose='computer'):
        # Creating a schedule only writes a row in this isolated database; nothing runs it.
        return purpose == 'schedule'
    return Agent(engine, store, root, approve=schedules_only)


async def run_case(case, agent, store, root):
    account = store.create_account('eval ' + case['name'], is_admin=True)
    sessions = {}

    def session(name):
        if name not in sessions:
            sessions[name] = store.create_session(account['id'], f"{case['name']} · {name}")
        return sessions[name]

    if case.get('seed_image'):
        sid = session('main')['id']
        jid = store.enqueue(account['user_id'], None, 'image', '一只坐在窗边的橘猫', session_id=sid, channel='app')
        image = root / 'seed' / f'{jid}.png'
        image.parent.mkdir(exist_ok=True)
        image.write_bytes(PIXEL_PNG)
        store.update(jid, status='succeeded', result=str(image), delivered=1, delivery_state='delivered')

    turns = []
    for spec in case['turns']:
        sid = session(spec.get('session', 'main'))['id']
        jid = store.enqueue(account['user_id'], None, 'chat', spec['prompt'], session_id=sid, channel='app')
        store.update(jid, status='running')
        if case.get('attachment') and not turns:
            name, text = case['attachment']
            source = root / 'seed' / name
            source.parent.mkdir(exist_ok=True)
            source.write_text(text)
            Attachments(store, store.get(jid), root).add(source, name)
        started = time.monotonic()
        row = {'prompt': spec['prompt'], 'job_id': jid, 'review': bool(spec.get('review'))}
        try:
            answer = await agent.run(store.get(jid))
            store.update(jid, status='succeeded', result=answer)
            turn = Turn(store, store.get(jid), answer, time.monotonic() - started, account, sessions)
            row['checks'] = {}
            for key, check in spec['checks'].items():
                try:
                    row['checks'][key] = bool(check(turn))
                except Exception as exc:  # a broken check is a failed check, not a crashed run
                    row['checks'][key] = False
                    row.setdefault('check_errors', {})[key] = repr(exc)
            row.update(answer=answer, seconds=round(turn.seconds, 1), tools=[c['name'] + ':' + c['status'] for c in turn.calls],
                       outcome=store.get(jid).get('outcome'), passed=all(row['checks'].values()))
        except Exception as exc:
            store.update(jid, status='failed', error=str(exc))
            row.update(seconds=round(time.monotonic() - started, 1), passed=False, error=str(exc),
                       traceback=traceback.format_exc())
        turns.append(row)
        print(json.dumps({'case': case['name'], **{k: row.get(k) for k in ('prompt', 'seconds', 'passed', 'tools', 'checks', 'error')}},
                         ensure_ascii=False), flush=True)
    return {'name': case['name'], 'tier': case['tier'], 'source': case['source'], 'turns': turns,
            'passed': all(t['passed'] for t in turns)}


def summary(results, agent, folder):
    lines = [f'# Agent 评测 · {agent}', '', f'结果目录：`{folder}`', '']
    for tier in ('core', 'v2'):
        rows = [r for r in results if r['tier'] == tier]
        if not rows:
            continue
        turns = [t for r in rows for t in r['turns']]
        checks = [v for t in turns for v in t.get('checks', {}).values()]
        seconds = [t['seconds'] for t in turns]
        lines += [f'## {tier}', '',
                  f'- 用例通过：{sum(r["passed"] for r in rows)}/{len(rows)}',
                  f'- 检查项通过：{sum(checks)}/{len(checks)}',
                  f'- 每轮平均耗时：{sum(seconds) / len(seconds):.1f} 秒', '',
                  '| 用例 | 结果 | 未通过的检查 | 耗时（秒） | 需人工复核 |', '| --- | --- | --- | --- | --- |']
        for r in rows:
            failed = [f'{i + 1}.{k}' for i, t in enumerate(r['turns']) for k, v in t.get('checks', {}).items() if not v]
            failed += [f'{i + 1}.error' for i, t in enumerate(r['turns']) if t.get('error')]
            lines.append(f"| {r['name']} | {'通过' if r['passed'] else '未通过'} | {', '.join(failed) or '—'} | "
                         f"{sum(t['seconds'] for t in r['turns']):.0f} | {'是' if any(t['review'] for t in r['turns']) else ''} |")
        lines.append('')
    return '\n'.join(lines)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--agent', choices=('v1', 'v2'), default='v1')
    parser.add_argument('--tier', choices=('core', 'v2', 'all'), default='all')
    parser.add_argument('--case', action='append', help='only run these case names')
    args = parser.parse_args()
    cases = [c for c in CASES if (args.tier == 'all' or c['tier'] == args.tier) and (not args.case or c['name'] in args.case)]
    folder = RUNTIME / 'evals' / f'{args.agent}-{time.strftime("%Y%m%d-%H%M%S")}'
    folder.mkdir(parents=True)
    store = Store(folder / 'eval.sqlite3', skills_root=ROOT / 'assistant-skills')
    engine = Engine()
    agent = make_agent(args.agent, engine, store, folder)
    results = []
    try:
        for case in cases:
            results.append(await run_case(case, agent, store, folder))
            (folder / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
    finally:
        (folder / 'summary.md').write_text(summary(results, args.agent, folder))
        await engine.stop()
        store.db.close()
    print('SUMMARY ' + str(folder / 'summary.md'), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
