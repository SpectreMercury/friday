"""Real HTTP ingress and durable boundaries, with no GPU or external network."""
import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer

from local_ai.approvals import ApprovalPending
from local_ai.attachments import Attachments
from local_ai.connectors import ConnectorManager, GOOGLE_SCOPE, MemorySecretStore
from local_ai.harness import HarnessService
from local_ai.mobile_api import create_app
from local_ai.mobile_auth import MobileAuth
from local_ai.sessions import SessionSkills
from local_ai.store import Store


@pytest.fixture
async def api(tmp_path, monkeypatch):
    # These tests exercise native bearer ingress independently of a developer's
    # deployed PWA settings (LocalBot may load .env in the same pytest process).
    monkeypatch.setenv('PWA_ENABLED', '0')
    monkeypatch.setenv('MOBILE_NATIVE_ENABLED', '1')
    monkeypatch.setenv('MOBILE_NATIVE_PAIRING', '1')
    for key in ('APNS_TEAM_ID', 'APNS_KEY_ID', 'APNS_KEY_FILE', 'APNS_BUNDLE_ID'):
        monkeypatch.delenv(key, raising=False)
    skills = tmp_path / 'skills/research'
    skills.mkdir(parents=True)
    (skills / 'SKILL.md').write_text('Research\nVERSION ONE')
    store = Store(tmp_path / 'db', skills_root=skills.parent)
    owner = store.create_account('Owner', is_admin=True)
    friend = store.create_account('Friend')
    engine = SimpleNamespace(speech_worker=None, lock=asyncio.Lock(),
        start=AsyncMock(side_effect=AssertionError('API tests must never load a model')),
        stop=AsyncMock(), process=None, voice_reserved=False)
    service = HarnessService(tmp_path, store=store, engine=engine)
    auth = MobileAuth(store)
    async with TestClient(TestServer(create_app(service))) as client:
        async def pair(account, name='iPhone'):
            code = auth.invite(account['id'])
            response = await client.post('/v1/pair', json={'code': code, 'device_name': name})
            assert response.status == 200, await response.text()
            data = await response.json()
            return data, {'Authorization': 'Bearer ' + data['token']}
        own_device, own_headers = await pair(owner)
        friend_device, friend_headers = await pair(friend)
        yield SimpleNamespace(client=client, store=store, service=service, auth=auth,
            owner=owner, friend=friend, headers=own_headers, other_headers=friend_headers,
            device=own_device, other_device=friend_device, pair=pair, skills=skills)
    store.db.close()


async def create_session(api, name, headers=None):
    response = await api.client.post('/v1/sessions', json={'name': name}, headers=headers or api.headers)
    assert response.status == 201, await response.text()
    return await response.json()


async def submit(api, session, text='message', request_id='request', headers=None):
    response = await api.client.post(f"/v1/sessions/{session['id']}/messages",
        json={'text': text, 'request_id': request_id}, headers=headers or api.headers)
    assert response.status == 201, await response.text()
    return await response.json()


async def upload(api, session, filename, data):
    form = aiohttp.FormData()
    form.add_field('file', data, filename=filename, content_type='application/octet-stream')
    response = await api.client.post(f"/v1/sessions/{session['id']}/files", data=form, headers=api.headers)
    assert response.status == 201, await response.text()
    return await response.json()


async def test_pairing_one_use_bearer_only_and_public_shapes(api):
    assert (await api.client.get('/healthz')).status == 200
    assert (await api.client.get('/v1/sessions')).status == 401
    assert (await api.client.get('/v1/sessions', headers={'Authorization': 'Basic abc'})).status == 401
    code = api.auth.invite(api.owner['id'])
    first = await api.client.post('/v1/pair', json={'code': code, 'device_name': 'Second iPhone'})
    assert first.status == 200
    assert first.headers['Cache-Control'] == 'no-store'
    repeated = await api.client.post('/v1/pair', json={'code': code, 'device_name': 'Reuse'})
    assert repeated.status == 403
    expired = api.auth.invite(api.owner['id'], ttl=-1)
    assert (await api.client.post('/v1/pair', json={'code': expired, 'device_name': 'Expired'})).status == 403
    session = await create_session(api, 'Private')
    assert session['skill_names'] == [] and not session['management']
    assert 'workspace_key' not in session and 'account_id' not in session
    devices = await (await api.client.get('/v1/devices', headers=api.headers)).json()
    assert api.device['token'] not in json.dumps(devices) and 'token_hash' not in json.dumps(devices)


async def test_message_idempotency_history_tasks_events_and_cancel_are_scoped(api):
    a, b = await create_session(api, 'A'), await create_session(api, 'B')
    foreign = await create_session(api, 'Friend', api.other_headers)
    job = await submit(api, a, 'A private request', 'same-key')
    assert job['request_id'] == 'same-key'
    duplicate = await submit(api, a, 'A private request', 'same-key')
    assert duplicate['id'] == job['id']
    assert duplicate['request_id'] == 'same-key'
    mismatch = await api.client.post(f"/v1/sessions/{b['id']}/messages",
        json={'text': 'A private request', 'request_id': 'same-key'}, headers=api.headers)
    assert mismatch.status == 400
    friend_job = await submit(api, foreign, 'Friend secret', 'friend-key', api.other_headers)
    b_messages = await (await api.client.get(f"/v1/sessions/{b['id']}/messages", headers=api.headers)).json()
    assert b_messages['items'] == []
    a_messages = await (await api.client.get(f"/v1/sessions/{a['id']}/messages", headers=api.headers)).json()
    assert a_messages['items'][0]['request_id'] == 'same-key'
    assert (await api.client.get(f"/v1/sessions/{a['id']}/messages", headers=api.other_headers)).status == 403
    tasks = await (await api.client.get('/v1/tasks', headers=api.headers)).json()
    assert {j['id'] for j in tasks['items']} == {job['id']}
    events = await (await api.client.get('/v1/events?after=0', headers=api.headers)).json()
    assert events['items'] and all(e['account_id'] == api.owner['id'] for e in events['items'])
    assert friend_job['id'] not in json.dumps(events)
    wrong = await api.client.post(f"/v1/sessions/{b['id']}/tasks/{job['id']}/cancel", headers=api.headers)
    assert wrong.status == 403 and api.store.get(job['id'])['status'] == 'queued'
    cancelled = await api.client.post(f"/v1/sessions/{a['id']}/tasks/{job['id']}/cancel", headers=api.headers)
    assert cancelled.status == 200 and (await cancelled.json())['status'] == 'cancelled'
    assert api.store.get(friend_job['id'])['status'] == 'queued'


