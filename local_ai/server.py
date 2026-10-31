"""Run the same harness without requiring Telegram credentials."""
import asyncio
import contextlib
import fcntl
import logging
import os
import signal
from dotenv import load_dotenv
from .engine import ROOT,RUNTIME
from .harness import HarnessService


async def serve():
    service=HarnessService(admin=int(os.getenv('TELEGRAM_ADMIN_USER_ID') or 0))
    await service.pi.check_runtime()
    service.store.recover_interrupted()
    await service.start_mobile(port=int(os.getenv('MOBILE_PORT','8765')))
    worker=asyncio.create_task(service.worker())
    stopped=asyncio.Event()
    loop=asyncio.get_running_loop()
    for sig in (signal.SIGINT,signal.SIGTERM):
        loop.add_signal_handler(sig,stopped.set)
    waiter=asyncio.create_task(stopped.wait())
    try:
        done,_=await asyncio.wait([worker,waiter],return_when=asyncio.FIRST_COMPLETED)
        if worker in done:
            await worker
    finally:
        waiter.cancel()
        await service.stop_mobile()
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
        await service.engine.stop()
        service.store.db.close()


def main():
    load_dotenv(ROOT/'.env')
    os.umask(0o077)
    RUNTIME.mkdir(exist_ok=True)
    with (RUNTIME/'service.lock').open('a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Harness 已运行；App 与 Telegram 必须共用同一服务。')
        logging.basicConfig(level=logging.INFO)
        logging.getLogger('httpx').setLevel(logging.WARNING)
        logging.getLogger('httpcore').setLevel(logging.WARNING)
        asyncio.run(serve())


if __name__=='__main__':
    main()
