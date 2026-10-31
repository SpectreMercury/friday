"""Revocable browser sessions; WebAuthn proves identity, cookies carry sessions.

No browser receives a native Bearer credential. Origin and CSRF checks are
performed by the HTTP ingress, including the WebSocket upgrade boundary.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid

from .mobile_auth import digest
from .passkeys import PasskeyAuth


class BrowserAuth:
    lifetime = 7 * 86400
    idle_timeout = 12 * 3600

    def __init__(self, store, origin):
        self.store, self.db = store, store.db
        self.passkeys = PasskeyAuth(store, origin)
        self.origin = self.passkeys.origin
        self.secure = self.origin.startswith('https://')
        # Production cookies cannot be supplied by sibling subdomains or scoped
        # to another path. Plain cookies exist only for explicit localhost dev.
        prefix = '__Host-' if self.secure else 'Local-'
        self.cookie_name = prefix + 'HarnessSession'
        self.flow_cookie_name = prefix + 'HarnessFlow'
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS browser_sessions (
                id TEXT PRIMARY KEY,token_hash TEXT NOT NULL UNIQUE,
                device_id TEXT NOT NULL UNIQUE,credential_id TEXT NOT NULL,
                created REAL NOT NULL,last_seen REAL NOT NULL,expires REAL NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0
            );
        ''')
        self.db.commit()

    @staticmethod
    def csrf(token):
        return hmac.new(token.encode(), b'harness-browser-csrf-v1', hashlib.sha256).hexdigest()

    def set_cookie(self, response, token):
        response.set_cookie(self.cookie_name, token, secure=self.secure, httponly=True,
                            samesite='Strict', path='/', max_age=self.lifetime)

    def clear_cookie(self, response):
        response.del_cookie(self.cookie_name, secure=self.secure, httponly=True,
                            samesite='Strict', path='/')

    def set_flow(self, response, flow):
        response.set_cookie(self.flow_cookie_name, flow, secure=self.secure, httponly=True,
                            samesite='Strict', path='/', max_age=300)

    def clear_flow(self, response):
        response.del_cookie(self.flow_cookie_name, secure=self.secure, httponly=True,
                            samesite='Strict', path='/')

    def issue(self, identity, previous=None):
        now = time.time()
        credential = self.passkeys.require_credential(identity['account_id'], identity['credential_id'])
        account = self.store.account(identity['account_id'])
        if not credential or not account['enabled']:
            raise PermissionError('通行密钥已失效。')
        token, sid, did = secrets.token_urlsafe(48), uuid.uuid4().hex, uuid.uuid4().hex
        label = identity.get('label') or '网页通行密钥'
        with self.db:
            if previous:
                self.revoke_token(previous)
            self.db.execute('INSERT INTO mobile_devices(id,account_id,token_hash,created,expires,device_name) VALUES (?,?,?,?,?,?)',
                (did, account['id'], digest(secrets.token_urlsafe(48)), now, now + self.lifetime, '网页 · ' + label[:80]))
            self.db.execute('INSERT INTO browser_sessions VALUES (?,?,?,?,?,?,?,0)',
                (sid, digest(token), did, identity['credential_id'], now, now, now + self.lifetime))
        return token

    def _query(self, where, value):
        return self.db.execute(f'''SELECT d.*,b.id AS browser_session_id,b.credential_id,
            b.expires AS browser_expires,b.last_seen,b.revoked AS browser_revoked,
            c.revoked AS credential_revoked,c.origin AS credential_origin,c.rp_id,a.enabled,a.display_name,a.is_admin
            FROM browser_sessions b JOIN mobile_devices d ON d.id=b.device_id
            JOIN passkey_credentials c ON c.credential_id=b.credential_id AND c.account_id=d.account_id
            JOIN accounts a ON a.id=d.account_id WHERE {where}=?''', (value,)).fetchone()

    def authenticate(self, token, *, touch=True):
        if not isinstance(token, str) or not 32 <= len(token) <= 200:
            raise PermissionError('请用通行密钥登录。')
        row = self._query('b.token_hash', digest(token))
        if not self._valid(row):
            raise PermissionError('登录已过期或已撤销，请重新验证。')
        now = time.time()
        if touch and now - row['last_seen'] > 60:
            with self.db:
                self.db.execute('UPDATE browser_sessions SET last_seen=? WHERE id=?', (now, row['browser_session_id']))
        return dict(row)

    def _valid(self, row):
        now = time.time()
        return bool(row and row['enabled'] and not row['revoked'] and not row['browser_revoked']
                    and not row['credential_revoked'] and row['credential_origin'] == self.origin
                    and row['rp_id'] == self.passkeys.rp_id and row['expires'] > now
                    and row['browser_expires'] > now and row['last_seen'] + self.idle_timeout > now)

    def device_active(self, device_id):
        row = self._query('b.device_id', device_id)
        # Native devices do not have a browser session. Their existing auth
        # and expiry remain handled by the ingress's normal device checks.
        if row is None:
            return not self.db.execute('SELECT 1 FROM browser_sessions WHERE device_id=?', (device_id,)).fetchone()
        return self._valid(row)

    def revoke_token(self, token):
        if not isinstance(token, str) or len(token) > 200:
            return None
        row = self.db.execute('SELECT device_id FROM browser_sessions WHERE token_hash=?', (digest(token),)).fetchone()
        if row:
            with self.db:
                self.db.execute('UPDATE browser_sessions SET revoked=1 WHERE token_hash=?', (digest(token),))
                self.db.execute('UPDATE mobile_devices SET revoked=1,push_token=NULL WHERE id=?', (row['device_id'],))
            return row['device_id']
        return None

    def public_session(self, token):
        row = self.authenticate(token)
        return {'account': {'id': row['account_id'], 'display_name': row['display_name'], 'is_admin': bool(row['is_admin'])},
                'device_id': row['id'], 'csrf_token': self.csrf(token), 'expires': row['browser_expires']}
