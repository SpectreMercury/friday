"""Opt-in H3 single/dual process stress probe. Real GPU work, no user messages.

Temporarily renews the existing admission drain, keeps HTTP available, and uses
its own GPU lease/ports/process groups. The drain expires if this probe dies.
Two-step runs measure pressure and relative throughput, not production quality.
"""
import argparse
import asyncio
import contextlib
import fcntl
import json
import os
from pathlib import Path
import signal
import sqlite3
import sys
import time

import aiohttp
import psutil

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from local_ai.engine import build_workflow,COMFY

GIB=1024**3
PROMPT='integrated_multimodal_description: [Shot 1] A blue paper pinwheel turns slowly on a wooden table by a bright window. A fixed medium close-up, soft daylight, consistent framing throughout.\n\noverall_soundscape: Quiet room ambience.\n\nnon_diegetic_music: N/A'


class Probe:
    def __init__(self,steps):
        self.steps=steps
        self.folder=ROOT/'runtime'/('gpu-concurrency-'+time.strftime('%Y%m%d-%H%M%S'))
        self.folder.mkdir(parents=True)
        self.db=sqlite3.connect(ROOT/'runtime/bot.sqlite3',timeout=10)
        self.procs=[];self.logs=[];self.lease=None;self.guard=None
        self.started=time.time();self.phase='setup';self.samples=[];self.results=[]
        self.start_swap=psutil.swap_memory().used
        self.swap_limit=self.start_swap+24*GIB
        self.low_since=None;self.drain_owned=False

    def emit(self,kind,**data):
        row={'time':time.time(),'elapsed':round(time.time()-self.started,2),'phase':self.phase,'kind':kind,**data}
        with (self.folder/'events.jsonl').open('a') as stream:stream.write(json.dumps(row)+'\n')
        print(json.dumps(row),flush=True)

    def drain(self):
        with self.db:self.db.execute('UPDATE evolution_control SET drain_until=? WHERE id=1',(time.time()+120,))

    async def get(self,url):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
            async with session.get(url) as response:
                response.raise_for_status();return await response.json()

    async def setup(self):
        # Separate probe lock prevents two benchmark scripts racing admission.
        self.guard=(ROOT/'runtime/gpu-benchmark.lock').open('w')
        fcntl.flock(self.guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
        health=await self.get('http://127.0.0.1:8765/healthz')
        if not health.get('upgrade_ready'):raise RuntimeError('Harness has active work or voice')
        if self.db.execute("SELECT id FROM jobs WHERE status IN ('queued','running','waiting_approval')").fetchone():raise RuntimeError('Pending work exists')
        if self.db.execute("SELECT id FROM evolution_revisions WHERE kind='core' AND state IN ('queued','testing','waiting_idle','applying')").fetchone():raise RuntimeError('Core upgrade pending')
        if self.db.execute('SELECT drain_until FROM evolution_control WHERE id=1').fetchone()[0]>time.time():raise RuntimeError('Another maintenance operation owns drain')
        self.drain();self.drain_owned=True
        proc=await asyncio.create_subprocess_exec(str(ROOT/'.venv/bin/python'),str(ROOT/'scripts/service.py'),'restart')
        if await proc.wait():raise RuntimeError('Harness restart failed')
        for _ in range(30):
            try:
                health=await self.get('http://127.0.0.1:8765/healthz')
                if health.get('upgrade_ready'):break
            except Exception:pass
            await asyncio.sleep(1)
        else:raise RuntimeError('Harness did not recover')
        self.lease=(ROOT/'runtime/engine.lock').open('a')
        fcntl.flock(self.lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
        self.emit('ready',steps=self.steps,frames=362,resolution=[352,608],initial_swap_gib=round(psutil.swap_memory().used/GIB,2))

    async def monitor(self):
        while True:
            self.drain()
            if self.db.execute("SELECT id FROM evolution_revisions WHERE kind='core' AND state IN ('queued','testing','waiting_idle','applying')").fetchone():raise RuntimeError('Stopped because a core upgrade was requested')
            vm=psutil.virtual_memory();swap=psutil.swap_memory()
            row={'time':time.time(),'phase':self.phase,'available_gib':vm.available/GIB,'swap_gib':swap.used/GIB,
                 'rss_gib':{str(p.pid):psutil.Process(p.pid).memory_info().rss/GIB for p in self.procs if p.returncode is None}}
            self.samples.append(row)
            with (self.folder/'memory.jsonl').open('a') as stream:stream.write(json.dumps(row)+'\n')
            if vm.available<2*GIB:self.low_since=self.low_since or time.time()
            else:self.low_since=None
            if swap.used>self.swap_limit:raise RuntimeError('Safety stop: swap exceeded phase headroom')
            if self.low_since and time.time()-self.low_since>20:raise RuntimeError('Safety stop: available memory below 2 GiB for 20 seconds')
            if len(self.samples)%6==0:self.emit('memory',available_gib=round(row['available_gib'],2),swap_gib=round(row['swap_gib'],2),processes=len(self.procs))
            await asyncio.sleep(5)

    async def launch(self,index):
        port=8289+index
        try:
            await self.get(f'http://127.0.0.1:{port}/system_stats')
        except (aiohttp.ClientError,asyncio.TimeoutError):pass
        else:raise RuntimeError('Probe port occupied')
        output=self.folder/self.phase/str(index);output.mkdir(parents=True)
        log=(self.folder/f'{self.phase}-{index}.log').open('wb');self.logs.append(log)
        env={key:os.environ[key] for key in ('HOME','PATH','TMPDIR','LANG','USER','LOGNAME') if key in os.environ}
        env['ASFP8_INT8_EXT']='1';env['PYTHONUNBUFFERED']='1'
        args=[str(COMFY/'venv/bin/python'),'main.py','--listen','127.0.0.1','--port',str(port),'--reserve-vram','10','--cache-none','--disable-smart-memory','--disable-auto-launch','--disable-metadata','--output-directory',str(output)]
        process=await asyncio.create_subprocess_exec(*args,cwd=COMFY,env=env,stdout=log,stderr=log,start_new_session=True)
        self.procs.append(process)
        for _ in range(150):
            if process.returncode is not None:raise RuntimeError(f'Worker {index} exited during startup')
            try:
                await self.get(f'http://127.0.0.1:{port}/system_stats')
                return port
            except (aiohttp.ClientError,asyncio.TimeoutError):await asyncio.sleep(2)
        raise RuntimeError('Worker startup timeout')

    async def render(self,port,index):
        graph=build_workflow('video',PROMPT,'probe',seed=7100+index,duration_seconds=15)
        graph['scheduler']['inputs']['steps']=self.steps
        graph['save']['inputs']['filename_prefix']='clip'
        started=time.time()
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.post(f'http://127.0.0.1:{port}/prompt',json={'prompt':graph,'client_id':f'probe-{index}'}) as response:
                response.raise_for_status();pid=(await response.json())['prompt_id']
            self.emit('submitted',worker=index,prompt_id=pid)
            while time.time()-started<5400:
                await asyncio.sleep(3)
                async with session.get(f'http://127.0.0.1:{port}/history/{pid}') as response:
                    response.raise_for_status();history=(await response.json()).get(pid)
                if not history:continue
                (self.folder/f'{self.phase}-{index}-history.json').write_text(json.dumps(history,indent=2))
                if history.get('status',{}).get('status_str')=='error':raise RuntimeError(f'Worker {index} workflow failed; see history')
                if history.get('status',{}).get('completed'):
                    result={'worker':index,'seconds':round(time.time()-started,3),'outputs':history.get('outputs',{})}
                    self.emit('completed',**result);return result
        raise RuntimeError('Render timeout')

    async def stop_workers(self):
        for proc in self.procs:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):os.killpg(proc.pid,signal.SIGTERM)
        for proc in self.procs:
            try:await asyncio.wait_for(proc.wait(),8)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):os.killpg(proc.pid,signal.SIGKILL)
                await proc.wait()
        self.procs.clear()
        for log in self.logs:log.close()
        self.logs.clear()

    async def phase_run(self,name,count):
        self.phase=name;self.low_since=None;phase_start=time.time()
        self.emit('phase_start',workers=count)
        try:
            ports=await asyncio.gather(*(self.launch(i) for i in range(count)))
            render_start=time.time()
            jobs=await asyncio.gather(*(self.render(port,i) for i,port in enumerate(ports)))
            result={'phase':name,'workers':count,'render_wall_seconds':round(time.time()-render_start,3),'including_startup_seconds':round(time.time()-phase_start,3),'jobs':jobs}
            self.results.append(result);self.emit('phase_complete',**result)
        finally:await self.stop_workers()

    async def work(self):
        await self.phase_run('single',1)
        single_peak=max(s['swap_gib'] for s in self.samples if s['phase']=='single')*GIB
        self.swap_limit=max(self.start_swap+8*GIB,single_peak+8*GIB)
        await asyncio.sleep(5)
        await self.phase_run('dual',2)

    async def run(self):
        error=None;monitor=work=None
        try:
            await self.setup()
            monitor=asyncio.create_task(self.monitor());work=asyncio.create_task(self.work())
            done,_=await asyncio.wait([monitor,work],return_when=asyncio.FIRST_COMPLETED)
            for task in done:await task
        except BaseException as exc:
            error=type(exc).__name__+': '+str(exc)
            self.emit('stopped',error=error)
        finally:
            for task in (monitor,work):
                if task and not task.done():task.cancel()
            await asyncio.gather(*(t for t in (monitor,work) if t),return_exceptions=True)
            await self.stop_workers()
            if self.lease:self.lease.close()
            if self.drain_owned:
                with self.db:self.db.execute('UPDATE evolution_control SET drain_until=0 WHERE id=1')
            self.db.close()
            if self.guard:self.guard.close()
            report={'steps':self.steps,'frames':362,'resolution':[352,608],'results':self.results,'error':error,'elapsed':time.time()-self.started,'folder':str(self.folder)}
            (self.folder/'report.json').write_text(json.dumps(report,indent=2))
            self.emit('finished',report=report)


async def main():
    parser=argparse.ArgumentParser();parser.add_argument('--steps',type=int,choices=(2,8),default=2)
    probe=Probe(parser.parse_args().steps)
    task=asyncio.current_task()
    for sig in (signal.SIGTERM,signal.SIGINT):asyncio.get_running_loop().add_signal_handler(sig,task.cancel)
    await probe.run()

if __name__=='__main__':asyncio.run(main())
