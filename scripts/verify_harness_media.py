"""Live, isolated Harness acceptance: tool use, review/resume, two real clips.

Run with the resident service stopped. This never reads production conversations
or sends messages. Test-only approvals are recorded in its own SQLite database.
Default clips use production resolution/steps and an explicit four-second brief;
this is not a claim that a full-length drama passed visual acceptance.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path

from PIL import Image

from local_ai.engine import Engine, ROOT, RUNTIME
from local_ai.harness import HarnessService
from local_ai.store import Store
from local_ai.video_export import FFMPEG, assemble_project, command, duration


BRIEFS = [
    'A bright red paper boat floats from left to right across a calm blue pond. '
    'Close low side view, daylight, ripples follow the boat. A single continuous '
    'four-second shot. Water sounds only, no people, no text, no music.',
    'A small yellow toy excavator scoops dry brown sand into a bucket in a sandbox. '
    'Close side view, daylight, the arm visibly lifts the bucket. A single continuous '
    'four-second shot. Soft mechanical sounds only, no people, no text, no music.',
]


async def run_job(service, store, jid):
    run = store.start_run(jid)
    job = store.get(jid)
    job['run_id'] = run['id']
    await service.run_job(job)
    return store.get(jid)


async def main():
    folder = RUNTIME / 'harness-media-acceptance' / str(time.time_ns())
    folder.mkdir(parents=True)
    store = Store(folder / 'acceptance.sqlite3', skills_root=ROOT / 'assistant-skills')
    engine = Engine()
    account = store.create_account('Isolated acceptance fixture')
    session = store.create_session(account['id'], 'Live media acceptance', skills=['h3-prompt-writing'])
    service = HarnessService(folder, store=store, engine=engine, configured=[account['user_id']])
    report = {'build_id': service.build_id, 'folder': str(folder), 'clips': [],
              'scope': 'Real local chat/tool execution and two 4-second production-profile video renders; test-only review decisions; no external delivery.'}

    def save():
        (folder / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))

    try:
        print(json.dumps({'stage': 'started', 'folder': str(folder)}), flush=True)
        chat_id = store.enqueue(account['user_id'], None, 'chat',
            '这是隔离的软件验收。请使用 calculate 工具计算 4.6 - 4.3，并判断 4.3 < 4.6 - 0.3 是否成立。直接回答结果，不需要联网。',
            session_id=session['id'], channel='app')
        chat_started = time.monotonic()
        chat = await run_job(service, store, chat_id)
        calls = [dict(row) for row in store.db.execute('SELECT name,result,error FROM pi_tools WHERE job_id=?', (chat_id,))]
        report['chat'] = {'status': chat['status'], 'outcome': chat['outcome'], 'reply': chat['result'], 'tools': calls,
                          'elapsed_seconds': round(time.monotonic() - chat_started, 3)}
        save()
        assert chat['status'] == 'succeeded' and chat['outcome'] == 'completed', f"Chat acceptance failed; inspect {folder / 'report.json'}"
        values = {''.join(json.loads(row['result'])['expression'].split()): json.loads(row['result'])['value']
                  for row in calls if row['name'] == 'calculate' and not row['error']}
        assert values.get('4.6-4.3') == '0.3' and values.get('4.3<4.6-0.3') is False, values
        print(json.dumps({'stage': 'chat_passed', 'tools': [row['name'] for row in calls]}), flush=True)
        multi_session = store.create_session(account['id'], 'Dependent tool execution acceptance')
        multi_id = store.enqueue(account['user_id'], None, 'chat',
            '比较两种采购方案：甲17件、乙20件，每件23.5元，两种方案都打八五折。分别计算总价与差额，将两种总价、差额和算式保存到 comparison.md，读回核对后告诉我结果。不要联网。',
            session_id=multi_session['id'], channel='app')
        multi_started = time.monotonic()
        multi = await run_job(service, store, multi_id)
        operations = [dict(row) for row in store.db.execute('SELECT name,arguments,result,status FROM tool_operations WHERE job_id=? ORDER BY started_at', (multi_id,))]
        report['dependent_task'] = {'status': multi['status'], 'outcome': multi['outcome'], 'reply': multi['result'],
            'elapsed_seconds': round(time.monotonic() - multi_started, 3),
            'operations': operations, 'plans': store.task_facts(multi)['plans']}
        save()
        assert multi['status'] == 'succeeded' and multi['outcome'] == 'completed', f"Dependent task acceptance failed; inspect {folder / 'report.json'}"
        writes = [i for i, row in enumerate(operations) if row['name'] == 'write_file' and row['status'] == 'succeeded']
        reads = [i for i, row in enumerate(operations) if row['name'] == 'read_file' and row['status'] == 'succeeded']
        assert writes and reads and max(reads) > max(writes), operations
        content = json.loads(operations[max(writes)]['arguments'])['content']
        assert '339.575' in content or '339.58' in content, content
        assert '399.5' in content and ('59.925' in content or '59.93' in content), content
        readback = json.loads(operations[max(reads)]['result'])
        assert readback['content'] == content and not readback['truncated'], readback
        assert any(item.get('work_items') for item in report['dependent_task']['plans']), 'Dependent task did not maintain structured steps'
        print(json.dumps({'stage': 'dependent_task_passed', 'tools': [row['name'] for row in operations]}), flush=True)
        jobs = []
        # Prepare both prompts before switching the shared GPU to video.
        for brief in BRIEFS:
            jid = store.enqueue(account['user_id'], None, 'video', brief,
                session_id=session['id'], channel='app', duration_seconds=4,
                _video_references={'character_photos': '验收样例不使用角色照片',
                                   'environment_photos': '验收样例不使用环境照片'})
            waiting = await run_job(service, store, jid)
            assert waiting['status'] == 'waiting_approval', waiting['error']
            approvals = service.approvals.list_for_session(account['id'], session['id'])
            approval = next(row for row in approvals if row['job_id'] == jid)
            service.approvals.decide(account['id'], session['id'], approval['token'], 'approved')
            jobs.append(jid)
        for index, jid in enumerate(jobs):
            started = time.monotonic()
            print(json.dumps({'stage': 'rendering', 'index': index + 1, 'job_id': jid}), flush=True)
            job = await run_job(service, store, jid)
            assert job['status'] == 'succeeded' and job['outcome'] == 'completed', (job['status'], job['outcome'], job['error'])
            output = Path(job['result'])
            await command([FFMPEG, '-v', 'error', '-xerror', '-i', str(output), '-f', 'null', '-'])
            snap = json.loads(store.db.execute('SELECT snapshot FROM media_prompts WHERE job_id=?', (jid,)).fetchone()[0])
            frame_paths = []
            for position in (0.25, 2, 3.75):
                frame = folder / f'clip-{index+1}-{position}.png'
                await command([FFMPEG, '-v', 'error', '-y', '-ss', str(position), '-i', str(output), '-frames:v', '1', str(frame)])
                with Image.open(frame) as picture:
                    picture.verify()
                frame_paths.append(str(frame))
            report['clips'].append({'job_id': jid, 'path': str(output), 'duration': await duration(output),
                'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
                'elapsed_seconds': round(time.monotonic() - started, 3), 'seed': snap['seed'],
                'brief': BRIEFS[index], 'render_prompt': snap['rewritten_prompt'],
                'settings': {key: snap['workflow']['text']['inputs'][key] for key in ('width', 'height', 'length')},
                'frames': frame_paths, 'status': job['status'], 'outcome': job['outcome']})
            save()
            assert abs(report['clips'][-1]['duration'] - 4) < 0.12
            print(json.dumps({'stage': 'clip_passed', 'index': index+1, 'path': str(output)}), flush=True)
        assert len({clip['sha256'] for clip in report['clips']}) == len(BRIEFS)
        assembly = folder / 'assembled'
        assembly.mkdir()
        film = await assemble_project([{'ordinal': i, 'duration': 4, 'result': clip['path']} for i, clip in enumerate(report['clips'])], assembly)
        await command([FFMPEG, '-v', 'error', '-xerror', '-i', str(film), '-f', 'null', '-'])
        report['assembly'] = {'path': str(film), 'duration': await duration(film)}
        assert abs(report['assembly']['duration'] - 8) < 0.25
        report['visual_acceptance'] = 'pending_manual_frame_review'
        report['pipeline_passed'] = True
        save()
        print(json.dumps({'stage': 'passed', 'report': str(folder / 'report.json')}), flush=True)
    except BaseException as exc:
        report.update(pipeline_passed=False, error_type=type(exc).__name__)
        save()
        raise
    finally:
        await engine.stop()
        store.db.close()


if __name__ == '__main__':
    asyncio.run(main())
