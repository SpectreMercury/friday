"""Owner-only maintenance requests, executed by an independent local watchdog.

The agent never gets an unrestricted shell through this API. Only two existing
launch agents can be restarted, and automatic retries stop after three failures.
"""
import argparse
import hashlib
import json
import os
import plistlib
import sqlite3
import subprocess
import time
from pathlib import Path
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener
from urllib.parse import urlsplit

import psutil

from .capabilities import Capability, string

SERVICES = {'harness': 'local.qwen.telegram', 'tunnel': 'local.qwen.mobile-tunnel'}
SCHEMA = '''
    CREATE TABLE IF NOT EXISTS maintenance_requests (
        id TEXT PRIMARY KEY, account_id TEXT NOT NULL, session_id TEXT NOT NULL,
        service TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL,
        updated REAL NOT NULL, error TEXT
    );
    CREATE TABLE IF NOT EXISTS maintenance_watchdog (
        service TEXT PRIMARY KEY, failures INTEGER NOT NULL, attempts INTEGER NOT NULL,
        next_attempt REAL NOT NULL, last_error TEXT, updated REAL NOT NULL
    );
'''


class MaintenanceManager:
    def __init__(self, store):
        self.store, self.db = store, store.db
        self.db.executescript(SCHEMA)
        self.db.commit()

    def _allow(self, account_id, session_id):
        session = self.store.require_session(account_id, session_id)
        account = self.store.account(account_id)
        if not account or not account.get('enabled', True) or not account.get('is_admin') or not session.get('management'):
            raise PermissionError('仅所有者明确启用管理权限的 session 可以管理 Mac。')

    def status(self, account_id, session_id):
        self._allow(account_id, session_id)
        memory = psutil.virtual_memory()
        return {'memory_available_bytes': memory.available, 'memory_total_bytes': memory.total,
            'services': list(SERVICES),
            'watchdog': [dict(r) for r in self.db.execute('SELECT * FROM maintenance_watchdog ORDER BY service')],
            'requests': [dict(r) for r in self.db.execute('SELECT id,service,state,updated,error FROM maintenance_requests WHERE account_id=? AND session_id=? ORDER BY created DESC LIMIT 20', (account_id, session_id))]}

    def restart(self, account_id, session_id, service, request_id):
        self._allow(account_id, session_id)
        if service not in SERVICES: raise ValueError('只允许重启 harness 或 tunnel。')
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 200: raise ValueError('需要有效请求编号。')
        ident = hashlib.sha256(json.dumps([account_id, session_id, service, request_id]).encode()).hexdigest()
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO maintenance_requests VALUES (?,?,?,?,'queued',?,?,NULL)",
                (ident, account_id, session_id, service, time.time(), time.time()))
        row = self.db.execute('SELECT id,service,state,error FROM maintenance_requests WHERE id=?', (ident,)).fetchone()
        return dict(row)

    def register(self, registry, job):
        account_id, session_id = job.get('account_id'), job.get('session_id')
        if not account_id or not session_id: return
        def available():
            try: self._allow(account_id, session_id)
            except PermissionError: return False, '需要所有者及当前 session 的 Mac 管理授权'
            return True, None
        async def execute(name, args):
            if name == 'mac_status': return self.status(account_id, session_id)
            return self.restart(account_id, session_id, args['service'], job['id'])
        async def reconcile(name, args):
            self._allow(account_id, session_id)
            ident = hashlib.sha256(json.dumps([account_id, session_id, args['service'], job['id']]).encode()).hexdigest()
            row = self.db.execute('SELECT id,service,state,error FROM maintenance_requests WHERE id=?', (ident,)).fetchone()
            return dict(row) if row else None
        registry.register(Capability('mac_status', '读取 Mac 内存和有限维护请求状态。', access='admin', group='maintenance'), execute, available)
        registry.register(Capability('mac_restart_service', '提交本项目 harness 或 tunnel 服务重启请求；queued 仅表示等待独立守护进程处理。',
            {'service': string(20, enum=list(SERVICES))}, ('service',), access='admin', effect='submit', replay='reconcile', group='maintenance'),
            execute, available, reconcile=reconcile)


def restart_launch_agent(service):
    if service not in SERVICES: raise ValueError('Unknown maintenance service')
    label = SERVICES[service]
    domain = f'gui/{os.getuid()}'
    plist = Path.home() / 'Library' / 'LaunchAgents' / (label + '.plist')
    if not plist.is_file() or plist.is_symlink() or plist.stat().st_uid != os.getuid() or plist.stat().st_mode & 0o022:
        raise PermissionError('LaunchAgent 配置缺失或可被其他账号修改。')
    with plist.open('rb') as handle:
        if plistlib.load(handle).get('Label') != label: raise ValueError('LaunchAgent label mismatch')
    # launchctl gracefully terminates the service; the independent watchdog stays alive.
    subprocess.run(['/bin/launchctl', 'bootout', domain + '/' + label], capture_output=True, timeout=30)
    subprocess.run(['/bin/launchctl', 'bootstrap', domain, str(plist)], capture_output=True, timeout=30, check=True)


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None


