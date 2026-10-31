#!/usr/bin/env python3
"""Independent, stdlib-only release supervisor. Installed outside the editable project.

No generated shell commands, dependency installs or database restoration. Owner-approved
Python is trusted code, not sandboxed code; isolation here protects release state.
"""
import argparse
import fcntl
import hashlib
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

CORE_FILES=frozenset('''local_ai/pi_agent.py local_ai/engine.py local_ai/media.py
local_ai/media_prompts.py local_ai/providers.py local_ai/web_tools.py
local_ai/voice_audio.py local_ai/voice_language.py local_ai/voice_loop.py
local_ai/voice_runtime.py local_ai/speech.py local_ai/calculator.py
local_ai/context_view.py local_ai/routing.py local_ai/execution.py
pi/runner-v2.mjs pi/context.mjs pi/progress.mjs'''.split())


def digest(value):return hashlib.sha256(value.encode() if isinstance(value,str) else value).hexdigest()


def atomic(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,temp=tempfile.mkstemp(dir=path.parent,prefix='.upgrade-')
    try:
        with os.fdopen(fd,'wb') as handle:
            handle.write(data);handle.flush();os.fsync(handle.fileno())
        os.replace(temp,path)
        fd=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    finally:
        if os.path.exists(temp):os.unlink(temp)


def base(root):
    return digest(json.dumps({p:digest((root/p).read_bytes()) if (root/p).is_file() else None for p in sorted(CORE_FILES)},sort_keys=True))


def build_id(root):
    paths=sorted((root/'local_ai').glob('*.py'))+[root/'pi'/p for p in ('runner-v2.mjs','context.mjs','progress.mjs','package-lock.json')]
    value=hashlib.sha256()
    for path in paths:
        if path.is_file():value.update(str(path.relative_to(root)).encode()+b'\0');value.update(path.read_bytes())
    return value.hexdigest()[:16]


class Updater:
    def __init__(self,project,db_path,state):
        self.project=Path(project).resolve();self.state=Path(state).resolve()
        self.db_path=Path(db_path).resolve()
        self.db=sqlite3.connect(self.db_path,timeout=10);self.db.row_factory=sqlite3.Row
        self.state.mkdir(parents=True,exist_ok=True,mode=0o700)

    def heartbeat(self,drain=False):
        with self.db:
            self.db.execute('UPDATE evolution_control SET heartbeat=? WHERE id=1',(time.time(),))
            if drain:self.db.execute('UPDATE evolution_control SET drain_until=? WHERE id=1',(time.time()+120,))

    def state_of(self,item,state,**report):
        old=self.db.execute('SELECT report FROM evolution_revisions WHERE id=?',(item['id'],)).fetchone()
        values={**json.loads(old[0]),**report}
        with self.db:
            self.db.execute('UPDATE evolution_revisions SET state=?,report=?,updated=? WHERE id=?',(state,json.dumps(values,ensure_ascii=False),time.time(),item['id']))
            self.db.execute('INSERT INTO session_events(account_id,session_id,kind,payload,created) VALUES(?,?,?,?,?)',
                (item['account_id'],item['session_id'],'upgrade_updated',json.dumps({'revision':item['id'],'state':state}),time.time()))

    def allowed(self,item):
        row=self.db.execute('SELECT a.enabled,a.is_admin,s.management FROM accounts a JOIN sessions s ON s.account_id=a.id WHERE a.id=? AND s.id=?',(item['account_id'],item['session_id'])).fetchone()
        if not row or not all(row):raise PermissionError('Upgrade authorization revoked')
        files=json.loads(item['files'])
        if item['kind']!='core' or item['name']!='friday' or not files or set(files)-CORE_FILES:raise ValueError('Disallowed upgrade files')
        if len(json.dumps(files,ensure_ascii=False).encode())>256000:raise ValueError('Oversize upgrade')
        # The revision ID authenticates the exact content approved in the application.
        encoded=json.dumps(files,ensure_ascii=False,sort_keys=True)
        expected=digest(json.dumps([item['account_id'],item['session_id'],item['kind'],item['name'],item['base'],encoded,item['summary']],ensure_ascii=False))
        if expected!=item['id']:raise ValueError('Revision contents changed')
        for name in files:
            target=self.project/name
            if target.is_symlink() or not target.resolve().is_relative_to(self.project):raise ValueError('Upgrade path is not a regular project file')
        return files

    def health(self):
        try:
            with build_opener(ProxyHandler({})).open('http://127.0.0.1:8765/healthz',timeout=3) as response:
                return json.loads(response.read(16384))
        except Exception:return {}

    def service(self,start):
        label='local.qwen.telegram';domain=f'gui/{os.getuid()}'
        if start:
            command=['/bin/launchctl','bootstrap',domain,str(Path.home()/'Library/LaunchAgents'/f'{label}.plist')]
            for attempt in range(10):
                result=subprocess.run(command,capture_output=True,timeout=20)
                if result.returncode==0:return
                time.sleep(1)
            raise RuntimeError('Service bootstrap failed')
        result=subprocess.run(['/bin/launchctl','bootout',domain+'/'+label],capture_output=True,timeout=40)
        if result.returncode!=0:raise RuntimeError('Service stop failed; no files changed')

    def await_health(self,expected,seconds=90):
        deadline=time.monotonic()+seconds;good=0
        while time.monotonic()<deadline:
            self.heartbeat(drain=True)
            value=self.health()
            good=good+1 if value.get('ready') and value.get('build_id')==expected else 0
            if good>=15:return True
            time.sleep(2)
        return False

    def command(self,args,cwd,log,seconds=300):
        # No inherited service credentials. The candidate has no production .env or DB.
        env={'PATH':'/opt/homebrew/bin:/usr/bin:/bin','PYTHONPATH':str(cwd),
             'PYTHONDONTWRITEBYTECODE':'1','PWA_ENABLED':'0','PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1'}
        with open(log,'ab') as output:
            process=subprocess.Popen(args,cwd=cwd,env=env,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
            end=time.monotonic()+seconds
            try:
                while process.poll() is None:
                    self.heartbeat()
                    if time.monotonic()>end:raise TimeoutError('Candidate validation timed out')
                    time.sleep(.3)
                if process.returncode:raise RuntimeError('Candidate validation failed; see local validation.log')
            finally:
                if process.poll() is None:
                    os.killpg(process.pid,signal.SIGKILL);process.wait()

    def prepare(self,item,files):
        release=self.state/item['id'];candidate=release/'candidate'
        if release.exists():raise ValueError('Candidate directory already exists; do not rerun uncertain validation')
        release.mkdir(mode=0o700)
        for directory in ('local_ai','pi','tests','scripts','assistant-skills','config','docs','workflows'):
            source=self.project/directory
            if source.exists():
                shutil.copytree(source,candidate/directory,ignore=shutil.ignore_patterns('__pycache__','node_modules','.pytest_cache','*.pyc'),symlinks=False)
        for name in ('pyproject.toml','models.lock.json','sources.lock.json','speech-models.lock.json'):
            source=self.project/name
            if source.is_file():shutil.copy2(source,candidate/name)
        (candidate/'vendor').mkdir()
        for source,target in ((self.project/'vendor/node',candidate/'vendor/node'),(self.project/'pi/node_modules',candidate/'pi/node_modules')):
            if source.exists():target.symlink_to(source)
        for name,text in files.items():atomic(candidate/name,text.encode())
        expected=build_id(candidate)
        python=str(self.project/'.venv/bin/python');log=release/'validation.log'
        self.command([python,'-m','pytest','-p','pytest_asyncio.plugin','-q'],candidate,log)
        if (candidate/'vendor/node').exists():
            self.command([str(candidate/'vendor/node'),'--test','pi/context.test.mjs','pi/progress.test.mjs'],candidate,log)
            self.command([str(candidate/'vendor/node'),'pi/runner-v2.mjs','--check'],candidate,log)
        if build_id(candidate)!=expected:raise ValueError('Candidate files changed during validation')
        atomic(release/'validated.json',json.dumps({'build_id':expected,'base':item['base']}).encode())
        return expected

    def idle(self,item,seconds=1800):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            self.allowed(item);self.heartbeat(drain=True)
            busy=self.db.execute("SELECT count(*) FROM jobs WHERE status='running'").fetchone()[0]
            health=self.health()
            if not busy and health.get('ready') and health.get('upgrade_ready'):
                # Allow a whole admission/presence cycle to settle, then recheck.
                time.sleep(2)
                health=self.health()
                if health.get('upgrade_ready') and not self.db.execute("SELECT count(*) FROM jobs WHERE status='running'").fetchone()[0]:return
            time.sleep(2)
        raise TimeoutError('Existing task or voice session did not finish; old service retained')

    def backup(self,item,files):
        release=self.state/item['id']
        original={name:(self.project/name).read_text() for name in files}
        atomic(release/'rollback.json',json.dumps({'files':original,'build_id':build_id(self.project)},ensure_ascii=False).encode())
        with sqlite3.connect(release/'database-before.sqlite3') as target:self.db.backup(target)
        (release/'database-before.sqlite3').chmod(0o600)

    def restore(self,item):
        saved=json.loads((self.state/item['id']/'rollback.json').read_text())
        if not saved['files'] or set(saved['files'])-CORE_FILES:raise ValueError('Invalid rollback manifest')
        try:self.service(False)
        except RuntimeError:
            # A failed new process may already be down; bootstrap still must succeed.
            pass
        for name,text in saved['files'].items():atomic(self.project/name,text.encode())
        self.service(True)
        if not self.await_health(saved['build_id']):raise RuntimeError('Rollback health check failed; manual recovery required')
        self.state_of(item,'rolled_back',rollback='old_code_restored_database_preserved')

    def apply(self,item):
        switched=False
        try:
            files=self.allowed(item)
            if base(self.project)!=item['base']:raise ValueError('Source changed since approval; create a new draft')
            self.state_of(item,'testing')
            expected=self.prepare(item,files)
            self.state_of(item,'waiting_idle',tests='passed',build_id=expected)
            self.idle(item)
            self.allowed(item)
            if base(self.project)!=item['base']:raise ValueError('Source changed during validation')
            self.backup(item,files)
            self.state_of(item,'applying')
            switched=True  # Recovery from every point below restores the full manifest.
            self.service(False)
            for name,text in files.items():atomic(self.project/name,text.encode())
            self.service(True)
            if not self.await_health(expected):raise RuntimeError('New version failed health checks')
            self.state_of(item,'succeeded',health='fifteen_consecutive_checks_passed')
        except Exception as exc:
            if switched:
                try:self.restore(item)
                except Exception as recovery:self.state_of(item,'rollback_failed',error=type(exc).__name__,recovery_error=type(recovery).__name__)
            else:self.state_of(item,'failed',error=str(exc)[:500])
        finally:
            with self.db:self.db.execute('UPDATE evolution_control SET drain_until=0 WHERE id=1')

    def recover(self):
        for row in self.db.execute("SELECT * FROM evolution_revisions WHERE kind='core' AND state IN ('testing','waiting_idle','applying')").fetchall():
            item=dict(row)
            if item['state']=='applying':
                try:self.restore(item)
                except Exception as exc:self.state_of(item,'rollback_failed',error=type(exc).__name__)
            else:self.state_of(item,'failed',error='updater_interrupted_before_deployment; old version retained')
        with self.db:self.db.execute('UPDATE evolution_control SET drain_until=0 WHERE id=1')

    def tick(self):
        self.heartbeat()
        row=self.db.execute("SELECT * FROM evolution_revisions WHERE kind='core' AND state='queued' ORDER BY created LIMIT 1").fetchone()
        if row:self.apply(dict(row))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--project',required=True);parser.add_argument('--database',required=True);parser.add_argument('--state',required=True)
    args=parser.parse_args()
    state=Path(args.state);state.mkdir(parents=True,exist_ok=True,mode=0o700)
    with open(state/'updater.lock','w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        updater=Updater(args.project,args.database,state)
        updater.recover()
        while True:
            try:updater.tick()
            except Exception:
                # Recover next loop/process; never follow instructions in log output.
                raise
            time.sleep(3)


if __name__=='__main__':main()
