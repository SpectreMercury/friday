"""Security regression tests at asynchronous and lifecycle boundaries.

Uses real WebAuthn, SQLite and HTTP; only GPU speech and Google transport are fake.
"""
import asyncio
import json
import time
from urllib.parse import parse_qs, urlsplit
from unittest.mock import AsyncMock

import aiohttp
import pytest
from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient, TestServer

from local_ai.browser_auth import BrowserAuth
from local_ai.connectors import MemorySecretStore
from local_ai.google_picker import GooglePicker
from local_ai.mobile_api import create_app
from local_ai.mobile_auth import MobileAuth
from test_browser_api import browser_api, register
from test_mobile_voice import Worker
from test_passkeys import ORIGIN, b64


async def new_session(api, login):
    response = await api.client.post('/v1/sessions', json={'name': 'Private workspace'}, headers=login.headers)
    assert response.status == 201, await response.text()
    return await response.json()


async def test_absolute_expiry_does_not_slide_with_activity(browser_api):
    api = browser_api
    login = await register(api)
    did = api.auth.public_session(login.token)['device_id']
    expiry = api.store.db.execute('SELECT expires FROM browser_sessions WHERE device_id=?', (did,)).fetchone()[0]
    with api.store.db:
        api.store.db.execute('UPDATE browser_sessions SET last_seen=? WHERE device_id=?', (time.time() - 120, did))
    assert (await api.client.get('/v1/sessions', headers=login.headers)).status == 200
    row = api.store.db.execute('SELECT expires,last_seen FROM browser_sessions WHERE device_id=?', (did,)).fetchone()
    assert row['expires'] == expiry and row['last_seen'] > time.time() - 10
    with api.store.db:
        api.store.db.execute('UPDATE browser_sessions SET expires=?,last_seen=? WHERE device_id=?', (time.time() - 1, time.time(), did))
    assert (await api.client.get('/v1/sessions', headers=login.headers)).status == 401
    assert not api.auth.device_active(did)
    entry = await api.client.get('/app', headers=login.headers, allow_redirects=False)
    assert entry.status == 303 and entry.headers['Location'] == '/login'


async def test_new_origin_invalidates_previous_browser_cookie(browser_api):
    api = browser_api
    login = await register(api)
    new_origin = 'https://replacement.example.test'
    replacement = BrowserAuth(api.store, new_origin)
    with pytest.raises(PermissionError):
        replacement.authenticate(login.token)
    async with TestClient(TestServer(create_app(api.service, web_origin=new_origin)), cookie_jar=aiohttp.DummyCookieJar()) as client:
        response = await client.get('/v1/sessions', headers={**login.headers, 'Origin': new_origin})
        assert response.status == 401
        # An attacker cannot restore acceptance by supplying the old trusted origin.
        response = await client.get('/v1/sessions', headers=login.headers)
        assert response.status == 401


async def test_enabling_pairing_alone_cannot_bypass_pwa_native_gate(browser_api, monkeypatch):
    api = browser_api
    native = MobileAuth(api.store)
    account = api.owner['id']
    device = native.pair(native.invite(account), 'Previously paired phone')
    invitation = native.invite(account)
    monkeypatch.setenv('MOBILE_NATIVE_PAIRING', '1')
    monkeypatch.delenv('MOBILE_NATIVE_ENABLED', raising=False)
    async with TestClient(TestServer(create_app(api.service, web_origin=ORIGIN)), cookie_jar=aiohttp.DummyCookieJar()) as client:
        headers = {'Authorization': 'Bearer ' + device['token']}
        for path in ('/pwa/app.js', '/v1/sessions', '/v1/devices'):
            assert (await client.get(path, headers=headers)).status == 401
        assert (await client.post('/v1/sessions', json={'name': 'Bypass'}, headers=headers)).status == 401
        assert (await client.post('/v1/auth/renew', headers=headers)).status == 401
        assert (await client.post('/v1/pair', json={'code': invitation, 'device_name': 'Bypass'})).status == 404
    assert not api.store.list_sessions(account)


