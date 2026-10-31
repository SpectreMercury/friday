"""Single harness owner shared by Telegram and authenticated mobile ingress."""
from __future__ import annotations
import asyncio
import contextlib
import logging
import os
import time
import json
import traceback
from pathlib import Path
from .engine import Engine,ROOT,RUNTIME,COMFY
from .store import Store
from .pi_agent import PiAgent, ResumeRequired
from .speech import Speech
from .approvals import Approvals,ApprovalPending
from .task_context import failure_reply
from .runtime_build import BUILD_ID

# Unloading the chat model costs a reload plus re-reading the whole prompt on the next
# message (15-60 s). Keep it resident through normal pauses; media jobs still evict it.
IDLE_UNLOAD = 2 * 3600

class HarnessService:
    build_id = BUILD_ID

    def __init__(self, runtime=RUNTIME, *, admin=0, configured=(), store=None, engine=None):
        self.runtime=Path(runtime)
        self.build_id=BUILD_ID
        self.runtime.mkdir(parents=True,exist_ok=True)
        self.admin,self.allowed=admin,set(configured)
        self.store=store or Store(self.runtime/'bot.sqlite3')
        self.store.evolution.recover()
        if admin:
            account=self.store.account_for_uid(admin)
            with self.store.db:
                self.store.db.execute('UPDATE accounts SET is_admin=1,enabled=1 WHERE id=?',(account['id'],))
            if not self.store.db.execute("SELECT 1 FROM schema_versions WHERE name='owner_management'").fetchone():
                legacy=self.store.telegram_session(admin,admin)
                with self.store.db:
                    self.store.db.execute('UPDATE sessions SET management=1 WHERE id=?',(legacy['id'],))
                    self.store.db.execute("INSERT INTO schema_versions VALUES ('owner_management',1)")
        self.engine=engine or Engine()
        self.speech=Speech(self.engine)
        self.approvals=Approvals(self.store,admin)
        self.approvals.durable=True
        self.pi=PiAgent(self.engine,self.store,self.is_allowed,self.runtime,admin,self.approvals.request,
                        sender=getattr(self,'send_artifact',None),speech=self.speech)
        # AGENT_V2=1 runs the in-process agent loop instead of the Pi subprocess.
        from .agent.live import LiveHub
        self.live=LiveHub()
        self.agent=None
        if os.getenv('AGENT_V2','0')=='1':
            from .agent.loop import Agent
            self.agent=Agent(self.engine,self.store,self.runtime,admin=admin,approve=self.approvals.request,
                             sender=getattr(self,'send_artifact',None),speech=self.speech,allowed=self.is_allowed,
                             live=self.live)
        self.schedule_task=None
        self.active=self.active_task=self.worker_task=self.presence_task=None
        self.active_runs={}
        self.video_child_tasks={}
        self.last_work=time.monotonic()
        self.delivery_retry={}
        self.mobile_runner=None
        self.mobile_voice=None
        self.notifications_task=None
        from .connectors import ConnectorManager
        from .maintenance import MaintenanceManager
        self.store.connectors=ConnectorManager(self.store)
        self.store.maintenance=MaintenanceManager(self.store)

    def is_allowed(self, uid):
        return self.store.allowed(uid,self.allowed,self.admin)

    def enqueue_mobile(self, account_id, session_id, text, request_id, *, thinking=None, agents=None):
        from .agent import roster
        account=self.store.account(account_id)
        session=self.store.require_session(account_id,session_id)
        if not isinstance(text,str) or not 1<=len(text.strip())<=12000:
            raise ValueError('消息须为1–12000字符。')
        if not isinstance(request_id,str) or not 1<=len(request_id)<=128:
            raise ValueError('消息需要有效的请求编号。')
        if thinking is not None and not isinstance(thinking,bool):
            raise ValueError('thinking 须为 true 或 false。')
        agents=roster.validate_agents(agents)
        jid=self.store.enqueue(account['user_id'],None,'chat',text.strip(),session_id=session_id,
                               channel='app',request_id=request_id)
        # A retried request keeps the options of its first submission.
        roster.save_options(self.store.db,jid,thinking,agents)
        if session.get('auto_title') and session['name']==roster.DEFAULT_TITLE:
            roster.set_title(self.store,account_id,session_id,roster.provisional_title(text),final=False)
        return self.store.get(jid)

    async def delivery_loop(self, app=None):
        # Mobile results live in the database; notification delivery is separate.
        while True:
            await asyncio.sleep(1)

    async def start_mobile(self, host='127.0.0.1', port=8765):
        from aiohttp import web
        from .mobile_api import create_app
        app=create_app(self)
        self.mobile_runner=web.AppRunner(app,access_log=None)
        await self.mobile_runner.setup()
        await web.TCPSite(self.mobile_runner,host,port).start()

    async def stop_mobile(self):
        if self.mobile_runner:
            await self.mobile_runner.cleanup()
            self.mobile_runner=None

    async def cancel_job(self, job_id):
        cancelled = self.store.get(job_id)
        self.store.update(job_id, status="cancelled", outcome='cancelled', delivered=1,delivery_state='suppressed')
        running = self.active_runs.get(job_id) if hasattr(self, 'active_runs') else None
        task = getattr(self,'video_child_tasks',{}).get(job_id) or (running[1] if running else self.active_task if self.active and self.active['id'] == job_id else None)
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if cancelled and cancelled.get('audio'):
            Path(cancelled['audio']).unlink(missing_ok=True)
        for row in self.store.db.execute("SELECT id FROM jobs WHERE parent_job_id=? AND status IN ('queued','running','waiting_approval')",(job_id,)).fetchall():
            await self.cancel_job(row['id'])
        # Older journals can predate the explicit parent column. Never follow a
        # legacy receipt into a different owner/session.
        for row in self.store.db.execute("SELECT result FROM pi_tools WHERE job_id=? AND name IN ('generate_image','generate_video') AND error IS NULL",(job_id,)).fetchall():
            child=self.store.get(json.loads(row['result']).get('job_id'))
            if child and cancelled and (child['account_id'],child['session_id'])==(cancelled['account_id'],cancelled['session_id']) and child['status']=='queued':
                await self.cancel_job(child['id'])

    async def execute(self, job):
        if job["kind"] == "chat":
            if job.get('audio') and not self.store.checkpoint(job['id']):
                job['prompt'] = await self.speech.transcribe(job['audio'], job['id'])
                self.store.update(job['id'], prompt=job['prompt'])
            reply = await (self.agent or self.pi).run(job)
            self.store.remember(job["user_id"], job["prompt"], reply,session_id=job['session_id'] if job['channel']=='app' else None)
            if job.get('audio') and not self.store.get(job['id']).get('voice_result'):
                try:
                    path = await self.speech.synthesize(reply, job['id'])
                    self.store.update(job['id'], voice_result=str(path))
                except Exception:
                    logging.warning('Speech synthesis failed for job %s; delivering text', job['id'])
            return reply
        if job['kind'] == 'drama':
            return await self.store.dramas.execute(job, self.engine)
        if self.store.video_projects.get(job['id']):
            return await self.store.video_projects.execute(job,self)
        from .media_prompts import prepare
        prepared_at = time.monotonic()
        workflow = await prepare(self.store, self.engine, job)
        drama_take = self.store.dramas.take_for_job(job['id'])
        # A drama take was confirmed on the project page; the storyboard is its review.
        if job['kind']=='video' and not drama_take and not self.store.video_projects.validate_child(job,workflow):
            from .video_review import review_single
            await review_single(self.store,job,self.approvals.request,workflow)
        self.store.event(job['id'],'latency',{'stage':'media_prompt','seconds':round(time.monotonic()-prepared_at,3)})
        self.store.db.commit()
        render_at = time.monotonic()
        path = await self.engine.render(job["kind"], job["prompt"], job["id"], image=job["image"], hd=bool(job["hd"]), workflow=workflow)
        self.store.event(job['id'],'latency',{'stage':'media_render','seconds':round(time.monotonic()-render_at,3)})
        self.store.db.commit()
        if job['kind']=='video' and job.get('duration_seconds'):
            from .video_export import trim_clip
            path=await trim_clip(path,job['duration_seconds'],path.with_name(path.stem+'-trimmed.mp4'))
        if job["kind"] == "video" and job['channel']=='telegram':
            path = await self.prepare_video(path)
        return str(path)

    async def prepare_video(self, path):
        if path.stat().st_size <= 49_000_000 and path.suffix == ".mp4":
            return path
        target = path.with_name(path.stem + "_telegram.mp4")
        proc = await asyncio.create_subprocess_exec("/opt/homebrew/bin/ffmpeg", "-y", "-i", str(path),
            "-c:v", "libx264", "-crf", "25", "-maxrate", "8M", "-bufsize", "16M", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(target),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        try:
            await asyncio.wait_for(proc.wait(), 180)
        except BaseException:
            proc.kill()
            await proc.wait()
            raise
        if proc.returncode or not target.exists() or target.stat().st_size > 49_000_000:
            raise RuntimeError("视频已生成，但无法压缩到 Telegram 文件限制内，原片保存在本机。")
        return target

    async def run_job(self, job):
        try:
            result = await self.execute(job)
            outcome = self.store.get(job['id']).get('outcome')
            # Media execution returns only after rendering/assembly completes.
            # An approval resume leaves a provisional pending outcome behind;
            # carrying it into success makes the finished artifact look active.
            # Chat outcomes instead come from the reviewer and may intentionally
            # remain pending while their separately queued media is still running.
            if job['kind'] in ('image', 'video'):
                # Drama takes report once per batch from the project, not per clip.
                outcome = 'drama' if self.store.dramas.take_for_job(job['id']) else 'completed'
            from .agent.schedule import SILENT
            if job['kind'] == 'chat' and isinstance(result, str) and result.strip() == SILENT:
                # A scheduled check with nothing to report: keep the record, notify nobody.
                self.store.update(job['id'], status='succeeded', outcome='silent', result=result,
                                  delivered=1, delivery_state='suppressed')
            else:
                self.store.update(job['id'], status='succeeded', outcome=outcome or 'completed', result=result)
        except ApprovalPending:
            self.store.update(job['id'],status='waiting_approval',outcome='needs_input')
        except ResumeRequired as exc:
            # New input arrived after the checked draft. The same durable job
            # resumes with it instead of delivering the outdated answer.
            if exc.requeue:
                self.store.update(job['id'],status='queued',outcome=None,result=None,error=None)
            else:
                self.store.update(job['id'],status='succeeded',outcome='continued',result=None,error=None,
                                  delivered=1,delivery_state='suppressed')
        except asyncio.CancelledError:
            if self.store.get(job['id'])['status'] != 'cancelled':
                # Graceful service stop preserves the same request/checkpoint.
                self.store.update(job['id'], status='queued')
            raise
        except Exception as exc:
            # Keep the failing code locations without logging exception bodies,
            # locals, prompts, or remote responses that may contain credentials.
            frames = [{'file': Path(frame.filename).name, 'line': frame.lineno, 'function': frame.name}
                      for frame in traceback.extract_tb(exc.__traceback__)]
            logging.error('Job %s failed: %s; stack=%s', job['id'], type(exc).__name__,
                          ' > '.join(f"{frame['file']}:{frame['line']}:{frame['function']}" for frame in frames))
            with self.store.db:
                self.store.event(job['id'], 'run_error', {'type': type(exc).__name__, 'frames': frames})
            error = str(exc)[:300] if isinstance(exc, RuntimeError) else f'执行服务异常（{type(exc).__name__}）。'
            reply = failure_reply(self.store, job, error, execution_error=True)
            self.store.update(job['id'], status='failed', outcome='blocked', error=error, result=reply)
            if job['kind'] == 'chat':
                self.store.remember(job['user_id'], job['prompt'], reply,session_id=job['session_id'] if job['channel']=='app' else None)
        finally:
            if job.get('run_id'):
                self.store.finish_run(job['run_id'])
            terminal = self.store.get(job['id'])['status'] in ('succeeded','failed','cancelled')
            if terminal and job['kind'] == 'video':
                try:
                    self.store.dramas.settle(job)
                except Exception:
                    logging.warning('Drama take %s could not be settled', job['id'])
            if terminal and job.get('audio'):
                Path(job['audio']).unlink(missing_ok=True)
            if terminal and job.get('image'):
                (COMFY/'input'/job['image']).unlink(missing_ok=True)
            self.last_work = time.monotonic()

    async def schedule_loop(self):
        """Refill drama render windows and turn due schedules into ordinary chat jobs."""
        while True:
            try:
                self.store.dramas.pump_all()
            except Exception:
                logging.warning('Drama queue refill failed')
            for row in (self.agent.schedules.due() if self.agent else ()):
                try:
                    self.agent.schedules.fire(row)
                except Exception:
                    logging.warning('Schedule %s could not be queued', row['id'])
            await asyncio.sleep(30)

    async def worker(self, app=None):
        """Serialize a session, not the whole application. GPU has its own lease."""
        self.active_runs = {}
        delivery = asyncio.create_task(self.delivery_loop(app))
        try:
            while True:
                if hasattr(self,'approvals'):
                    self.approvals.expire()
                if delivery.done():
                    await delivery
                    raise RuntimeError('Delivery loop unexpectedly stopped')
                for jid, (job, task) in list(self.active_runs.items()):
                    if task.done():
                        with contextlib.suppress(asyncio.CancelledError):
                            await task
                        # A task cancelled before its coroutine first runs never
                        # enters run_job's finally block. Retire that admission too.
                        if self.store.get(jid)['status'] == 'running':
                            self.store.update(jid,status='queued')
                        if job.get('run_id'):
                            self.store.finish_run(job['run_id'])
                        del self.active_runs[jid]
                sessions = {job['session_id'] for job, _ in self.active_runs.values()}
                while len(self.active_runs) < 4:
                    job = self.store.next(sessions,excluded_kinds=('image','video') if getattr(self.engine,'voice_reserved',False) else ())
                    if not job:
                        break
                    if not self.is_allowed(job['user_id']):
                        self.store.update(job['id'], status='cancelled', delivered=1)
                        continue
                    run = self.store.start_run(job['id'])
                    with self.store.db:
                        self.store.event(job['id'],'runtime_build',{'build_id':self.build_id,'run_id':run['id']})
                    job.update(self.store.get(job['id']))
                    job['run_id'] = run['id']
                    task = asyncio.create_task(self.run_job(job))
                    self.active_runs[job['id']] = (job, task)
                    sessions.add(job['session_id'])
                first = next(iter(self.active_runs.values()), (None, None))
                self.active, self.active_task = first
                if not self.active_runs and not getattr(self.engine,'voice_reserved',False) and time.monotonic()-self.last_work > IDLE_UNLOAD:
                    lock = getattr(self.engine, 'lock', None)
                    if lock and not lock.locked() and (self.engine.process or self.speech.worker.proc):
                        async with lock:
                            await self.engine.stop()
                await asyncio.sleep(.05)
        finally:
            for _, task in self.active_runs.values():
                task.cancel()
            await asyncio.gather(*(task for _, task in self.active_runs.values()), return_exceptions=True)
            for job, _ in self.active_runs.values():
                if self.store.get(job['id'])['status'] == 'running':
                    self.store.update(job['id'],status='queued')
                if job.get('run_id'):
                    self.store.finish_run(job['run_id'])
            delivery.cancel()
            await asyncio.gather(delivery, return_exceptions=True)
            self.active_runs.clear()
            self.active = self.active_task = None
