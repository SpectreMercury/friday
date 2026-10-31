"""Real HTTP + real WebAuthn signatures; no GPU, account or external service."""
import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from local_ai.browser_auth import BrowserAuth
from local_ai.harness import HarnessService
from local_ai.mobile_api import create_app
from local_ai.mobile_auth import MobileAuth
from local_ai.store import Store
from test_passkeys import SoftwareAuthenticator, ORIGIN, b64


@pytest.fixture
async def browser_api(tmp_path, monkeypatch):
    monkeypatch.delenv('MOBILE_NATIVE_PAIRING', raising=False)
    monkeypatch.delenv('MOBILE_NATIVE_ENABLED', raising=False)
    for name in ('APNS_TEAM_ID', 'APNS_KEY_ID', 'APNS_KEY_FILE', 'APNS_BUNDLE_ID'):
        monkeypatch.delenv(name, raising=False)
    store = Store(tmp_path / 'db')
    owner = store.create_account('Owner', is_admin=True)
    friend = store.create_account('Friend')
    engine = SimpleNamespace(speech_worker=None, lock=asyncio.Lock(),
        start=AsyncMock(side_effect=AssertionError('Never start GPU in ingress tests')),
        stop=AsyncMock(), process=None, voice_reserved=False)
    service = HarnessService(tmp_path, store=store, engine=engine)
    app = create_app(service, web_origin=ORIGIN)
    auth = BrowserAuth(store, ORIGIN)
    async with TestClient(TestServer(app), cookie_jar=aiohttp.DummyCookieJar()) as client:
        env = SimpleNamespace(client=client, store=store, service=service, auth=auth, owner=owner, friend=friend)
        yield env
    store.db.close()


async def register(api, account=None, label='iPhone'):
    account = account or api.owner
    key = SoftwareAuthenticator()
    code = api.auth.passkeys.invite(account['id'])
    response = await api.client.post('/v1/web/auth/register/options',
        json={'code': code, 'label': label}, headers={'Origin': ORIGIN})
    assert response.status == 200, await response.text()
    options = await response.json()
    flow = response.cookies[api.auth.flow_cookie_name].value
    response = await api.client.post('/v1/web/auth/register/verify',
        json={'challenge_id': options['challenge_id'], 'credential': key.register(options)},
        headers={'Origin': ORIGIN, 'Cookie': api.auth.flow_cookie_name + '=' + flow})
    assert response.status == 200, await response.text()
    assert await response.json() == {'authenticated': True}
    token = response.cookies[api.auth.cookie_name].value
    headers = {'Cookie': api.auth.cookie_name + '=' + token, 'Origin': ORIGIN,
               'X-CSRF-Token': api.auth.csrf(token), 'Sec-Fetch-Site': 'same-origin'}
    return SimpleNamespace(key=key, token=token, headers=headers, cookie=response.cookies[api.auth.cookie_name])


async def test_private_entry_assets_and_every_api_require_identity(browser_api):
    api = browser_api
    for path in ('/', '/app'):
        response = await api.client.get(path, allow_redirects=False)
        assert response.status == 303 and response.headers['Location'] == '/login'
    for path in ('/login', '/pwa/login.js', '/pwa/auth.js', '/pwa/style.css', '/pwa/icon.svg', '/pwa/sw.js', '/pwa/manifest.webmanifest',
                 '/pwa/friday-icon.png', '/pwa/friday-icon-192.png', '/pwa/friday-icon-512.png', '/pwa/friday-apple-touch-icon.png'):
        response = await api.client.get(path)
        assert response.status == 200, path
        assert response.headers['Cache-Control'] == 'no-store'
        assert response.headers['X-Frame-Options'] == 'DENY'
        assert "frame-ancestors 'none'" in response.headers['Content-Security-Policy']
    for path in ('/pwa/app.js', '/pwa/core.js', '/pwa/voice.js', '/pwa/pcm-worklet.js',
                 '/v1/sessions', '/v1/tasks', '/v1/events', '/v1/devices', '/v1/connectors',
                 '/v1/web/auth/session', '/v1/sessions/secret/files/private.txt', '/v1/voice/secret/stream'):
        response = await api.client.get(path)
        assert response.status == 401, (path, await response.text())
        assert response.headers['Cache-Control'] == 'no-store'
    native = MobileAuth(api.store).invite(api.owner['id'])
    assert (await api.client.post('/v1/pair', json={'code': native, 'device_name': 'bypass'})).status == 404
    assert api.store.db.execute('SELECT count(*) FROM mobile_devices').fetchone()[0] == 0