def make_private_picker(api):
    manager = api.store.connectors
    manager.config = {'MOBILE_PUBLIC_URL': ORIGIN, 'GOOGLE_PICKER_API_KEY': 'test-key', 'GOOGLE_PICKER_APP_ID': '12345'}
    manager.secrets = MemorySecretStore()
    manager.secrets.set('test-google-secret', json.dumps({'access_token': 'fake-short-lived-access', 'expires_at': time.time() + 3600}))
    with api.store.db:
        api.store.db.execute('INSERT INTO connector_connections VALUES (?,?,?,?,?,?,?)',
            ('test-google', api.owner['id'], 'google', 'Test Google', 'connected', 'test-google-secret', time.time()))
    manager.request = AsyncMock(side_effect=lambda method, url, **kwargs: {'id': url.rsplit('/', 1)[-1]})
    picker = GooglePicker(manager, public_origin=ORIGIN, device_authorizer=api.auth.device_active)
    return picker, manager


def picker_ticket(picker, api, session, login):
    did = api.auth.public_session(login.token)['device_id']
    result = picker.start(api.owner['id'], session['id'], 'test-google', session['revision'], did)
    return parse_qs(urlsplit(result['url']).query)['ticket'][0]


@pytest.mark.parametrize('opened', [False, True])
async def test_revoked_passkey_invalidates_outstanding_picker_ticket(browser_api, opened):
    api = browser_api
    login = await register(api)
    session = await new_session(api, login)
    picker, manager = make_private_picker(api)
    ticket = picker_ticket(picker, api, session, login)
    if opened:
        await picker.page(ticket)
    api.auth.passkeys.revoke(api.owner['id'], b64(login.key.credential_id))
    with pytest.raises(PermissionError):
        if opened:
            await picker.select(ticket, ['document'])
        else:
            await picker.page(ticket)
    manager.request.assert_not_awaited()
    assert manager.list_bindings(api.owner['id'], session['id'])['bindings'] == []


async def test_picker_async_google_reply_cannot_save_after_logout(browser_api):
    api = browser_api
    login = await register(api)
    session = await new_session(api, login)
    picker, manager = make_private_picker(api)
    ticket = picker_ticket(picker, api, session, login)
    await picker.page(ticket)
    reached_google, release = asyncio.Event(), asyncio.Event()
    async def delayed_google(method, url, **kwargs):
        reached_google.set()
        await release.wait()
        return {'id': 'document'}
    manager.request = delayed_google
    pending = asyncio.create_task(picker.select(ticket, ['document']))
    await asyncio.wait_for(reached_google.wait(), 2)
    assert (await api.client.post('/v1/web/auth/logout', headers=login.headers)).status == 200
    release.set()
    with pytest.raises(PermissionError):
        await pending
    assert manager.list_bindings(api.owner['id'], session['id'])['bindings'] == []


async def wait_until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(.01)


@pytest.mark.parametrize('kind', ['file', 'voice_message'])
async def test_upload_revocation_prevents_replacement_and_enqueue(browser_api, kind):
    api = browser_api
    login = await register(api)
    session = await new_session(api, login)
    workspace = api.store.workspace_directory(api.owner['id'], session['id'])
    original_file = workspace / 'same.txt'
    original_file.write_text('Original private contents')
    release = asyncio.Event()
    prefix = '/v1/sessions/' + session['id']
    if kind == 'file':
        boundary = 'HarnessSecurityBoundary'
        initial = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="same.txt"\r\n'
                   'Content-Type: text/plain\r\n\r\n').encode() + b'x' * 12000
        final = f'\r\n--{boundary}--\r\n'.encode()
        path, content_type = prefix + '/files', 'multipart/form-data; boundary=' + boundary
        staged = lambda: list(workspace.glob('.upload-*'))
    else:
        initial, final = b'\x00\x00\x00\x18ftypM4A ' + b'\x00' * 32, b'\x00' * 32
        path, content_type = prefix + '/voice-messages', 'audio/mp4'
        staged = lambda: list((api.store.path.parent / 'incoming').glob('*.m4a'))
    async def chunks():
        yield initial
        await release.wait()
        yield final
    pending = asyncio.create_task(api.client.post(path, data=chunks(),
        headers={**login.headers, 'Content-Type': content_type, 'Idempotency-Key': 'late-upload'}))
    try:
        await wait_until(staged)
        api.auth.passkeys.revoke(api.owner['id'], b64(login.key.credential_id))
    finally:
        release.set()
    response = await pending
    assert response.status == 403, await response.text()
    assert original_file.read_text() == 'Original private contents'
    assert not staged()
    assert api.store.db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 0
    assert api.store.db.execute('SELECT count(*) FROM attachments').fetchone()[0] == 0