async def test_run_history_is_owned_by_original_session(api):
    a,b=await create_session(api,'A'),await create_session(api,'B')
    job=await submit(api,a)
    run=api.store.start_run(job['id'])
    api.store.update(job['id'],status='waiting_approval',outcome='needs_input')
    api.store.finish_run(run['id'])
    path=f"/v1/sessions/{a['id']}/tasks/{job['id']}/runs"
    response=await api.client.get(path,headers=api.headers)
    assert response.status==200
    rows=(await response.json())['items']
    assert len(rows)==1 and rows[0]['id']==run['id'] and rows[0]['status']=='waiting_approval'
    assert (await api.client.get(path,headers=api.other_headers)).status==403
    assert (await api.client.get(f"/v1/sessions/{b['id']}/tasks/{job['id']}/runs",headers=api.headers)).status==403


async def test_skill_revisions_snapshot_and_cross_session_independence(api):
    a, b = await create_session(api, 'A'), await create_session(api, 'B')
    changed = await api.client.put(f"/v1/sessions/{a['id']}/skills",
        json={'revision': a['revision'], 'skill_names': ['research']}, headers=api.headers)
    assert changed.status == 200
    configured = await changed.json()
    stale = await api.client.patch(f"/v1/sessions/{a['id']}",
        json={'revision': a['revision'], 'name': 'Stale'}, headers=api.headers)
    assert stale.status == 409 and (await stale.json())['error']['code'] == 'revision_conflict'
    old = await submit(api, a, request_id='first')
    (api.skills / 'SKILL.md').write_text('Research\nVERSION TWO')
    new = await submit(api, a, request_id='second')
    assert 'VERSION ONE' in SessionSkills(api.store, old['id']).read('research')['text']
    assert 'VERSION TWO' in SessionSkills(api.store, new['id']).read('research')['text']
    other = await (await api.client.get(f"/v1/sessions/{b['id']}", headers=api.headers)).json()
    assert other['skill_names'] == [] and other['revision'] == b['revision']
    forbidden = await api.client.patch(f"/v1/sessions/{a['id']}",
        json={'revision': configured['revision'], 'management': True}, headers=api.other_headers)
    assert forbidden.status == 403


async def test_files_and_immutable_attachments_are_scoped_and_names_validated(api):
    a, b = await create_session(api, 'A'), await create_session(api, 'B')
    original = await upload(api, a, 'note.txt', b'A PRIVATE')
    await upload(api, b, 'note.txt', b'B PRIVATE')
    await upload(api, a, 'note.txt', b'A REPLACED')
    file_url = f"/v1/sessions/{a['id']}/files/note.txt"
    downloaded = await api.client.get(file_url, headers=api.headers)
    assert downloaded.status == 200 and await downloaded.read() == b'A REPLACED'
    assert downloaded.headers['Content-Disposition'] == 'attachment'
    assert (await api.client.get(file_url, headers=api.other_headers)).status == 403
    b_download = await api.client.get(f"/v1/sessions/{b['id']}/files/note.txt", headers=api.headers)
    assert await b_download.read() == b'B PRIVATE'
    ja, jb = await submit(api, a, request_id='a'), await submit(api, b, request_id='b')
    aa = Attachments(api.store, api.store.get(ja['id']), api.store.path.parent)
    assert (await aa.read(original['attachment_id']))['text'] == 'A PRIVATE'
    ab = Attachments(api.store, api.store.get(jb['id']), api.store.path.parent)
    with pytest.raises(PermissionError):
        await ab.read(original['attachment_id'])
    form = aiohttp.FormData()
    form.add_field('file', b'attack', filename='../escape.txt')
    bad = await api.client.post(f"/v1/sessions/{a['id']}/files", data=form, headers=api.headers)
    assert bad.status == 400
    assert not (api.store.path.parent / 'escape.txt').exists()


async def test_voice_message_queues_during_live_call_and_retries_by_content(api):
    a,b=await create_session(api,'A'),await create_session(api,'B')
    api.service.engine.voice_reserved=True
    url=f"/v1/sessions/{a['id']}/voice-messages"
    headers={**api.headers,'Content-Type':'audio/mp4','Idempotency-Key':'memo-one'}
    # Container prefix is checked at ingress; real decoding and duration checks
    # run in the bounded speech worker, which this HTTP test never starts.
    audio=b'\x00\x00\x00\x18ftypM4A '+b'\x00'*64
    response=await api.client.post(url,headers=headers,data=audio)
    assert response.status==201,await response.text()
    job=await response.json()
    original=api.store.get(job['id'])
    from pathlib import Path
    path=Path(original['audio'])
    assert path.read_bytes()==audio and original['session_id']==a['id']
    retry=await api.client.post(url,headers=headers,data=audio)
    assert retry.status==201 and (await retry.json())['id']==job['id']
    assert list(path.parent.iterdir())==[path]
    collision=await api.client.post(url,headers=headers,data=audio+b'changed')
    assert collision.status==400 and list(path.parent.iterdir())==[path]
    cross=await api.client.post(f"/v1/sessions/{b['id']}/voice-messages",headers=headers,data=audio)
    assert cross.status==400 and list(path.parent.iterdir())==[path]
    forbidden=await api.client.post(url,headers={**headers,**api.other_headers},data=audio)
    assert forbidden.status==403
    # A retry after execution/cancellation still returns the original job and
    # must not leave the newly uploaded temporary recording behind.
    await api.service.cancel_job(job['id'])
    assert not path.exists()
    late=await api.client.post(url,headers=headers,data=audio)
    assert late.status==201 and (await late.json())['status']=='cancelled'
    assert not list(path.parent.iterdir())


async def test_voice_message_rejects_bad_input_and_cleans_temporary_audio(api,monkeypatch):
    session=await create_session(api,'Memo')
    url=f"/v1/sessions/{session['id']}/voice-messages"
    headers={**api.headers,'Content-Type':'audio/mp4','Idempotency-Key':'memo'}
    assert (await api.client.post(url,headers=headers,data=b'not audio')).status==400
    assert not list((api.store.path.parent/'incoming').iterdir())
    assert (await api.client.post(url,headers=api.headers,data=b'not audio')).status==400
    monkeypatch.setattr('local_ai.mobile_api.MAX_FILE_BYTES',16)
    assert (await api.client.post(url,headers=headers,data=b'\x00\x00\x00\x18ftypM4A '+b'x'*32)).status==400
    assert not list((api.store.path.parent/'incoming').iterdir())
    assert api.store.db.execute('SELECT count(*) FROM jobs').fetchone()[0]==0