async def test_registration_sets_private_cookie_and_uses_existing_session_isolation(browser_api):
    api = browser_api
    owner = await register(api)
    cookie = owner.cookie
    assert cookie['secure'] and cookie['httponly'] and cookie['samesite'] == 'Strict'
    assert cookie['path'] == '/' and not cookie['domain'] and int(cookie['max-age']) == 7 * 86400
    status = await (await api.client.get('/v1/web/auth/session', headers=owner.headers)).json()
    assert status['account']['id'] == api.owner['id'] and status['account']['is_admin']
    assert status['csrf_token'] == api.auth.csrf(owner.token)
    assert owner.token not in json.dumps(status)
    app = await api.client.get('/app', headers=owner.headers)
    assert app.status == 200 and 'app.js' in await app.text()
    session = await (await api.client.post('/v1/sessions', json={'name': 'private'}, headers=owner.headers)).json()
    foreign = await register(api, api.friend)
    assert (await api.client.get('/v1/sessions/' + session['id'], headers=foreign.headers)).status == 403
    assert (await api.client.get('/v1/sessions', headers={'Authorization': 'Bearer ' + owner.token})).status == 401
    assert (await api.client.post('/v1/auth/renew', headers=owner.headers)).status == 403
    rows = [tuple(r) for r in api.store.db.execute('SELECT * FROM browser_sessions')]
    assert owner.token not in repr(rows)


async def test_writes_reject_csrf_cross_site_and_missing_origin(browser_api):
    api = browser_api
    login = await register(api)
    cases = [
        {k: v for k, v in login.headers.items() if k != 'X-CSRF-Token'},
        {**login.headers, 'X-CSRF-Token': 'forged'},
        {**login.headers, 'Origin': 'https://evil.example.test'},
        {**login.headers, 'Origin': 'null'},
        {k: v for k, v in login.headers.items() if k != 'Origin'},
        {**login.headers, 'Sec-Fetch-Site': 'same-site'},
        {**login.headers, 'Sec-Fetch-Site': 'cross-site'},
    ]
    for headers in cases:
        response = await api.client.post('/v1/sessions', json={'name': 'attack'}, headers=headers)
        assert response.status == 403, await response.text()
    assert api.store.list_sessions(api.owner['id']) == []
    forged_read = await api.client.get('/v1/sessions', headers={**login.headers, 'Origin': 'https://evil.example.test'})
    assert forged_read.status == 403
    assert 'Access-Control-Allow-Origin' not in forged_read.headers


async def test_ceremonies_need_exact_origin_json_and_browser_bound_flow(browser_api):
    api = browser_api
    for headers in ({}, {'Origin': 'null'}, {'Origin': 'https://evil.example.test'}, {'Origin': ORIGIN, 'Sec-Fetch-Site': 'cross-site'}):
        assert (await api.client.post('/v1/web/auth/login/options', json={}, headers=headers)).status == 403
    assert (await api.client.post('/v1/web/auth/login/options', data='{}', headers={'Origin': ORIGIN})).status == 400
    login = await register(api)
    response = await api.client.post('/v1/web/auth/login/options', json={}, headers={'Origin': ORIGIN})
    options = await response.json()
    flow = response.cookies[api.auth.flow_cookie_name].value
    payload = {'challenge_id': options['challenge_id'], 'credential': login.key.authenticate(options)}
    missing = await api.client.post('/v1/web/auth/login/verify', json=payload, headers={'Origin': ORIGIN})
    assert missing.status == 403 and api.auth.cookie_name not in missing.cookies
    good = await api.client.post('/v1/web/auth/login/verify', json=payload,
        headers={'Origin': ORIGIN, 'Cookie': api.auth.flow_cookie_name + '=' + flow})
    assert good.status == 200
    replay = await api.client.post('/v1/web/auth/login/verify', json=payload,
        headers={'Origin': ORIGIN, 'Cookie': api.auth.flow_cookie_name + '=' + flow})
    assert replay.status == 403


async def test_logout_revocation_expiry_and_disabled_account(browser_api):
    api = browser_api
    login = await register(api)
    status = api.auth.public_session(login.token)
    with api.store.db:
        api.store.db.execute('UPDATE browser_sessions SET last_seen=? WHERE device_id=?',
            (time.time() - api.auth.idle_timeout - 1, status['device_id']))
    assert (await api.client.get('/v1/sessions', headers=login.headers)).status == 401
    assert not api.auth.device_active(status['device_id'])
    login = await register(api)
    response = await api.client.post('/v1/web/auth/logout', headers=login.headers)
    assert response.status == 200 and response.cookies[api.auth.cookie_name]['max-age'] == '0'
    assert 'storage' in response.headers['Clear-Site-Data']
    assert (await api.client.get('/v1/sessions', headers=login.headers)).status == 401
    login = await register(api)
    api.auth.passkeys.revoke(api.owner['id'], b64(login.key.credential_id))
    assert (await api.client.get('/v1/sessions', headers=login.headers)).status == 401
    login = await register(api)
    did = api.auth.public_session(login.token)['device_id']
    MobileAuth(api.store).revoke(api.owner['id'], did)
    assert (await api.client.get('/v1/sessions', headers=login.headers)).status == 401
    login = await register(api)
    api.store.set_access(api.owner['user_id'], False)
    assert (await api.client.get('/v1/sessions', headers=login.headers)).status == 401