@pytest.mark.parametrize('action', ['logout', 'revoke_device'])
async def test_logout_and_device_revocation_end_actual_voice_connection(browser_api, action):
    api = browser_api
    phone = await register(api, label='Phone')
    control = await register(api, label='Control')
    session = await new_session(api, phone)
    did = api.auth.public_session(phone.token)['device_id']
    hub = api.service.mobile_voice
    hub.worker = Worker()
    hub.engine.reserve_voice = AsyncMock()
    hub.engine.release_voice = AsyncMock()
    response = await api.client.post('/v1/sessions/' + session['id'] + '/voice', headers=phone.headers)
    assert response.status == 201, await response.text()
    voice = await response.json()
    socket = await api.client.ws_connect(voice['stream_path'], headers=phone.headers)
    assert (await socket.receive_json())['type'] == 'ready'
    if action == 'logout':
        response = await api.client.post('/v1/web/auth/logout', headers=phone.headers)
    else:
        response = await api.client.delete('/v1/devices/' + did, headers=control.headers)
    assert response.status == 200, await response.text()
    async with asyncio.timeout(2):
        while not socket.closed:
            message = await socket.receive()
            if message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING):
                break
    await socket.close()
    assert not hub.active
    hub.engine.release_voice.assert_awaited_once_with(voice['id'])
    assert (await api.client.delete('/v1/voice/' + voice['id'], headers=control.headers)).status == 403
    # The old mapping must be gone and the sole voice slot reusable immediately.
    response = await api.client.post('/v1/sessions/' + session['id'] + '/voice', headers=control.headers)
    assert response.status == 201, await response.text()
    next_voice = await response.json()
    assert next_voice['id'] != voice['id']
    assert (await api.client.delete('/v1/voice/' + next_voice['id'], headers=control.headers)).status == 200


@pytest.mark.parametrize('kind', ['file', 'artifact'])
async def test_active_html_downloads_are_inert_and_cannot_cross_workspaces(browser_api, kind):
    api = browser_api
    login = await register(api)
    foreign = await register(api, api.friend)
    first, second = await new_session(api, login), await new_session(api, login)
    html = b'<!doctype html><script>window.__HARNESS_DOWNLOAD_EXECUTED__=true</script>'
    prefix = '/v1/sessions/' + first['id']
    if kind == 'file':
        form = aiohttp.FormData()
        form.add_field('file', html, filename='untrusted.html', content_type='text/html')
        uploaded = await api.client.post(prefix + '/files', data=form, headers=login.headers)
        assert uploaded.status == 201, await uploaded.text()
        path = prefix + '/files/untrusted.html'
        other_workspace_path = '/v1/sessions/' + second['id'] + '/files/untrusted.html'
        other_workspace_status = 404
    else:
        output = api.store.path.parent / 'generated-untrusted.html'
        output.write_bytes(html)
        jid = api.store.enqueue(api.owner['user_id'], None, 'image', 'Test artifact', session_id=first['id'])
        with api.store.db:
            api.store.db.execute("UPDATE jobs SET result=?,status='succeeded' WHERE id=?", (str(output), jid))
        path = prefix + '/tasks/' + jid + '/artifact'
        other_workspace_path = '/v1/sessions/' + second['id'] + '/tasks/' + jid + '/artifact'
        other_workspace_status = 403
    response = await api.client.get(path, headers=login.headers)
    assert response.status == 200 and await response.read() == html
    assert response.content_type == 'application/octet-stream'
    assert response.headers['Content-Disposition'] == 'attachment'
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert response.headers['Cache-Control'] == 'no-store'
    preview = await api.client.get(path+'?preview=1', headers=login.headers)
    assert preview.status == 415
    assert (await api.client.get(path)).status == 401
    assert (await api.client.get(path, headers=foreign.headers)).status == 403
    assert (await api.client.get(other_workspace_path, headers=login.headers)).status == other_workspace_status
