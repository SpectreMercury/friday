"""Bounded subprocess execution; no inherited bot or provider credentials."""
import asyncio
import contextlib
import os
import signal
from pathlib import Path


async def run_process(argv, *, cwd, stdin=None, timeout=60):
    env = {'PATH': '/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin',
           'HOME': str(Path.home()), 'LANG': 'en_US.UTF-8', 'NO_COLOR': '1'}
    proc = await asyncio.create_subprocess_exec(*argv, cwd=cwd, env=env, start_new_session=True,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    async def drain(stream):
        saved = bytearray()
        count = 0
        while chunk := await stream.read(8192):
            count += len(chunk)
            if len(saved) < 32000:
                saved.extend(chunk[:32000-len(saved)])
        return saved.decode('utf-8', errors='replace'), count > 32000
    readers = [asyncio.create_task(drain(proc.stdout)), asyncio.create_task(drain(proc.stderr))]
    try:
        if stdin:
            proc.stdin.write(stdin)
            await proc.stdin.drain()
        proc.stdin.close()
        async with asyncio.timeout(timeout):
            await proc.wait()
            out, err = await asyncio.gather(*readers)
        return {'exit_code': proc.returncode, 'stdout': out[0], 'stderr': err[0], 'truncated': out[1] or err[1]}
    except asyncio.TimeoutError:
        raise ValueError(f'命令超过 {timeout} 秒，已终止。') from None
    finally:
        # Reap descendants too, even if their parent has already exited.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        await proc.wait()
        for task in readers:
            task.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
