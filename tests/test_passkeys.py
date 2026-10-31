"""Cryptographic WebAuthn tests using a small software P-256 authenticator.

No verifier is mocked: CBOR attestations, RP hashes, flags, and ECDSA signatures
are passed through the same py_webauthn routines used by the HTTP service.
"""
import hashlib
import json
import secrets
import sqlite3
import stat
import struct
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from webauthn.helpers import bytes_to_base64url, base64url_to_bytes

from local_ai.mobile_auth import MobileAuth
from local_ai.passkeys import PasskeyAuth, configured_origin
from local_ai.store import Store

ORIGIN = 'https://harness.example.test'
RP_ID = 'harness.example.test'
FLOW = 'r' * 43
b64 = bytes_to_base64url


class SoftwareAuthenticator:
    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = secrets.token_bytes(32)
        self.user_handle = None

    def _client(self, options, ceremony, origin=ORIGIN, cross_origin=False):
        return json.dumps({'type': ceremony, 'challenge': options['publicKey']['challenge'],
                           'origin': origin, 'crossOrigin': cross_origin}, separators=(',', ':')).encode()

    def register(self, options, *, origin=ORIGIN, rp_id=RP_ID, flags=0x45, cross_origin=False):
        self.user_handle = base64url_to_bytes(options['publicKey']['user']['id'])
        numbers = self.key.public_key().public_numbers()
        cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, 'big'), -3: numbers.y.to_bytes(32, 'big')})
        auth_data = (hashlib.sha256(rp_id.encode()).digest() + bytes([flags]) + struct.pack('>I', 0)
                     + bytes(16) + struct.pack('>H', len(self.credential_id)) + self.credential_id + cose)
        attestation = cbor2.dumps({'fmt': 'none', 'attStmt': {}, 'authData': auth_data})
        return {'id': b64(self.credential_id), 'rawId': b64(self.credential_id), 'type': 'public-key',
                'response': {'clientDataJSON': b64(self._client(options, 'webauthn.create', origin, cross_origin)),
                             'attestationObject': b64(attestation), 'transports': ['internal']}}

    def authenticate(self, options, *, origin=ORIGIN, rp_id=RP_ID, flags=0x05, count=1,
                     cross_origin=False, user_handle=None):
        client = self._client(options, 'webauthn.get', origin, cross_origin)
        auth_data = hashlib.sha256(rp_id.encode()).digest() + bytes([flags]) + struct.pack('>I', count)
        signature = self.key.sign(auth_data + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return {'id': b64(self.credential_id), 'rawId': b64(self.credential_id), 'type': 'public-key',
                'response': {'clientDataJSON': b64(client), 'authenticatorData': b64(auth_data),
                             'signature': b64(signature), 'userHandle': b64(user_handle or self.user_handle)}}


@pytest.fixture
def auth(tmp_path):
    store = Store(tmp_path / 'private.sqlite3')
    account = store.create_account('Owner', is_admin=True)
    auth = PasskeyAuth(store, ORIGIN)
    auth.account_id = account['id']
    yield auth
    store.db.close()


def register(auth, software=None, *, account_id=None):
    software = software or SoftwareAuthenticator()
    options = auth.begin_registration(auth.invite(account_id or auth.account_id), 'iPhone', flow_id=FLOW)
    result = auth.finish_registration(options['challenge_id'], software.register(options), flow_id=FLOW)
    return software, result


def test_actual_registration_authentication_and_replay(auth):
    key, result = register(auth)
    assert result['account_id'] == auth.account_id
    assert result['credential_id'] == b64(key.credential_id)
    row = auth.require_credential(auth.account_id, result['credential_id'])
    assert row['public_key'] and row['sign_count'] == 0
    options = auth.begin_authentication(flow_id=FLOW)
    assert options['publicKey']['userVerification'] == 'required'
    assert not options['publicKey'].get('allowCredentials')
    response = key.authenticate(options, count=1)
    assert auth.finish_authentication(options['challenge_id'], response, flow_id=FLOW) == result
    assert auth.require_credential(auth.account_id, result['credential_id'])['sign_count'] == 1
    with pytest.raises(PermissionError):
        auth.finish_authentication(options['challenge_id'], response, flow_id=FLOW)
    options = auth.begin_authentication(flow_id=FLOW)
    # A fresh challenge cannot make a replayed signature valid.
    with pytest.raises(PermissionError):
        auth.finish_authentication(options['challenge_id'], response, flow_id=FLOW)


def test_options_require_discoverability_and_verification(auth):
    options = auth.begin_registration(auth.invite(auth.account_id), 'Phone', flow_id=FLOW)['publicKey']
    assert options['attestation'] == 'none'
    assert options['rp']['id'] == RP_ID
    assert options['authenticatorSelection'] == {'residentKey': 'required', 'requireResidentKey': True, 'userVerification': 'required'}
    assert len(base64url_to_bytes(options['challenge'])) == 32
    assert len(base64url_to_bytes(options['user']['id'])) == 32


@pytest.mark.parametrize('mutation', ['origin', 'rp', 'uv', 'up', 'cross_origin', 'challenge', 'signature', 'user_handle', 'missing_handle', 'raw_id', 'counter', 'unknown_key'])
def test_rejects_invalid_cryptographic_authentication(auth, mutation):
    key, _ = register(auth)
    first = auth.begin_authentication(flow_id=FLOW)
    auth.finish_authentication(first['challenge_id'], key.authenticate(first, count=1), flow_id=FLOW)
    options = auth.begin_authentication(flow_id=FLOW)
    kwargs = {'count': 2}
    if mutation == 'origin': kwargs['origin'] = 'https://evil.example.test'
    if mutation == 'rp': kwargs['rp_id'] = 'evil.example.test'
    if mutation == 'uv': kwargs['flags'] = 0x01
    if mutation == 'up': kwargs['flags'] = 0x04
    if mutation == 'cross_origin': kwargs['cross_origin'] = True
    if mutation == 'user_handle': kwargs['user_handle'] = b'wrong account'
    if mutation == 'counter': kwargs['count'] = 1
    response = key.authenticate(options, **kwargs)
    if mutation == 'challenge':
        response = key.authenticate(auth.begin_authentication(flow_id=FLOW), count=2)
    if mutation == 'signature':
        signature = base64url_to_bytes(response['response']['signature'])
        response['response']['signature'] = b64(signature[:-1] + bytes([signature[-1] ^ 1]))
    if mutation == 'missing_handle': response['response'].pop('userHandle')
    if mutation == 'raw_id': response['rawId'] = b64(b'another credential')
    if mutation == 'unknown_key': response['id'] = response['rawId'] = b64(b'unknown credential')
    with pytest.raises(PermissionError):
        auth.finish_authentication(options['challenge_id'], response, flow_id=FLOW)
    # Even failure consumes the browser-bound challenge.
    with pytest.raises(PermissionError):
        auth.finish_authentication(options['challenge_id'], key.authenticate(options, count=2), flow_id=FLOW)


@pytest.mark.parametrize('kwargs', [dict(origin='https://evil.example.test'), dict(rp_id='example.test'),
                                    dict(flags=0x41), dict(flags=0x44), dict(cross_origin=True)])
def test_rejects_invalid_registration(auth, kwargs):
    key = SoftwareAuthenticator()
    options = auth.begin_registration(auth.invite(auth.account_id), 'Phone', flow_id=FLOW)
    with pytest.raises(PermissionError):
        auth.finish_registration(options['challenge_id'], key.register(options, **kwargs), flow_id=FLOW)
    assert not auth.list_credentials(auth.account_id)


def test_invites_are_private_separate_and_consumed_once(auth, monkeypatch):
    native_code = MobileAuth(auth.store).invite(auth.account_id)
    with pytest.raises(PermissionError):
        auth.begin_registration(native_code, 'Phone', flow_id=FLOW)
    code = auth.invite(auth.account_id)
    assert code not in repr([tuple(r) for r in auth.store.db.execute('SELECT * FROM passkey_invites')])
    with pytest.raises(PermissionError):
        MobileAuth(auth.store).pair(code, 'Native')
    auth.begin_registration(code, 'Phone', flow_id=FLOW)
    with pytest.raises(PermissionError):
        auth.begin_registration(code, 'Another', flow_id=FLOW)
    expired = auth.invite(auth.account_id)
    with auth.store.db:
        auth.store.db.execute('UPDATE passkey_invites SET expires=0 WHERE consumed IS NULL')
    with pytest.raises(PermissionError):
        auth.begin_registration(expired, 'Phone', flow_id=FLOW)
    wrong_purpose = auth.invite(auth.account_id)
    with auth.store.db:
        auth.store.db.execute("UPDATE passkey_invites SET purpose='native_pairing' WHERE consumed IS NULL")
    with pytest.raises(PermissionError):
        auth.begin_registration(wrong_purpose, 'Phone', flow_id=FLOW)


def test_flow_binding_expiry_and_durable_restart(auth):
    key, _ = register(auth)
    options = auth.begin_authentication(flow_id=FLOW)
    response = key.authenticate(options)
    with pytest.raises(PermissionError):
        auth.finish_authentication(options['challenge_id'], response, flow_id='another_browser_cookie_' * 3)
    reopened = Store(auth.store.path)
    try:
        restarted = PasskeyAuth(reopened, ORIGIN)
        assert restarted.finish_authentication(options['challenge_id'], response, flow_id=FLOW)['account_id'] == auth.account_id
    finally:
        reopened.db.close()
    with pytest.raises(PermissionError):
        auth.finish_authentication(options['challenge_id'], response, flow_id=FLOW)
    options = auth.begin_authentication(flow_id=FLOW)
    with auth.store.db:
        auth.store.db.execute('UPDATE passkey_challenges SET expires=0 WHERE id=?', (options['challenge_id'],))
    with pytest.raises(PermissionError):
        auth.finish_authentication(options['challenge_id'], key.authenticate(options, count=2), flow_id=FLOW)


def test_credentials_cannot_cross_accounts_or_be_reassigned(auth):
    key, result = register(auth)
    another = auth.store.create_account('Other')['id']
    with pytest.raises(PermissionError):
        auth.require_credential(another, result['credential_id'])
    with pytest.raises(PermissionError):
        auth.revoke(another, result['credential_id'])
    with pytest.raises(PermissionError):
        register(auth, key, account_id=another)
    assert not auth.list_credentials(another)
    assert auth.require_credential(auth.account_id, result['credential_id'])['account_id'] == auth.account_id


def test_revocation_and_disabled_account_block_login(auth):
    key, result = register(auth)
    options = auth.begin_authentication(flow_id=FLOW)
    auth.revoke(auth.account_id, result['credential_id'])
    with pytest.raises(PermissionError):
        auth.finish_authentication(options['challenge_id'], key.authenticate(options), flow_id=FLOW)
    with pytest.raises(PermissionError):
        auth.require_credential(auth.account_id, result['credential_id'])
    key, result = register(auth)
    options = auth.begin_authentication(flow_id=FLOW)
    with auth.store.db:
        auth.store.db.execute('UPDATE accounts SET enabled=0 WHERE id=?', (auth.account_id,))
    with pytest.raises(PermissionError):
        auth.finish_authentication(options['challenge_id'], key.authenticate(options), flow_id=FLOW)
    with pytest.raises(PermissionError):
        auth.invite(auth.account_id)
    with pytest.raises(PermissionError):
        auth.require_credential(auth.account_id, result['credential_id'])


def test_zero_counters_supported_for_synced_passkeys(auth):
    key, _ = register(auth)
    for _ in range(2):
        options = auth.begin_authentication(flow_id=FLOW)
        assert auth.finish_authentication(options['challenge_id'], key.authenticate(options, count=0), flow_id=FLOW)


@pytest.mark.parametrize('ceremony', ['invitation', 'authentication', 'registration'])
def test_two_database_connections_cannot_consume_same_ceremony(auth, ceremony):
    key, _ = register(auth)
    if ceremony == 'invitation':
        code = auth.invite(auth.account_id)
        invoke = lambda manager: manager.begin_registration(code, 'Phone', flow_id=FLOW)
    elif ceremony == 'authentication':
        options = auth.begin_authentication(flow_id=FLOW)
        response = key.authenticate(options)
        invoke = lambda manager: manager.finish_authentication(options['challenge_id'], response, flow_id=FLOW)
    else:
        options = auth.begin_registration(auth.invite(auth.account_id), 'Another', flow_id=FLOW)
        response = SoftwareAuthenticator().register(options)
        invoke = lambda manager: manager.finish_registration(options['challenge_id'], response, flow_id=FLOW)
    barrier = threading.Barrier(2)

    def attempt():
        store = Store(auth.store.path)
        try:
            manager = PasskeyAuth(store, ORIGIN)
            barrier.wait(timeout=5)
            try:
                invoke(manager)
                return 'accepted'
            except PermissionError:
                return 'rejected'
        finally:
            store.db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(results) == ['accepted', 'rejected']


@pytest.mark.parametrize('origin', ['', 'https://good.test/path', 'https://u:p@good.test', 'https://good.test?q=x',
                                  'https://good.test#x', 'http://good.test', 'http://127.0.0.1:8080',
                                  'https://127.0.0.1', 'https://good.test:bad', 'https://good.test\\evil',
                                  'https://good.test\n', 'https://-bad.test', 'https://bad..test'])
def test_invalid_origins_rejected(origin):
    with pytest.raises(ValueError): configured_origin(origin)


def test_explicit_origin_normalization_and_no_implicit_origin(auth):
    assert configured_origin('https://Harness.Example.test:443/') == (ORIGIN, RP_ID)
    assert configured_origin('http://localhost:8080') == ('http://localhost:8080', 'localhost')
    manager = PasskeyAuth(auth.store)
    assert manager.invite(auth.account_id)
    with pytest.raises(ValueError): manager.begin_authentication(flow_id=FLOW)
    for bad_flow in ('', 'short', 'a ' * 32, None):
        with pytest.raises(PermissionError): auth.begin_authentication(flow_id=bad_flow)


def test_cli_registration_invite_list_revoke(tmp_path):
    db = tmp_path / 'cli.sqlite3'
    store = Store(db)
    account_id = store.create_account('Owner')['id']
    auth = PasskeyAuth(store, ORIGIN)
    auth.account_id = account_id
    _, result = register(auth)
    store.db.close()
    command = [sys.executable, str(Path(__file__).resolve().parents[1] / 'scripts/mobile_admin.py'), '--database', str(db)]
    invited = subprocess.run(command + ['passkey-invite', '--account', account_id], check=True, capture_output=True, text=True)
    assert 'One-use passkey registration code' in invited.stdout
    listing = subprocess.run(command + ['passkeys', '--account', account_id], check=True, capture_output=True, text=True)
    assert result['credential_id'] in listing.stdout
    subprocess.run(command + ['revoke-passkey', '--', account_id, result['credential_id']], check=True, capture_output=True)
    store = Store(db)
    with pytest.raises(PermissionError):
        PasskeyAuth(store, ORIGIN).require_credential(account_id, result['credential_id'])
    store.db.close()


def test_first_passkey_migration_backs_up_existing_data_once(tmp_path):
    path = tmp_path / 'existing.sqlite3'
    store = Store(path)
    account = store.create_account('Existing owner')
    session = store.create_session(account['id'], 'Existing workspace')
    with store.db:
        store.db.execute('INSERT INTO history(user_id,role,content) VALUES (?,?,?)',
                         (account['user_id'], 'user', 'Existing private history'))
    PasskeyAuth(store, ORIGIN)
    backups = list(tmp_path.glob('existing.sqlite3.pre-passkeys-*.bak'))
    assert len(backups) == 1
    assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert backup.execute('SELECT display_name FROM accounts WHERE id=?', (account['id'],)).fetchone()[0] == 'Existing owner'
        assert backup.execute('SELECT name FROM sessions WHERE id=?', (session['id'],)).fetchone()[0] == 'Existing workspace'
        assert backup.execute('SELECT content FROM history').fetchone()[0] == 'Existing private history'
        assert not backup.execute("SELECT 1 FROM sqlite_master WHERE name='passkey_credentials'").fetchone()
    PasskeyAuth(store, ORIGIN)
    assert len(list(tmp_path.glob('existing.sqlite3.pre-passkeys-*.bak'))) == 1
    assert store.require_session(account['id'], session['id'])['name'] == 'Existing workspace'
    store.db.close()


def test_migration_backup_never_overwrites_existing_path(tmp_path, monkeypatch):
    path = tmp_path / 'db'
    store = Store(path)
    monkeypatch.setattr('local_ai.passkeys.time.time_ns', lambda: 123)
    collision = tmp_path / 'db.pre-passkeys-123.bak'
    collision.write_text('Must survive')
    with pytest.raises(FileExistsError):
        PasskeyAuth(store, ORIGIN)
    assert collision.read_text() == 'Must survive'
    assert not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='passkey_credentials'").fetchone()
    store.db.close()