async def test_fork_imports_selected_content_without_jobs_or_management(api):
    source = await create_session(api, 'Source')
    configured = await api.client.patch(f"/v1/sessions/{source['id']}",
        json={'revision': source['revision'], 'management': True, 'skill_names': ['research']}, headers=api.headers)
    assert configured.status == 200
    job = await submit(api, source, 'Selected request')
    api.store.update(job['id'], status='succeeded', result='Selected response')
    await upload(api, source, 'selected.txt', b'Selected file')
    await upload(api, source, 'unselected.txt', b'Private')
    response = await api.client.post(f"/v1/sessions/{source['id']}/fork",
        json={'name': 'Fork', 'message_ids': [job['id']], 'file_names': ['selected.txt']}, headers=api.headers)
    assert response.status == 201, await response.text()
    target = await response.json()
    assert not target['management'] and target['skill_names'] == ['research']
    history = await (await api.client.get(f"/v1/sessions/{target['id']}/messages", headers=api.headers)).json()
    assert len(history['items']) == 1 and history['items'][0]['kind'] == 'imported'
    assert history['items'][0]['result'] == 'Selected response'
    tasks = await (await api.client.get(f"/v1/sessions/{target['id']}/tasks", headers=api.headers)).json()
    assert tasks['items'] == []
    files = await (await api.client.get(f"/v1/sessions/{target['id']}/files", headers=api.headers)).json()
    assert [f['name'] for f in files['items']] == ['selected.txt']
    copied = await submit(api, target, request_id='fork-request')
    attachments = Attachments(api.store, api.store.get(copied['id']), api.store.path.parent).list()
    assert len(attachments) == 1 and attachments[0]['name'] == 'selected.txt'


async def test_approval_pending_decision_exactly_once_and_cancelled_stale_rejection(api):
    a, b = await create_session(api, 'Managed'), await create_session(api, 'Other')
    await api.client.patch(f"/v1/sessions/{a['id']}",
        json={'revision': a['revision'], 'management': True}, headers=api.headers)
    job = await submit(api, a, 'Write approved file', 'approval')
    durable = api.store.get(job['id'])
    with pytest.raises(ApprovalPending):
        await api.service.approvals.request(durable, 'Write test file')
    api.store.update(job['id'], status='waiting_approval')
    pending = await (await api.client.get(f"/v1/sessions/{a['id']}/approvals", headers=api.headers)).json()
    token = pending['items'][0]['token']
    wrong = await api.client.post(f"/v1/sessions/{b['id']}/approvals/{token}", json={'decision': 'approved'}, headers=api.headers)
    assert wrong.status == 403
    path = f"/v1/sessions/{a['id']}/approvals/{token}"
    results = await asyncio.gather(*(api.client.post(path, json={'decision': 'approved'}, headers=api.headers) for _ in range(2)))
    assert sorted(r.status for r in results) == [200, 400]
    assert api.store.get(job['id'])['status'] == 'queued'
    assert await api.service.approvals.request(durable, 'Write test file') is True
    assert await api.service.approvals.request(durable, 'Write test file') is False
    second = await submit(api, a, 'Another write', 'cancel-approval')
    with pytest.raises(ApprovalPending):
        await api.service.approvals.request(api.store.get(second['id']), 'Second write')
    api.store.update(second['id'], status='waiting_approval')
    pending = await (await api.client.get(f"/v1/sessions/{a['id']}/approvals", headers=api.headers)).json()
    stale = pending['items'][0]['token']
    assert (await api.client.post(f"/v1/sessions/{a['id']}/tasks/{second['id']}/cancel", headers=api.headers)).status == 200
    declined = await api.client.post(f"/v1/sessions/{a['id']}/approvals/{stale}", json={'decision': 'approved'}, headers=api.headers)
    assert declined.status == 400 and api.store.get(second['id'])['status'] == 'cancelled'


async def test_device_rotation_revocation_push_and_account_disable(api):
    device, headers = await api.pair(api.owner, 'Second device')
    push = await api.client.post(f"/v1/devices/{device['device_id']}/push",
        json={'token': 'ab' * 32, 'environment': 'sandbox'}, headers=headers)
    assert push.status == 200
    foreign = await api.client.delete(f"/v1/devices/{device['device_id']}", headers=api.other_headers)
    assert foreign.status in (200, 403, 404)
    assert (await api.client.get('/v1/sessions', headers=headers)).status == 200
    rotated = await api.client.post('/v1/auth/renew', headers=headers)
    assert rotated.status == 200
    renewed = {'Authorization': 'Bearer ' + (await rotated.json())['token']}
    assert (await api.client.get('/v1/sessions', headers=headers)).status == 401
    assert (await api.client.get('/v1/sessions', headers=renewed)).status == 200
    revoked = await api.client.delete(f"/v1/devices/{device['device_id']}", headers=api.headers)
    assert revoked.status == 200
    assert (await api.client.get('/v1/sessions', headers=renewed)).status == 401
    api.store.set_access(api.owner['user_id'], False)
    assert (await api.client.get('/v1/sessions', headers=api.headers)).status == 401
    assert (await api.client.get('/v1/sessions', headers=api.other_headers)).status == 200


