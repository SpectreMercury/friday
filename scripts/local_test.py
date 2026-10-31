"""Run an actual local inference and record its duration/output, without Telegram."""
import argparse
import asyncio
import json
import sys
import time
import contextlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_ai.engine import Engine, RUNTIME
import psutil


async def run(args):
    engine = Engine()
    started = time.monotonic()
    job_id = 'test-' + args.kind + '-' + str(int(time.time()))
    peak = {"process_rss_bytes":0,"system_used_bytes":0}
    async def watch():
        while True:
            peak["system_used_bytes"] = max(peak["system_used_bytes"], psutil.virtual_memory().used)
            if engine.process:
                with contextlib.suppress(psutil.Error):
                    proc = psutil.Process(engine.process.pid)
                    rss = proc.memory_info().rss + sum(p.memory_info().rss for p in proc.children(recursive=True))
                    peak["process_rss_bytes"] = max(peak["process_rss_bytes"],rss)
            await asyncio.sleep(1)
    watcher = asyncio.create_task(watch())
    try:
        if args.kind == "chat":
            first = await engine.chat([{"role":"user", "content":"请记住暗号是青松。只回复已记住。"}])
            result = await engine.chat([{"role":"user", "content":"请记住暗号是青松。只回复已记住。"},
                                       {"role":"assistant", "content": first},
                                       {"role":"user", "content":"刚才的暗号是什么？"}])
            assert '青松' in result, result
        else:
            result = str(await engine.render(args.kind, args.prompt, job_id, image=args.image, smoke=args.smoke, seed=42))
        report = {"job":job_id, "kind":args.kind,"elapsed_seconds":round(time.monotonic()-started, 2),"result":result,"peak":peak}
        print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
        (RUNTIME / (job_id+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2))
    finally:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher
        await engine.stop()


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('kind',choices=['chat','image','video'])
    parser.add_argument('--smoke',action='store_true')
    parser.add_argument('--image')
    parser.add_argument('--prompt',default='A red paper boat floating slowly on a calm blue pond, soft morning sunlight, fixed camera, quiet water sounds. No text.')
    asyncio.run(run(parser.parse_args()))