def probe_health(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('Invalid health URL')
    if parsed.scheme == 'http' and parsed.hostname not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('HTTP health probes are loopback only')
    try:
        with build_opener(ProxyHandler({}), _NoRedirects()).open(url, timeout=5) as response:
            return response.status == 200
    except OSError:
        return False


class Watchdog:
    def __init__(self, db, restart=restart_launch_agent, clock=time.time):
        self.db, self.restart, self.clock = db, restart, clock
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def process_requests(self):
        # Three seconds lets the harness durably return the submission receipt.
        pending = self.db.execute("SELECT * FROM maintenance_requests WHERE state='queued' AND created<=? ORDER BY created LIMIT 10", (self.clock() - 3,)).fetchall()
        for request in pending:
            with self.db:
                changed = self.db.execute("UPDATE maintenance_requests SET state='running',updated=? WHERE id=? AND state='queued'", (self.clock(), request['id']))
            if changed.rowcount != 1: continue
            try:
                # The watchdog rechecks permissions: an owner may have revoked the session.
                account = self.db.execute('SELECT * FROM accounts WHERE id=?', (request['account_id'],)).fetchone()
                session = self.db.execute('SELECT * FROM sessions WHERE id=? AND account_id=?', (request['session_id'], request['account_id'])).fetchone()
                if not account or not account['is_admin'] or not account['enabled'] or not session or not session['management']:
                    raise PermissionError('Maintenance permission revoked')
                self.restart(request['service'])
                state, error = 'succeeded', None
            except Exception as exc:
                state, error = 'failed', type(exc).__name__
            with self.db:
                self.db.execute('UPDATE maintenance_requests SET state=?,error=?,updated=? WHERE id=?', (state, error, self.clock(), request['id']))

    def recover_interrupted(self):
        # Never blindly repeat a restart that may already have happened.
        with self.db:
            self.db.execute("UPDATE maintenance_requests SET state='uncertain',error='watchdog_interrupted',updated=? WHERE state='running'", (self.clock(),))

    def observe(self, service, healthy):
        if service not in SERVICES: raise ValueError('Unknown maintenance service')
        now = self.clock()
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO maintenance_watchdog VALUES (?,0,0,0,NULL,?)', (service, now))
            row = self.db.execute('SELECT * FROM maintenance_watchdog WHERE service=?', (service,)).fetchone()
            if healthy:
                self.db.execute('UPDATE maintenance_watchdog SET failures=0,attempts=0,next_attempt=0,last_error=NULL,updated=? WHERE service=?', (now, service))
                return False
            failures = row['failures'] + 1
            self.db.execute('UPDATE maintenance_watchdog SET failures=?,updated=? WHERE service=?', (failures, now, service))
            if failures < 3 or row['attempts'] >= 3 or row['next_attempt'] > now: return False
            attempts = row['attempts'] + 1
            self.db.execute('UPDATE maintenance_watchdog SET attempts=?,next_attempt=?,updated=? WHERE service=?',
                            (attempts, now + min(300, 30 * 2 ** attempts), now, service))
        try:
            self.restart(service)
        except Exception as exc:
            with self.db:
                self.db.execute('UPDATE maintenance_watchdog SET last_error=? WHERE service=?', (type(exc).__name__, service))
        return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--local-health', default='http://127.0.0.1:8765/healthz')
    parser.add_argument('--gateway-health', help='HTTPS URL to the same health endpoint through the tunnel')
    parser.add_argument('--auto-recover', action='store_true', help='Explicitly enable bounded automatic service restarts')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if not args.database.is_file(): parser.error('database must already exist')
    # Lock only the watchdog, never take the harness service lock.
    import fcntl
    lock = open(str(args.database) + '.watchdog.lock', 'a')
    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: parser.error('watchdog already running')
    watchdog = Watchdog(sqlite3.connect(args.database, timeout=10))
    watchdog.recover_interrupted()
    while True:
        watchdog.process_requests()
        if args.auto_recover:
            local_ok = probe_health(args.local_health)
            watchdog.observe('harness', local_ok)
            # Avoid restarting the tunnel when the backend itself is unhealthy.
            if local_ok and args.gateway_health:
                watchdog.observe('tunnel', probe_health(args.gateway_health))
        if args.once: break
        time.sleep(30)


if __name__ == '__main__': main()
