"""Invite-only WebAuthn registration and browser-bound, one-use ceremonies.

The authenticator's private key never reaches this service. All WebAuthn
cryptography is delegated to py_webauthn; this module supplies account ownership,
strict origin policy, durable anti-replay state, and local-owner administration.
"""
import hashlib
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit

from webauthn import (
    generate_authentication_options, generate_registration_options,
    options_to_json, verify_authentication_response, verify_registration_response,
)
from webauthn.helpers import bytes_to_base64url, parse_authentication_credential_json
from webauthn.helpers import base64url_to_bytes
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers.structs import (
    AttestationConveyancePreference, AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor, ResidentKeyRequirement, UserVerificationRequirement,
)

INVITE_TTL = 600
CHALLENGE_TTL = 300
PURPOSE = 'passkey_registration'
ALGORITHMS = [COSEAlgorithmIdentifier.ECDSA_SHA_256, COSEAlgorithmIdentifier.EDDSA,
              COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256]


def _digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def configured_origin(value):
    """Validate an explicit origin; never infer trust from request/proxy headers."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError('通行密钥需要显式配置 MOBILE_PUBLIC_URL。')
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError('MOBILE_PUBLIC_URL 端口无效。') from exc
    host = parsed.hostname
    if (not host or parsed.username is not None or parsed.password is not None
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment
            or '\\' in value or any(ord(c) <= 32 for c in value)):
        raise ValueError('MOBILE_PUBLIC_URL 必须是没有路径、凭据或查询参数的固定 origin。')
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and host == 'localhost'):
        raise ValueError('通行密钥必须使用 HTTPS；仅显式 localhost 允许 HTTP 开发。')
    # DNS RP IDs only; a browser will not accept an IP address as a WebAuthn RP ID.
    if len(host) > 253 or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', host) or '..' in host:
        raise ValueError('MOBILE_PUBLIC_URL 必须使用有效域名。')
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError('通行密钥需要域名，不能使用 IP 地址。')
    if any(not label or len(label) > 63 or label.startswith('-') or label.endswith('-') for label in host.split('.')):
        raise ValueError('MOBILE_PUBLIC_URL 域名无效。')
    effective_port = '' if port is None or port == (443 if parsed.scheme == 'https' else 80) else f':{port}'
    return f'{parsed.scheme}://{host}{effective_port}', host


class PasskeyAuth:
    def __init__(self, store, origin=None):
        self.store = store
        self.origin, self.rp_id = configured_origin(origin) if origin is not None else (None, None)
        self._backup_before_migration()
        store.db.executescript('''
            CREATE TABLE IF NOT EXISTS passkey_invites (
                code_hash TEXT PRIMARY KEY, account_id TEXT NOT NULL,
                purpose TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL, consumed REAL
            );
            CREATE TABLE IF NOT EXISTS passkey_users (
                account_id TEXT PRIMARY KEY, user_handle BLOB UNIQUE NOT NULL
            );
            CREATE TABLE IF NOT EXISTS passkey_credentials (
                credential_id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
                public_key BLOB NOT NULL, sign_count INTEGER NOT NULL,
                user_handle BLOB NOT NULL, rp_id TEXT NOT NULL, origin TEXT NOT NULL,
                label TEXT NOT NULL, created REAL NOT NULL, last_used REAL,
                revoked INTEGER NOT NULL DEFAULT 0, device_type TEXT NOT NULL,
                backed_up INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS passkey_credentials_account ON passkey_credentials(account_id);
            CREATE TABLE IF NOT EXISTS passkey_challenges (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, challenge BLOB NOT NULL,
                flow_hash TEXT NOT NULL, account_id TEXT, label TEXT,
                origin TEXT NOT NULL, rp_id TEXT NOT NULL,
                created REAL NOT NULL, expires REAL NOT NULL, consumed REAL
            );
        ''')
        store.db.commit()

    def _backup_before_migration(self):
        db = self.store.db
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='passkey_credentials'").fetchone():
            return
        if db.in_transaction:
            raise RuntimeError('通行密钥首次迁移前须先提交当前数据库事务。')
        path = Path(self.store.path)
        backup = path.with_name(path.name + '.pre-passkeys-' + str(time.time_ns()) + '.bak')
        # Create with final restrictive permissions, refuse collisions/symlinks,
        # and use SQLite's snapshot API so WAL contents are included consistently.
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        try:
            destination = sqlite3.connect(backup)
            try:
                db.backup(destination)
            finally:
                destination.close()
        except BaseException:
            backup.unlink(missing_ok=True)
            raise

    def _configured(self):
        if not self.origin:
            raise ValueError('通行密钥登录尚未配置固定 HTTPS origin。')

    @staticmethod
    def _flow(flow_id):
        if not isinstance(flow_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', flow_id):
            raise PermissionError('浏览器验证流程无效，请重新开始。')
        return _digest(flow_id)

    def _enabled(self, account_id):
        account = self.store.account(account_id)
        if not account['enabled']:
            raise PermissionError('账号已停用。')
        return account

    def invite(self, account_id):
        """Only called by the local administration CLI, never a public route."""
        self._enabled(account_id)
        code = secrets.token_urlsafe(32)
        now = time.time()
        with self.store.db:
            self.store.db.execute('INSERT INTO passkey_invites VALUES (?,?,?,?,?,NULL)',
                                  (_digest(code), account_id, PURPOSE, now, now + INVITE_TTL))
        return code

    def list_credentials(self, account_id):
        self.store.account(account_id)
        return [dict(row) for row in self.store.db.execute(
            'SELECT credential_id,account_id,label,created,last_used,revoked,device_type,backed_up '
            'FROM passkey_credentials WHERE account_id=? ORDER BY created', (account_id,))]

    def require_credential(self, account_id, credential_id):
        """Browser sessions must call this on every request, including live streams."""
        self._enabled(account_id)
        row = self.store.db.execute('SELECT * FROM passkey_credentials WHERE credential_id=? AND account_id=? AND revoked=0',
                                    (credential_id, account_id)).fetchone()
        if not row or (self.origin and (row['origin'] != self.origin or row['rp_id'] != self.rp_id)):
            raise PermissionError('通行密钥已失效或已撤销。')
        return dict(row)

    def revoke(self, account_id, credential_id):
        self.store.account(account_id)
        with self.store.db:
            changed = self.store.db.execute('UPDATE passkey_credentials SET revoked=1 WHERE credential_id=? AND account_id=?',
                                            (credential_id, account_id))
            if changed.rowcount != 1:
                raise PermissionError('找不到该账号的通行密钥。')

    def _new_challenge(self, kind, flow_hash, *, account_id=None, label=None):
        now = time.time()
        challenge_id, challenge = secrets.token_urlsafe(24), secrets.token_bytes(32)
        self.store.db.execute('DELETE FROM passkey_challenges WHERE expires<?', (now - 86400,))
        self.store.db.execute('DELETE FROM passkey_invites WHERE expires<?', (now - 86400,))
        self.store.db.execute('INSERT INTO passkey_challenges VALUES (?,?,?,?,?,?,?,?,?,?,NULL)',
                              (challenge_id, kind, challenge, flow_hash, account_id, label,
                               self.origin, self.rp_id, now, now + CHALLENGE_TTL))
        return challenge_id, challenge

    def begin_registration(self, code, label, *, flow_id):
        self._configured()
        flow_hash = self._flow(flow_id)
        if not isinstance(code, str) or not 32 <= len(code) <= 200:
            raise PermissionError('注册码无效、已使用或已过期。')
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 100:
            raise ValueError('请填写 1–100 字的通行密钥名称。')
        now = time.time()
        with self.store.db:
            row = self.store.db.execute('SELECT * FROM passkey_invites WHERE code_hash=? AND purpose=?',
                                        (_digest(code), PURPOSE)).fetchone()
            if not row or row['consumed'] is not None or row['expires'] <= now:
                raise PermissionError('注册码无效、已使用或已过期。')
            account = self._enabled(row['account_id'])
            changed = self.store.db.execute('UPDATE passkey_invites SET consumed=? WHERE code_hash=? AND purpose=? AND consumed IS NULL AND expires>?',
                                            (now, _digest(code), PURPOSE, now))
            if changed.rowcount != 1:
                raise PermissionError('注册码无效、已使用或已过期。')
            self.store.db.execute('INSERT OR IGNORE INTO passkey_users VALUES (?,?)',
                                  (account['id'], secrets.token_bytes(32)))
            handle = self.store.db.execute('SELECT user_handle FROM passkey_users WHERE account_id=?', (account['id'],)).fetchone()[0]
            challenge_id, challenge = self._new_challenge('registration', flow_hash, account_id=account['id'], label=label.strip())
            existing = self.store.db.execute('SELECT credential_id FROM passkey_credentials WHERE account_id=? AND rp_id=?',
                                             (account['id'], self.rp_id)).fetchall()
            options = generate_registration_options(
                rp_id=self.rp_id, rp_name='Friday', user_id=handle,
                user_name=account['display_name'], user_display_name=account['display_name'],
                challenge=challenge, timeout=CHALLENGE_TTL * 1000,
                attestation=AttestationConveyancePreference.NONE,
                authenticator_selection=AuthenticatorSelectionCriteria(
                    resident_key=ResidentKeyRequirement.REQUIRED, require_resident_key=True,
                    user_verification=UserVerificationRequirement.REQUIRED),
                exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(r['credential_id'])) for r in existing],
                supported_pub_key_algs=ALGORITHMS)
        return {'challenge_id': challenge_id, 'publicKey': json.loads(options_to_json(options))}

    def begin_authentication(self, *, flow_id):
        self._configured()
        flow_hash = self._flow(flow_id)
        with self.store.db:
            challenge_id, challenge = self._new_challenge('authentication', flow_hash)
        options = generate_authentication_options(rp_id=self.rp_id, challenge=challenge,
                    timeout=CHALLENGE_TTL * 1000, user_verification=UserVerificationRequirement.REQUIRED)
        # No account hint / credential list: discoverable credentials and no account enumeration.
        return {'challenge_id': challenge_id, 'publicKey': json.loads(options_to_json(options))}

    def _consume_challenge(self, challenge_id, kind, flow_id):
        self._configured()
        flow_hash = self._flow(flow_id)
        if not isinstance(challenge_id, str) or not 1 <= len(challenge_id) <= 128:
            raise PermissionError('验证请求无效，请重新开始。')
        now = time.time()
        with self.store.db:
            row = self.store.db.execute('SELECT * FROM passkey_challenges WHERE id=?', (challenge_id,)).fetchone()
            if (not row or row['kind'] != kind or row['consumed'] is not None or row['expires'] <= now
                    or row['origin'] != self.origin or row['rp_id'] != self.rp_id
                    or not secrets.compare_digest(row['flow_hash'], flow_hash)):
                raise PermissionError('验证请求无效、已使用或已过期。')
            changed = self.store.db.execute('UPDATE passkey_challenges SET consumed=? WHERE id=? AND consumed IS NULL AND expires>?',
                                            (now, challenge_id, now))
            if changed.rowcount != 1:
                raise PermissionError('验证请求已使用。')
        # Commit consumption before verification: malformed/failed attempts cannot retry.
        return dict(row)

    @staticmethod
    def _credential_policy(credential):
        if not isinstance(credential, dict) or len(json.dumps(credential)) > 65536:
            raise PermissionError('通行密钥响应无效。')
        response = credential.get('response')
        if not isinstance(response, dict):
            raise PermissionError('通行密钥响应无效。')
        data = json.loads(base64url_to_bytes(response['clientDataJSON']))
        # The library validates challenge/type/origin/signature; also prohibit framed ceremonies.
        if data.get('crossOrigin', False) is not False or 'topOrigin' in data:
            raise PermissionError('不允许跨站点通行密钥验证。')

    def finish_registration(self, challenge_id, credential, *, flow_id):
        pending = self._consume_challenge(challenge_id, 'registration', flow_id)
        try:
            self._credential_policy(credential)
            verified = verify_registration_response(
                credential=credential, expected_challenge=pending['challenge'],
                expected_rp_id=self.rp_id, expected_origin=self.origin,
                require_user_verification=True, require_user_presence=True,
                supported_pub_key_algs=ALGORITHMS)
        except Exception as exc:
            raise PermissionError('通行密钥注册验证失败，请重新生成注册码。') from exc
        cid = bytes_to_base64url(verified.credential_id)
        with self.store.db:
            self._enabled(pending['account_id'])
            handle = self.store.db.execute('SELECT user_handle FROM passkey_users WHERE account_id=?', (pending['account_id'],)).fetchone()[0]
            try:
                self.store.db.execute('INSERT INTO passkey_credentials '
                    '(credential_id,account_id,public_key,sign_count,user_handle,rp_id,origin,label,created,device_type,backed_up) '
                    'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                    (cid, pending['account_id'], verified.credential_public_key, verified.sign_count,
                     handle, self.rp_id, self.origin, pending['label'], time.time(),
                     verified.credential_device_type.value, int(verified.credential_backed_up)))
            except sqlite3.IntegrityError as exc:
                raise PermissionError('该通行密钥已经注册。') from exc
        return {'account_id': pending['account_id'], 'credential_id': cid, 'label': pending['label']}

    def finish_authentication(self, challenge_id, credential, *, flow_id):
        pending = self._consume_challenge(challenge_id, 'authentication', flow_id)
        try:
            self._credential_policy(credential)
            parsed = parse_authentication_credential_json(credential)
            row = self.store.db.execute('SELECT * FROM passkey_credentials WHERE credential_id=? AND revoked=0 AND rp_id=? AND origin=?',
                                        (parsed.id, self.rp_id, self.origin)).fetchone()
            if not row or not parsed.response.user_handle or not secrets.compare_digest(parsed.response.user_handle, row['user_handle']):
                raise PermissionError('通行密钥账号不匹配。')
            self._enabled(row['account_id'])
            verified = verify_authentication_response(
                credential=parsed, expected_challenge=pending['challenge'], expected_rp_id=self.rp_id,
                expected_origin=self.origin, credential_public_key=row['public_key'],
                credential_current_sign_count=row['sign_count'], require_user_verification=True)
        except Exception as exc:
            raise PermissionError('通行密钥登录失败，请重新验证。') from exc
        with self.store.db:
            self._enabled(row['account_id'])
            changed = self.store.db.execute('UPDATE passkey_credentials SET sign_count=?,last_used=?,device_type=?,backed_up=? '
                'WHERE credential_id=? AND sign_count=? AND revoked=0',
                (verified.new_sign_count, time.time(), verified.credential_device_type.value,
                 int(verified.credential_backed_up), row['credential_id'], row['sign_count']))
            if changed.rowcount != 1:
                raise PermissionError('通行密钥状态已变化，请重新验证。')
        return {'account_id': row['account_id'], 'credential_id': row['credential_id'], 'label': row['label']}