async def test_google_oauth_callback_is_state_bound_and_bindings_are_scoped(api):
    calls = []
    async def transport(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url == 'https://oauth2.googleapis.com/token':
            return {'access_token': 'private-access-token', 'refresh_token': 'private-refresh-token',
                    'scope': GOOGLE_SCOPE, 'expires_in': 3600}
        if url == 'https://www.googleapis.com/drive/v3/about':
            return {'user': {'emailAddress': 'owner@example.test'}}
        raise AssertionError('Unexpected external request')
    api.store.connectors = ConnectorManager(api.store, MemorySecretStore(),
        {'GOOGLE_CLIENT_ID': 'client', 'GOOGLE_CLIENT_SECRET': 'client-secret',
         'GOOGLE_REDIRECT_URI': 'https://example.test/v1/oauth/google/callback'}, transport)
    a, b = await create_session(api, 'A'), await create_session(api, 'B')
    started = await api.client.post('/v1/connectors/google/authorize', headers=api.headers)
    assert started.status == 200
    auth = await started.json()
    assert auth['authorization_url'].startswith('https://accounts.google.com/')
    bad = await api.client.get('/v1/oauth/google/callback?state=wrong&code=code')
    assert bad.status == 400 and calls == []
    callback = await api.client.get('/v1/oauth/google/callback', params={'state': auth['state'], 'code': 'code'})
    assert callback.status == 200 and 'Location' not in callback.headers
    assert 'private-access-token' not in await callback.text()
    replay = await api.client.get('/v1/oauth/google/callback', params={'state': auth['state'], 'code': 'code'})
    assert replay.status == 403 and len(calls) == 2
    connections = await (await api.client.get('/v1/connectors', headers=api.headers)).json()
    cid = connections['items'][0]['id']
    assert 'private-' not in json.dumps(connections)
    friend = await (await api.client.get('/v1/connectors', headers=api.other_headers)).json()
    assert friend['items'] == []
    binding = {'revision': a['revision'], 'bindings': [{'connection_id': cid, 'resource_ids': ['document-a'], 'can_create': False}]}
    saved = await api.client.put(f"/v1/sessions/{a['id']}/connectors", json=binding, headers=api.headers)
    assert saved.status == 200
    stale = await api.client.put(f"/v1/sessions/{a['id']}/connectors", json=binding, headers=api.headers)
    assert stale.status == 409
    other = await (await api.client.get(f"/v1/sessions/{b['id']}/connectors", headers=api.headers)).json()
    assert other['items'] == []
    assert (await api.client.delete(f'/v1/connectors/{cid}', headers=api.other_headers)).status == 403
    assert (await api.client.delete(f'/v1/connectors/{cid}', headers=api.headers)).status == 200
    cleared = await (await api.client.get(f"/v1/sessions/{a['id']}/connectors", headers=api.headers)).json()
    assert cleared['items'] == []


async def test_json_size_bound_applies_to_content_length_and_chunked_uploads(api):
    headers = {**api.headers, 'Content-Type': 'application/json'}
    assert (await api.client.post('/v1/sessions', data='[]', headers=headers)).status == 400
    huge = json.dumps({'name': 'x' * 70000}).encode()
    assert (await api.client.post('/v1/sessions', data=huge, headers=headers)).status == 413
    async def chunks():
        for offset in range(0, len(huge), 1024):
            yield huge[offset:offset + 1024]
    response = await api.client.post('/v1/sessions', data=chunks(), headers=headers)
    assert response.status == 413


async def test_archive_reset_and_cache_headers(api):
    a, b = await create_session(api, 'A'), await create_session(api, 'B')
    ja, jb = await submit(api, a, request_id='a'), await submit(api, b, request_id='b')
    archived = await api.client.patch(f"/v1/sessions/{a['id']}",
        json={'revision': a['revision'], 'archived': True}, headers=api.headers)
    assert archived.status == 200 and api.store.get(ja['id'])['status'] == 'queued'
    assert (await api.client.post(f"/v1/sessions/{a['id']}/reset", headers=api.headers)).status == 409
    api.store.update(ja['id'], status='succeeded', result='A complete')
    api.store.update(jb['id'], status='succeeded', result='B complete')
    assert (await api.client.post(f"/v1/sessions/{a['id']}/reset", headers=api.headers)).status == 200
    assert (await (await api.client.get(f"/v1/sessions/{a['id']}/messages", headers=api.headers)).json())['items'] == []
    preserved = await (await api.client.get(f"/v1/sessions/{b['id']}/messages", headers=api.headers)).json()
    assert preserved['items'][0]['result'] == 'B complete'
    unauthorized = await api.client.get('/v1/sessions')
    assert unauthorized.status == 401
    assert unauthorized.headers.get('Cache-Control') == 'no-store'
    assert unauthorized.headers.get('X-Content-Type-Options') == 'nosniff'

async def test_push_registration_does_not_deliver_historical_events(api):
    from local_ai.notifications import APNsNotifier
    workspace=api.store.create_session(api.owner['id'],'Push scope')
    def emit(job):
        api.store.db.execute('INSERT INTO session_events(account_id,session_id,kind,payload,created) VALUES (?,?,?,?,0)',
            (api.owner['id'],workspace['id'],'task_updated',json.dumps({'job_id':job,'status':'succeeded'})))
        api.store.db.commit()
    emit('old-result')
    did=api.device['device_id']
    route=f'/v1/devices/{did}/push'
    payload={'token':'cc'*32,'environment':'sandbox'}
    assert (await api.client.post(route,json=payload,headers=api.headers)).status==200
    cutoff=api.store.db.execute('SELECT since_sequence FROM mobile_push_subscriptions WHERE device_id=?',(did,)).fetchone()[0]
    notifier=APNsNotifier(api.store,config={})
    notifier.collect()
    assert api.store.db.execute('SELECT COUNT(*) FROM apns_outbox').fetchone()[0]==0
    emit('new-result')
    assert (await api.client.post(route,json=payload,headers=api.headers)).status==200
    assert api.store.db.execute('SELECT since_sequence FROM mobile_push_subscriptions WHERE device_id=?',(did,)).fetchone()[0]==cutoff
    notifier.collect()
    assert api.store.db.execute('SELECT COUNT(*) FROM apns_outbox').fetchone()[0]==1
    foreign=f"/v1/devices/{api.other_device['device_id']}/push"
    assert (await api.client.post(foreign,json=payload,headers=api.headers)).status==403


@pytest.mark.asyncio
async def test_evolution_owner_scope_exact_revision_and_skill_publication(api):
    a=await create_session(api,'管理');b=await create_session(api,'其他')
    path=f"/v1/sessions/{a['id']}/evolution"
    assert (await api.client.get(path,headers=api.headers)).status==403
    api.store.update_session(api.owner['id'],a['id'],a['revision'],management=True)
    assert (await api.client.get(path,headers=api.other_headers)).status==403
    response=await api.client.post(path,headers=api.headers,json={'kind':'skill','name':'writer','files':{'SKILL.md':'Writer\nTest method'},'summary':'创建写作方法','base':''})
    assert response.status==201,await response.text()
    item=await response.json();rid=item['id']
    assert (await api.client.get(f"/v1/sessions/{b['id']}/evolution/{rid}",headers=api.headers)).status==403
    assert (await api.client.post(path+'/'+rid,headers=api.headers,json={'action':'publish','revision':'wrong'})).status==400
    response=await api.client.post(path+'/'+rid,headers=api.headers,json={'action':'publish','revision':rid})
    assert response.status==200,await response.text()
    assert (await response.json())['state']=='published'
    assert api.store.require_session(api.owner['id'],a['id'])['skill_names']==[]
    assert api.store.require_session(api.owner['id'],b['id'])['skill_names']==[]


@pytest.mark.asyncio
async def test_mcp_publication_rechecks_device_after_awaiting_probe(api,monkeypatch):
    from local_ai.mcp_runtime import MCPRuntime
    a=await create_session(api,'管理');api.store.update_session(api.owner['id'],a['id'],a['revision'],management=True)
    item=api.store.evolution.stage(api.owner['id'],a['id'],'mcp','demo',{'server.py':'pass'},'revocation test','')
    async def probe(*args):
        api.auth.revoke(api.owner['id'],api.device['device_id'])
        return [{'name':'test'}]
    monkeypatch.setattr(MCPRuntime,'probe',probe)
    response=await api.client.post(f"/v1/sessions/{a['id']}/evolution/{item['id']}",headers=api.headers,json={'action':'publish','revision':item['id']})
    assert response.status in (401,403)
    assert api.store.evolution.current('mcp','demo')==''


async def test_message_revision_respects_session_and_reset(api):
    a = await create_session(api, 'A')
    b = await create_session(api, 'B')
    path = f"/v1/sessions/{a['id']}/messages"
    initial = await (await api.client.get(path, headers=api.headers)).json()
    unchanged = await (await api.client.get(path, params={'revision': initial['revision']}, headers=api.headers)).json()
    assert unchanged == {'unchanged': True, 'revision': initial['revision']}
    assert (await api.client.get(path, params={'revision': initial['revision']}, headers=api.other_headers)).status == 403
    await submit(api, b, 'only B', 'b1')
    still = await (await api.client.get(path, params={'revision': initial['revision']}, headers=api.headers)).json()
    assert still['unchanged']
    job = await submit(api, a, 'only A', 'a1')
    updated = await (await api.client.get(path, params={'revision': initial['revision']}, headers=api.headers)).json()
    assert updated['revision'] != initial['revision']
    assert [item['id'] for item in updated['items']] == [job['id']]
    api.store.update(job['id'], status='succeeded', result='done')
    response = await api.client.post(f"/v1/sessions/{a['id']}/reset", json={}, headers=api.headers)
    assert response.status == 200
    reset = await (await api.client.get(path, params={'revision': updated['revision']}, headers=api.headers)).json()
    assert reset['items'] == []


def test_artifact_version_changes_when_content_changes(tmp_path):
    from local_ai.mobile_api import public_job
    path = tmp_path / 'picture.png'
    path.write_bytes(b'first')
    job = {'id': 'image', 'session_id': 'A', 'kind': 'image', 'result': str(path)}
    first = public_job(job)['artifact_version']
    path.write_bytes(b'another version')
    assert public_job(job)['artifact_version'] != first
    path.unlink()
    assert public_job(job)['artifact_version'] == 'missing'


@pytest.mark.parametrize('mode', ['RGB', 'RGBA'])
async def test_thumbnail_private_small_versioned_and_original_unchanged(api, tmp_path, mode):
    import io
    from PIL import Image
    session = await create_session(api, 'Images')
    jid = api.store.enqueue(api.owner['user_id'], None, 'image', 'sample', session_id=session['id'], channel='app')
    path = tmp_path / 'original.png'
    Image.effect_noise((1536, 1024), 80).convert(mode).save(path)
    original = path.read_bytes()
    api.store.update(jid, status='succeeded', result=str(path))
    url = f"/v1/sessions/{session['id']}/tasks/{jid}/artifact"
    response = await api.client.get(url+'?thumbnail=1', headers=api.headers)
    assert response.status == 200
    data = await response.read()
    assert response.content_type == 'image/jpeg'
    assert response.headers['Cache-Control'] == 'no-store'
    with Image.open(io.BytesIO(data)) as preview:
        assert preview.size == (768, 512)
    assert len(data) < len(original) / 4
    assert path.read_bytes() == original
    assert (await api.client.get(url+'?thumbnail=1', headers=api.other_headers)).status == 403
    assert (await api.client.get(url+'?thumbnail=1')).status == 401
    other = await create_session(api, 'Other')
    wrong = url.replace(session['id'], other['id'])
    assert (await api.client.get(wrong+'?thumbnail=1', headers=api.headers)).status in (403,404)
    assert await (await api.client.get(url, headers=api.headers)).read() == original

async def test_live_draft_is_session_bound_ephemeral_and_changes_revision(api):
    first=await create_session(api,'Draft A')
    second=await create_session(api,'Draft B')
    job=await submit(api,first,'hello','stream-test')
    api.store.update(job['id'],status='running')
    path=f"/v1/sessions/{first['id']}/messages"
    before=await (await api.client.get(path,headers=api.headers)).json()
    api.service.pi.progress[job['id']]={'text':'unfinished candidate','phase':'generating'}
    live=await (await api.client.get(path+'?revision='+before['revision'],headers=api.headers)).json()
    assert live['items'][0]['draft']['text']=='unfinished candidate'
    assert not live['items'][0]['result']
    assert (await api.client.get(path,headers=api.other_headers)).status==403
    other=await (await api.client.get(f"/v1/sessions/{second['id']}/messages",headers=api.headers)).json()
    assert other['items']==[]
    api.store.update(job['id'],status='cancelled')
    done=await (await api.client.get(path,headers=api.headers)).json()
    assert 'draft' not in done['items'][0]
    assert api.store.get(job['id'])['result'] is None

async def test_video_project_and_clip_progress_keep_session_ownership(api):
    session=await create_session(api,'Film')
    other=await create_session(api,'Other')
    parent=await submit(api,session,'Make a film','project-request')
    result=api.store.video_projects.create(api.store.get(parent['id']),'Short story','A blue circle moves across a white room.',1,reference_choices={'character_photos':'用户选择不用','environment_photos':'用户选择不用'})
    jid=result['job_id']
    with api.store.db:
        for i in range(4):api.store.db.execute('insert into video_shots(project_id,ordinal,duration,plan) values (?,?,?,?)',(jid,i,15,json.dumps({'ordinal':i,'summary':f'Shot {i+1}','prompt':f'A blue circle moves to coordinate {i*10}.'})))
    from local_ai.video_review import require_review
    from unittest.mock import AsyncMock
    with api.store.db:api.store.db.execute('update video_projects set bible=? where job_id=?',(json.dumps({'characters':'circle'}),jid))
    await require_review(api.store,api.store.get(jid),AsyncMock(return_value=True),api.store.video_projects.review_document(api.store.get(jid)))
    api.store.video_projects.fill_window(api.store.get(jid))
    response=await api.client.get(f"/v1/sessions/{session['id']}/messages",headers=api.headers)
    items=(await response.json())['items']
    header=next(item for item in items if item['id']==jid)
    assert header['project']['total']==4 and header['project']['prepared_window']==2
    assert sum('shot' in item for item in items)==2
    assert 'script' not in header['project'] and 'bible' not in header['project']
    assert (await api.client.get(f"/v1/sessions/{session['id']}/messages",headers=api.other_headers)).status==403
    assert (await (await api.client.get(f"/v1/sessions/{other['id']}/messages",headers=api.headers)).json())['items']==[]
    await api.service.cancel_job(parent['id'])
    assert not api.store.db.execute("select id from jobs where session_id=? and status in ('queued','running')",(session['id'],)).fetchall()


async def test_mac_permission_gap_and_stable_elapsed_time(api):
    from local_ai.capabilities import BUILTINS
    session = await create_session(api, 'Mac')
    created = await submit(api, session, 'Open Calculator and screenshot')
    jid = created['id']
    run = api.store.start_run(jid)
    spec = next(s for s in BUILTINS if s.name == 'capabilities')
    api.store.begin_operation(jid, 'discovery', spec, {'group': 'computer'})
    api.store.finish_operation(jid, 'discovery', result={'capabilities': []})
    api.store.update(jid, status='succeeded', outcome='blocked', result='Generic failure')
    api.store.finish_run(run['id'])
    path = f"/v1/sessions/{session['id']}/messages"
    response = await (await api.client.get(path, headers=api.headers)).json()
    item = response['items'][0]
    assert item['required_action'] == 'enable_mac_management'
    assert 'Mac 管理' in item['result']
    assert item['timing']['finished_at'] >= item['timing']['started_at'] >= item['created']
    assert item['timing']['attempts'] == 1
    unchanged = await (await api.client.get(path+'?revision='+response['revision'], headers=api.headers)).json()
    assert unchanged['unchanged']
    tasks = await (await api.client.get('/v1/tasks', headers=api.headers)).json()
    assert tasks['items'][0]['timing'] == item['timing']
    api.store.update_session(api.owner['id'], session['id'], session['revision'], management=True)
    updated = await (await api.client.get(path, headers=api.headers)).json()
    assert 'required_action' not in updated['items'][0]


async def test_screenshot_is_previewable_owned_delivery_evidence(api, tmp_path):
    from PIL import Image
    from local_ai.capabilities import BUILTINS
    from local_ai.outcome_evidence import evidence_catalog
    session = await create_session(api, 'Screen')
    created = await submit(api, session)
    job = api.store.get(created['id'])
    png = tmp_path/'screen.png'
    Image.new('RGB', (30, 20), 'white').save(png)
    attachment = Attachments(api.store, job, tmp_path).add(png, 'screen.png')
    spec = next(s for s in BUILTINS if s.name == 'computer_screen')
    api.store.begin_operation(job['id'], 'screen', spec, {})
    api.store.finish_operation(job['id'], 'screen', result={'captured': True, 'attachment_id': attachment})
    api.store.update(job['id'], status='succeeded', outcome='completed')
    path = f"/v1/sessions/{session['id']}/messages"
    response = await (await api.client.get(path, headers=api.headers)).json()
    item = response['items'][0]
    assert item['artifact_media_type'] == 'image/png'
    artifact = await api.client.get(item['artifact_url'], headers=api.headers)
    assert artifact.status == 200 and await artifact.read() == png.read_bytes()
    assert (await api.client.get(item['artifact_thumbnail_url'], headers=api.headers)).status == 200
    assert (await api.client.get(item['artifact_url'], headers=api.other_headers)).status == 403
    catalog = evidence_catalog(api.store, job, api.store.task_facts(job))
    assert {'artifact', 'delivery'} <= {entry['kind'] for entry in catalog if entry['status']=='verified'}
    assert any('observation' in entry['proof_kinds'] and entry['kind']=='operation' for entry in catalog)
    # A receipt naming another job's image cannot deliver it or prove completion.
    other = await submit(api, session, request_id='second-screen')
    api.store.db.execute('UPDATE attachments SET job_id=? WHERE id=?', (other['id'], attachment))
    api.store.db.commit()
    response = await (await api.client.get(path, headers=api.headers)).json()
    assert 'artifact_url' not in next(v for v in response['items'] if v['id']==job['id'])
    catalog = evidence_catalog(api.store, job, api.store.task_facts(job))
    assert not any(entry['kind'] in ('artifact','delivery') and entry['status']=='verified' for entry in catalog)


async def test_task_timing_retries_and_queued_cancellation(api):
    from local_ai.job_presentation import add_job_timing
    session = await create_session(api, 'Timing')
    created = await submit(api, session)
    jid = created['id']
    first = api.store.start_run(jid)
    api.store.update(jid, status='queued')
    api.store.finish_run(first['id'])
    second = api.store.start_run(jid)
    active = add_job_timing(api.store, [api.store.get(jid)])[0]['timing']
    assert active['finished_at'] is None and active['running_since']==second['started_at'] and active['attempts']==2
    api.store.update(jid, status='failed')
    api.store.finish_run(second['id'])
    finished = add_job_timing(api.store, [api.store.get(jid)])[0]['timing']
    assert finished['finished_at'] >= second['started_at'] and finished['run_seconds'] >= 0
    queued = await submit(api, session, request_id='cancel-before-run')
    api.store.update(queued['id'], status='cancelled')
    cancelled = add_job_timing(api.store, [api.store.get(queued['id'])])[0]['timing']
    assert cancelled['finished_at'] >= queued['created'] and cancelled['attempts']==0


async def test_untitled_session_options_suggestion_and_live_stream(api):
    created = await api.client.post('/v1/sessions', json={}, headers=api.headers)
    assert created.status == 201
    session = await created.json()
    assert session['name'] == '新对话' and session['auto_title'] == 1
    response = await api.client.post(f"/v1/sessions/{session['id']}/messages",
        json={'text': '帮我把这个剧本做成短剧\n第二行', 'request_id': 'opt-1', 'thinking': False, 'agents': ['drama']}, headers=api.headers)
    assert response.status == 201, await response.text()
    job = await response.json()
    renamed = await (await api.client.get(f"/v1/sessions/{session['id']}", headers=api.headers)).json()
    assert renamed['name'] == '帮我把这个剧本做成短剧' and renamed['auto_title'] == 1
    bad = await api.client.post(f"/v1/sessions/{session['id']}/messages",
        json={'text': 'x', 'request_id': 'opt-2', 'agents': ['nobody']}, headers=api.headers)
    assert bad.status == 400
    # The user's own name is never replaced by the automatic one.
    await api.client.patch(f"/v1/sessions/{session['id']}", json={'name': '我的短剧', 'revision': renamed['revision']}, headers=api.headers)
    from local_ai.agent import roster
    assert not roster.set_title(api.store, api.owner['id'], session['id'], '自动', final=True)

    agents = await (await api.client.get('/v1/agents', headers=api.headers)).json()
    assert [a['id'] for a in agents['items']] == ['drama']
    hint = await (await api.client.post('/v1/agents/suggest', json={'text': '第1场 内景·咖啡馆·夜'}, headers=api.headers)).json()
    assert hint['suggestion']['id'] == 'drama'
    none = await (await api.client.post('/v1/agents/suggest', json={'text': '剧本', 'agents': ['drama']}, headers=api.headers)).json()
    assert none['suggestion'] is None
    plain = await (await api.client.post('/v1/agents/suggest', json={'text': '今天天气怎么样'}, headers=api.headers)).json()
    assert plain['suggestion'] is None

    live = api.service.live
    api.store.update(job['id'], status='running')
    state = live.start(api.store.get(job['id']))
    live.thinking(job['id'], '想')
    messages = await (await api.client.get(f"/v1/sessions/{session['id']}/messages", headers=api.headers)).json()
    item = messages['items'][0]
    assert item['live']['thinking'] == '想' and item['options'] == {'thinking': False, 'agents': ['drama'], 'suggested': None}
    assert (await api.client.get(f"/v1/sessions/{session['id']}/stream", headers=api.other_headers)).status == 403
    stream = await api.client.get(f"/v1/sessions/{session['id']}/stream", headers=api.headers)
    assert stream.status == 200 and stream.headers['Content-Type'].startswith('text/event-stream')

    async def next_event():
        while True:
            line = (await asyncio.wait_for(stream.content.readline(), 3)).decode().strip()
            if line.startswith('data: '):
                return json.loads(line[6:])

    snapshot = await next_event()
    assert snapshot['type'] == 'snapshot' and snapshot['state']['thinking'] == '想'
    assert (await next_event())['type'] == 'ready'
    live.text(job['id'], '你好')
    live.tool(job['id'], 'c1', 'web_search', 'running', '天气')
    live.finish(job['id'])
    events = [await next_event() for _ in range(3)]
    assert [e['type'] for e in events] == ['text', 'tool', 'done']
    assert events[0]['delta'] == '你好' and events[1]['tool']['label'] == '搜索网页'
    assert [e['seq'] for e in events] == sorted(e['seq'] for e in events) and state['seq'] == events[-1]['seq']
    stream.close()


async def test_sessions_list_puts_the_most_recently_used_first(api):
    older = await create_session(api, '早先的对话')
    newer = await create_session(api, '新建但没用')
    api.store.db.execute('UPDATE sessions SET created=created-100 WHERE id=?', (older['id'],))
    api.store.db.commit()
    await submit(api, older, '继续聊', 'recent-1')
    items = (await (await api.client.get('/v1/sessions', headers=api.headers)).json())['items']
    order = [i['id'] for i in items if i['id'] in (older['id'], newer['id'])]
    assert order == [older['id'], newer['id']]


async def test_deleted_session_waits_in_trash_then_restores_or_purges(api):
    kept = await create_session(api, 'Kept')
    doomed = await create_session(api, 'Doomed')
    await submit(api, doomed, 'hello', 'r1')
    await upload(api, doomed, 'notes.txt', b'secret')
    workspace = api.store.workspace_directory(api.owner['id'], doomed['id'])
    assert (await api.client.delete(f"/v1/sessions/{doomed['id']}", headers=api.other_headers)).status == 403
    response = await api.client.delete(f"/v1/sessions/{doomed['id']}", headers=api.headers)
    assert response.status == 200, await response.text()
    listed = [s['id'] for s in (await (await api.client.get('/v1/sessions', headers=api.headers)).json())['items']]
    assert listed == [kept['id']]
    assert (await api.client.get(f"/v1/sessions/{doomed['id']}/messages", headers=api.headers)).status == 403
    tasks = (await (await api.client.get('/v1/tasks', headers=api.headers)).json())['items']
    assert all(t['session_id'] != doomed['id'] for t in tasks)
    trash = await (await api.client.get('/v1/trash', headers=api.headers)).json()
    assert [i['id'] for i in trash['items']] == [doomed['id']] and trash['days'] == 30
    assert (await (await api.client.get('/v1/trash', headers=api.other_headers)).json())['items'] == []
    restored = await api.client.post(f"/v1/trash/{doomed['id']}/restore", headers=api.headers)
    assert restored.status == 200 and (await restored.json())['name'] == 'Doomed'
    assert (await api.client.get(f"/v1/sessions/{doomed['id']}/messages", headers=api.headers)).status == 200
    await api.client.delete(f"/v1/sessions/{doomed['id']}", headers=api.headers)
    assert (await api.client.delete(f"/v1/trash/{doomed['id']}", headers=api.other_headers)).status == 403
    assert (await api.client.delete(f"/v1/trash/{doomed['id']}", headers=api.headers)).status == 200
    assert not workspace.exists()
    assert not api.store.db.execute('SELECT 1 FROM jobs WHERE session_id=?', (doomed['id'],)).fetchone()
    assert not api.store.db.execute('SELECT 1 FROM sessions WHERE id=?', (doomed['id'],)).fetchone()
    assert (await (await api.client.get('/v1/trash', headers=api.headers)).json())['items'] == []


async def test_delete_answers_before_running_work_finishes_stopping(api, monkeypatch):
    current = await create_session(api, 'Slow stop')
    turn = await submit(api, current, 'render', 'r1')
    api.store.db.execute("UPDATE jobs SET status='running' WHERE id=?", (turn['id'],))
    api.store.db.commit()
    stopped, release = [], asyncio.Event()
    async def slow_cancel(job_id):
        stopped.append(job_id)
        await release.wait()
    monkeypatch.setattr(api.service, 'cancel_job', slow_cancel)
    response = await asyncio.wait_for(api.client.delete(f"/v1/sessions/{current['id']}/tasks/{turn['id']}", headers=api.headers), 2)
    assert response.status == 200, await response.text()
    assert api.store.get(turn['id'])['status'] == 'cancelled'
    other = await create_session(api, 'Also slow')
    job = await submit(api, other, 'video', 'r2')
    api.store.db.execute("UPDATE jobs SET status='running' WHERE id=?", (job['id'],))
    api.store.db.commit()
    response = await asyncio.wait_for(api.client.delete(f"/v1/sessions/{other['id']}", headers=api.headers), 2)
    assert response.status == 200 and api.store.get(job['id'])['status'] == 'cancelled'
    await asyncio.sleep(0)
    assert stopped == [turn['id'], job['id']]
    release.set()


async def test_trash_expires_after_thirty_days(api):
    old = await create_session(api, 'Old')
    await api.client.delete(f"/v1/sessions/{old['id']}", headers=api.headers)
    api.store.db.execute('UPDATE sessions SET deleted=deleted-31*86400 WHERE id=?', (old['id'],))
    api.store.db.commit()
    assert (await (await api.client.get('/v1/trash', headers=api.headers)).json())['items'] == []
    assert not api.store.db.execute('SELECT 1 FROM sessions WHERE id=?', (old['id'],)).fetchone()


async def test_deleting_a_turn_hides_it_everywhere(api):
    current = await create_session(api, 'Turns')
    first = await submit(api, current, 'keep me', 'r1')
    second = await submit(api, current, 'forget me', 'r2')
    api.store.db.execute("UPDATE jobs SET status='succeeded',result='ok' WHERE id IN (?,?)", (first['id'], second['id']))
    api.store.db.commit()
    assert (await api.client.delete(f"/v1/sessions/{current['id']}/tasks/{second['id']}", headers=api.other_headers)).status == 403
    response = await api.client.delete(f"/v1/sessions/{current['id']}/tasks/{second['id']}", headers=api.headers)
    assert response.status == 200, await response.text()
    items = (await (await api.client.get(f"/v1/sessions/{current['id']}/messages", headers=api.headers)).json())['items']
    assert [i['id'] for i in items if i['kind'] != 'imported'] == [first['id']]
    tasks = (await (await api.client.get('/v1/tasks', headers=api.headers)).json())['items']
    assert second['id'] not in [t['id'] for t in tasks]
    session = next(s for s in (await (await api.client.get('/v1/sessions', headers=api.headers)).json())['items'] if s['id'] == current['id'])
    assert session['last']['title'] != 'forget me'
    history = api.store.messages(api.owner['user_id'], 'next', session_id=current['id'])
    assert all('forget me' not in m['content'] for m in history)


async def test_delete_workspace_file(api):
    current = await create_session(api, 'Files')
    await upload(api, current, 'a.txt', b'one')
    assert (await api.client.delete(f"/v1/sessions/{current['id']}/files/a.txt", headers=api.other_headers)).status == 403
    assert (await api.client.delete(f"/v1/sessions/{current['id']}/files/a.txt", headers=api.headers)).status == 200
    assert (await (await api.client.get(f"/v1/sessions/{current['id']}/files", headers=api.headers)).json())['items'] == []
    assert (await api.client.delete(f"/v1/sessions/{current['id']}/files/a.txt", headers=api.headers)).status == 404


async def test_home_summary_counts_everyone_in_the_gpu_queue_but_describes_only_own_jobs(api):
    mine = await create_session(api, 'Mine')
    theirs = await create_session(api, 'Theirs', api.other_headers)
    first = api.store.enqueue(api.friend['user_id'], None, 'video', 'their render', session_id=theirs['id'], channel='app')
    second = api.store.enqueue(api.owner['user_id'], None, 'image', 'a cat by the window', session_id=mine['id'], channel='app')
    api.store.db.execute('UPDATE jobs SET created=? WHERE id=?', (1, first))
    api.store.db.execute('UPDATE jobs SET created=? WHERE id=?', (2, second))
    api.store.db.commit()
    response = await api.client.get('/v1/home', headers=api.headers)
    assert response.status == 200, await response.text()
    data = await response.json()
    assert data['now'] is None and data['mac']['queued'] == 2
    assert [(item['id'], item['position'], item['session_name']) for item in data['queue']] == [(second, 2, 'Mine')]
    assert data['queue'][0]['eta_seconds'] == 600 + 60
    api.store.update(first, status='running')
    data = await (await api.client.get('/v1/home', headers=api.headers)).json()
    assert data['now']['mine'] is False and data['now']['title'] == '另一位用户的任务' and data['now']['id'] is None
    assert data['queue'][0]['position'] == 1
    other = await (await api.client.get('/v1/home', headers=api.other_headers)).json()
    assert other['now']['mine'] is True and other['now']['id'] == first and other['queue'] == []


async def test_home_lists_paused_work_until_the_conversation_moves_on(api):
    session = await create_session(api, 'Notes')
    jid = api.store.enqueue(api.owner['user_id'], None, 'chat', 'organise my notes', session_id=session['id'], channel='app')
    api.store.update(jid, status='succeeded', outcome='paused')
    data = await (await api.client.get('/v1/home', headers=api.headers)).json()
    assert [(item['id'], item['reason']) for item in data['attention']] == [(jid, 'paused')]
    later = api.store.enqueue(api.owner['user_id'], None, 'chat', '继续', session_id=session['id'], channel='app')
    api.store.db.execute('UPDATE jobs SET created=created+1 WHERE id=?', (later,))
    api.store.db.commit()
    data = await (await api.client.get('/v1/home', headers=api.headers)).json()
    assert data['attention'] == [] and [item['id'] for item in data['active']] == [later]


async def test_sent_media_is_attached_to_the_reply_that_sent_it(api):
    # In the App, send_media uploads nothing: the reply carries the media ids
    # so the user sees the image where the assistant says it was sent.
    session = await create_session(api, 'media')
    first = await submit(api, session, '画一只猫', 'm-1')
    image = api.store.enqueue(api.owner['user_id'], None, 'image', 'cat', parent_job_id=first['id'],
                              session_id=session['id'], channel='app')
    api.store.update(image, status='succeeded', result=str(api.store.path.parent / 'cat.png'))
    second = await submit(api, session, '把图发给我', 'm-2')
    result = {'items': [{'id': image, 'status': 'available'}, {'id': 'gone', 'status': 'unavailable'}],
              'available': 1, 'delivery': 'available_in_session'}
    for call, name, status in (('c1', 'send_media', 'succeeded'), ('c2', 'list_media', 'succeeded')):
        api.store.db.execute("INSERT INTO tool_operations(job_id,call_id,name,arguments,fingerprint,effect,replay,status,result,started_at) "
                             "VALUES(?,?,?,?,?,?,?,?,?,?)", (second['id'], call, name, '{}', call, 'read', 'safe', status, json.dumps(result), time.time()))
    api.store.db.commit()
    items = {i['id']: i for i in (await (await api.client.get(f"/v1/sessions/{session['id']}/messages", headers=api.headers)).json())['items']}
    assert items[second['id']]['sent_media'] == [image]
    assert 'sent_media' not in items[first['id']]