async def test_browser_uploads_and_pwa_only_rejects_native_credentials(browser_api):
    api = browser_api
    login = await register(api)
    session = await (await api.client.post('/v1/sessions', json={'name': 'files'}, headers=login.headers)).json()
    form = aiohttp.FormData()
    form.add_field('file', b'private contents', filename='private.txt')
    path = '/v1/sessions/' + session['id']
    assert (await api.client.post(path + '/files', data=form, headers=login.headers)).status == 201
    download = await api.client.get(path + '/files/private.txt', headers=login.headers)
    assert await download.read() == b'private contents' and download.headers['Content-Disposition'] == 'attachment'
    audio = b'\x00\x00\x00\x18ftypM4A ' + b'\x00' * 32
    response = await api.client.post(path + '/voice-messages', data=audio,
        headers={**login.headers, 'Content-Type': 'audio/mp4', 'Idempotency-Key': 'memo'})
    assert response.status == 201, await response.text()
    assert (await response.json())['session_id'] == session['id']
    native_auth = MobileAuth(api.store)
    native = native_auth.pair(native_auth.invite(api.owner['id']), 'Existing native device')
    headers = {'Authorization': 'Bearer ' + native['token']}
    assert (await api.client.get(path + '/files/private.txt', headers=headers)).status == 401
    assert (await api.client.get('/v1/web/auth/session', headers=headers)).status == 401


async def test_slow_request_cannot_mutate_after_passkey_revocation(browser_api):
    api = browser_api
    login = await register(api)
    reached_body = asyncio.Event()
    release = asyncio.Event()
    # The wrapper observes an actual successful ingress authentication before
    # releasing the last body chunk. No authentication/crypto result is mocked.
    original = BrowserAuth.authenticate
    def observed(self, token, *, touch=True):
        result = original(self, token, touch=touch)
        if token == login.token:
            reached_body.set()
        return result
    from unittest.mock import patch
    async def chunks():
        yield b'{"name":"'
        await release.wait()
        yield b'late mutation"}'
    with patch.object(BrowserAuth, 'authenticate', observed):
        pending = asyncio.create_task(api.client.post('/v1/sessions', data=chunks(),
            headers={**login.headers, 'Content-Type': 'application/json'}))
        await asyncio.wait_for(reached_body.wait(), 2)
        api.auth.passkeys.revoke(api.owner['id'], b64(login.key.credential_id))
        release.set()
        response = await pending
    assert response.status == 403
    assert api.store.list_sessions(api.owner['id']) == []


async def test_websocket_requires_cookie_and_exact_origin_not_query_tokens(browser_api):
    api = browser_api
    login = await register(api)
    current = await (await api.client.post('/v1/sessions', json={'name': 'voice'}, headers=login.headers)).json()
    hub = api.service.mobile_voice
    hub.create = AsyncMock(return_value={'id': 'test-voice', 'session_id': current['id']})
    async def stream(account_id, voice_id, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({'type': 'ready'})
        await ws.close()
        return ws
    hub.stream = stream
    assert (await api.client.post('/v1/sessions/' + current['id'] + '/voice', headers=login.headers)).status == 201
    path = '/v1/voice/test-voice/stream'
    for headers in ({'Cookie': login.headers['Cookie']}, {**login.headers, 'Origin': 'https://evil.example.test'}):
        with pytest.raises(aiohttp.WSServerHandshakeError) as caught:
            await api.client.ws_connect(path, headers=headers)
        assert caught.value.status == 403
    with pytest.raises(aiohttp.WSServerHandshakeError) as caught:
        await api.client.ws_connect(path + '?token=' + login.token, headers={'Origin': ORIGIN})
    assert caught.value.status == 401
    async with api.client.ws_connect(path, headers=login.headers) as socket:
        assert await socket.receive_json() == {'type': 'ready'}


async def test_ceremony_rate_limit_cannot_be_bypassed_with_forwarded_ip(browser_api):
    api = browser_api
    for index in range(30):
        response = await api.client.post('/v1/web/auth/login/options', json={},
            headers={'Origin': ORIGIN, 'X-Forwarded-For': f'10.0.0.{index}'})
        assert response.status == 200
    response = await api.client.post('/v1/web/auth/login/options', json={}, headers={'Origin': ORIGIN, 'X-Forwarded-For': 'another'})
    assert response.status == 429 and response.headers['Retry-After'] == '60'
    assert api.store.db.execute('SELECT count(*) FROM passkey_challenges').fetchone()[0] == 30
