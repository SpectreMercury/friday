"""Minimal APNs notifications from durable session events, independent of delivery.

APNs acceptance is never interpreted as task completion or client synchronization.
The outbox retries using stable APNs/collapse IDs; alerts contain no task content.
"""
import asyncio
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path

log = logging.getLogger(__name__)


class APNsNotifier:
    def __init__(self, store, config=None, client=None, signer=None):
        self.store, self.db = store, store.db
        self.config = config if config is not None else os.environ
        self.client, self.signer = client, signer
        self._jwt, self._jwt_at = None, 0
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS apns_cursor(id INTEGER PRIMARY KEY CHECK(id=1),sequence INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS apns_outbox(
                id TEXT PRIMARY KEY,device_id TEXT NOT NULL,account_id TEXT NOT NULL,session_id TEXT NOT NULL,
                event_sequence INTEGER NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,retry_after REAL NOT NULL DEFAULT 0,error TEXT,
                UNIQUE(device_id,event_sequence)
            );
        ''')
        self.db.execute('CREATE TABLE IF NOT EXISTS mobile_push_subscriptions(device_id TEXT PRIMARY KEY,since_sequence INTEGER NOT NULL)')
        # Legacy registrations start at migration time. New device registration
        # records its own event cursor so enabling APNs never floods old results.
        self.db.execute('''INSERT OR IGNORE INTO mobile_push_subscriptions
            SELECT id,(SELECT COALESCE(MAX(sequence),0) FROM session_events)
            FROM mobile_devices WHERE push_token IS NOT NULL''')
        # First enablement does not announce historical completions to new installs.
        self.db.execute('INSERT OR IGNORE INTO apns_cursor SELECT 1,COALESCE(MAX(sequence),0) FROM session_events')
        self.db.commit()

    @property
    def configured(self):
        return all(self.config.get(key) for key in ('APNS_TEAM_ID', 'APNS_KEY_ID', 'APNS_KEY_FILE', 'APNS_BUNDLE_ID'))

    def _token(self):
        if self.signer: return self.signer()
        if self._jwt and time.time() - self._jwt_at < 1200: return self._jwt
        import jwt
        key = Path(self.config['APNS_KEY_FILE'])
        if key.is_symlink() or not key.is_file() or key.stat().st_uid != os.getuid() or key.stat().st_mode & 0o077 or key.stat().st_size > 32000:
            raise PermissionError('APNs key must be an owner-only regular file')
        if not re.fullmatch(r'[A-Za-z0-9]{10}', self.config['APNS_TEAM_ID']) or not re.fullmatch(r'[A-Za-z0-9]{10}', self.config['APNS_KEY_ID']):
            raise ValueError('Invalid APNs key/team identifier')
        now = int(time.time())
        token = jwt.encode({'iss': self.config['APNS_TEAM_ID'], 'iat': now}, key.read_bytes(),
            algorithm='ES256', headers={'kid': self.config['APNS_KEY_ID']})
        self._jwt, self._jwt_at = token, now
        return token

    @staticmethod
    def _alert(event):
        payload = json.loads(event['payload'])
        if event['kind'] == 'upgrade_updated':
            state=payload.get('state')
            if state in ('succeeded','published'):
                kind,body='upgrade_completed','Friday 的能力更新已完成。'
            elif state in ('failed','rolled_back','rollback_failed'):
                kind,body='upgrade_failed',('新版未启用，已恢复原版本。' if state=='rolled_back' else '升级需要留意，请打开原会话查看。')
            else:return None
        elif event['kind'] == 'drama_batch_done':
            kind = 'task_completed' if not payload.get('failed') else 'task_partial'
            body = f"短剧「{payload.get('title', '')}」这批镜头已生成完毕" + (f"，{payload['failed']} 镜失败。" if payload.get('failed') else '。')
        elif event['kind'] == 'approval_requested':
            kind, body = 'approval_requested', '有操作等待你确认。'
        elif event['kind'] == 'task_updated':
            if payload.get('status')=='cancelled' and payload.get('delivery_state')=='suppressed':
                return None
            if payload.get('status') == 'waiting_approval':
                return None  # The separate approval_requested event is the actionable alert.
            if payload.get('outcome') in ('needs_input', 'blocked'):
                kind, body = 'needs_input', '任务需要你补充信息。'
            elif payload.get('status') == 'succeeded':
                if payload.get('outcome') in ('pending', 'continued'):
                    return None
                if payload.get('outcome') == 'paused':
                    kind, body = 'task_partial', '任务已暂停，说“继续”可以接着做。'
                elif payload.get('outcome') == 'partial':
                    kind, body = 'task_partial', '任务已完成部分内容，请查看结果。'
                elif payload.get('outcome') in (None, 'completed'):
                    kind, body = 'task_completed', '任务已完成。'
                    if shot:=payload.get('video_shot'):
                        body=f"镜头 {shot['index']} / {shot['total']} 已完成，点开查看。"
                else:
                    return None
            elif payload.get('status') in ('failed', 'cancelled'):
                kind, body = 'task_stopped', '任务已停止，请查看结果。'
            else: return None
        else: return None
        category = {'approval_requested': 'FRIDAY_APPROVAL', 'needs_input': 'FRIDAY_INPUT',
                    'task_completed': 'FRIDAY_RESULT', 'task_partial': 'FRIDAY_INPUT',
                    'task_stopped': 'FRIDAY_STOPPED','upgrade_completed':'FRIDAY_RESULT','upgrade_failed':'FRIDAY_STOPPED'}[kind]
        return {'aps': {'alert': {'title': 'Friday', 'body': body}, 'sound': 'default',
                        'category': category, 'thread-id': 'friday.' + event['session_id']},
            'account_id': event['account_id'],
            'session_id': event['session_id'], 'job_id': payload.get('job_id') or ('upgrade:'+payload['revision'] if payload.get('revision') else None),
            'kind': kind, 'event_id': event['sequence']}

    def collect(self):
        sequence = self.db.execute('SELECT sequence FROM apns_cursor WHERE id=1').fetchone()[0]
        events = self.db.execute('SELECT * FROM session_events WHERE sequence>? ORDER BY sequence LIMIT 200', (sequence,)).fetchall()
        with self.db:
            for event in events:
                payload = self._alert(event)
                if payload and event['session_id']:
                    devices = self.db.execute('''SELECT d.id FROM mobile_devices d JOIN accounts a ON a.id=d.account_id
                        JOIN mobile_push_subscriptions p ON p.device_id=d.id
                        WHERE p.since_sequence<? AND d.account_id=? AND a.enabled=1 AND d.revoked=0 AND d.expires>?
                        AND d.push_token IS NOT NULL AND d.push_environment IN ('sandbox','production')''',
                        (event['sequence'], event['account_id'], time.time())).fetchall()
                    for device in devices:
                        ident = str(uuid.uuid5(uuid.NAMESPACE_URL, f'local-ai-apns:{device["id"]}:{event["sequence"]}'))
                        if payload.get('job_id'):
                            # If several durable updates arrive before delivery,
                            # send only the newest state of this task to a device.
                            self.db.execute("""UPDATE apns_outbox SET status='suppressed',error='superseded'
                                WHERE device_id=? AND account_id=? AND session_id=? AND status='queued'
                                AND json_extract(payload,'$.job_id')=?""",
                                (device['id'], event['account_id'], event['session_id'], payload['job_id']))
                        self.db.execute('''INSERT OR IGNORE INTO apns_outbox
                            (id,device_id,account_id,session_id,event_sequence,payload,status)
                            VALUES (?,?,?,?,?,?,'queued')''',
                            (ident, device['id'], event['account_id'], event['session_id'], event['sequence'], json.dumps(payload, ensure_ascii=False)))
                sequence = event['sequence']
            self.db.execute('UPDATE apns_cursor SET sequence=? WHERE id=1', (sequence,))
        return len(events)

    async def flush(self):
        if not self.configured: return 0
        pending = self.db.execute("SELECT * FROM apns_outbox WHERE status='queued' AND retry_after<=? ORDER BY event_sequence LIMIT 20", (time.time(),)).fetchall()
        if not pending: return 0
        client = self.client
        if client is None:
            import httpx
            client = httpx.AsyncClient(http2=True, trust_env=False, follow_redirects=False, timeout=15)
        try:
            for item in pending:
                device = self.db.execute('''SELECT d.* FROM mobile_devices d JOIN accounts a ON a.id=d.account_id
                    JOIN sessions s ON s.account_id=a.id AND s.id=?
                    WHERE d.id=? AND d.account_id=? AND a.enabled=1 AND d.revoked=0 AND d.expires>?''',
                    (item['session_id'], item['device_id'], item['account_id'], time.time())).fetchone()
                if not device or not device['push_token'] or device['push_environment'] not in ('sandbox', 'production'):
                    with self.db: self.db.execute("UPDATE apns_outbox SET status='suppressed',error='device_unavailable' WHERE id=?", (item['id'],))
                    continue
                # Device tokens vary in length; accept bounded hex, never assume 32 bytes.
                if not re.fullmatch(r'[A-Fa-f0-9]{32,512}', device['push_token']):
                    with self.db: self.db.execute("UPDATE apns_outbox SET status='failed',error='invalid_device_token' WHERE id=?", (item['id'],))
                    continue
                endpoint = 'https://api.sandbox.push.apple.com' if device['push_environment'] == 'sandbox' else 'https://api.push.apple.com'
                attempts = item['attempts'] + 1
                status, error = 'queued', None
                try:
                    response = await client.post(endpoint + '/3/device/' + device['push_token'],
                        headers={'authorization': 'bearer ' + self._token(), 'apns-topic': self.config['APNS_BUNDLE_ID'],
                                 'apns-push-type': 'alert', 'apns-priority': '10', 'apns-id': item['id'], 'apns-collapse-id': str(uuid.uuid5(uuid.NAMESPACE_URL, 'friday-task:' + item['account_id'] + ':' + item['session_id'] + ':' + str(json.loads(item['payload']).get('job_id') or item['id'])))},
                        json=json.loads(item['payload']))
                    if response.status_code == 200:
                        status = 'accepted'
                    elif response.status_code == 410:
                        status, error = 'suppressed', 'token_unregistered'
                        with self.db:
                            self.db.execute('UPDATE mobile_devices SET push_token=NULL WHERE id=? AND push_token=?', (device['id'], device['push_token']))
                    else:
                        error = 'apns_http_' + str(response.status_code)
                        status = 'queued' if response.status_code == 429 or response.status_code >= 500 else 'failed'
                except Exception as exc:
                    # Store only the exception class: HTTP exceptions may contain token URLs.
                    error = type(exc).__name__
                if status == 'queued' and attempts >= 5: status = 'failed'
                with self.db:
                    self.db.execute('UPDATE apns_outbox SET status=?,attempts=?,retry_after=?,error=? WHERE id=?',
                        (status, attempts, time.time() + min(900, 15 * 2 ** attempts), error, item['id']))
        finally:
            if self.client is None: await client.aclose()
        return len(pending)

    async def run(self):
        while True:
            try:
                if self.configured:
                    self.collect()
                    await self.flush()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning('APNs worker failed: %s', type(exc).__name__)
            await asyncio.sleep(3)
