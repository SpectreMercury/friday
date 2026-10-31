import base64
import hashlib
import secrets
import time
from urllib.parse import urlparse, parse_qs
import pytest
from local_ai.native_auth import NativeAuth
from local_ai.store import Store

@pytest.fixture
def auth(tmp_path):
    store=Store(tmp_path/'db')
    owner=store.create_account('owner', is_admin=True)
    friend=store.create_account('friend')
    auth=NativeAuth(store)
    yield auth,owner,friend
    store.db.close()

def begin(auth):
    verifier=secrets.token_urlsafe(32)
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    state=secrets.token_urlsafe(32)
    return auth.start(challenge,state),verifier,state

def test_native_handoff_requires_pkce_and_is_one_use(auth):
    a,owner,_=auth
    flow,verifier,state=begin(a)
    callback=a.authorize(flow,owner['id'],time.time())
    url=urlparse(callback);args=parse_qs(url.query)
    assert url.scheme=='friday-private' and url.netloc=='auth'
    assert args['state']==[state]
    assert 'token' not in args
    with pytest.raises(PermissionError):a.exchange(args['code'][0],secrets.token_urlsafe(32))
    pairing=a.exchange(args['code'][0],verifier)
    assert a.mobile.authenticate(pairing['token'])['account_id']==owner['id']
    with pytest.raises(PermissionError):a.exchange(args['code'][0],verifier)

def test_native_handoff_requires_fresh_owner_authentication(auth):
    a,owner,friend=auth
    flow,verifier,state=begin(a)
    with pytest.raises(PermissionError):a.authorize(flow,owner['id'],time.time()-3600)
    with pytest.raises(PermissionError):a.authorize(flow,friend['id'],time.time())
    a.authorize(flow,owner['id'],time.time())
    with pytest.raises(PermissionError):a.authorize(flow,owner['id'],time.time())

def test_native_expiration_and_account_disable(auth):
    a,owner,_=auth
    flow,verifier,state=begin(a)
    callback=a.authorize(flow,owner['id'],time.time())
    code=parse_qs(urlparse(callback).query)['code'][0]
    a.store.db.execute('UPDATE accounts SET enabled=0 WHERE id=?',(owner['id'],))
    with pytest.raises(PermissionError):a.exchange(code,verifier)
    a.store.db.execute('UPDATE native_auth_flows SET expires=0 WHERE id=?',(flow,))
    with pytest.raises(PermissionError):a.require(flow)

@pytest.mark.parametrize('challenge,state', [('x','a'*43),('a'*43,'../bad'),('https://evil','a'*43)])
def test_native_rejects_malformed_requests(auth,challenge,state):
    with pytest.raises(ValueError):auth[0].start(challenge,state)

async def test_native_http_handoff_requires_browser_csrf_and_fresh_passkey(tmp_path,monkeypatch):
    import asyncio
    import aiohttp
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from aiohttp.test_utils import TestClient,TestServer
    from local_ai.mobile_api import create_app
    from local_ai.browser_auth import BrowserAuth
    from local_ai.harness import HarnessService
    from test_browser_api import register
    from test_passkeys import ORIGIN
    monkeypatch.setenv('MOBILE_NATIVE_ENABLED','1')
    monkeypatch.setenv('MOBILE_NATIVE_PAIRING','0')
    for name in ('APNS_TEAM_ID','APNS_KEY_ID','APNS_KEY_FILE','APNS_BUNDLE_ID'):monkeypatch.delenv(name,raising=False)
    store=Store(tmp_path/'http.db');owner=store.create_account('Owner',is_admin=True)
    engine=SimpleNamespace(speech_worker=None,lock=asyncio.Lock(),start=AsyncMock(),stop=AsyncMock(),process=None,voice_reserved=False)
    service=HarnessService(tmp_path,store=store,engine=engine)
    async with TestClient(TestServer(create_app(service,web_origin=ORIGIN)),cookie_jar=aiohttp.DummyCookieJar()) as client:
        api=SimpleNamespace(client=client,store=store,service=service,auth=BrowserAuth(store,ORIGIN),owner=owner)
        old=await register(api)
        verifier=secrets.token_urlsafe(32);challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        r=await client.post('/v1/native/auth/start',json={'challenge':challenge,'state':secrets.token_urlsafe(32)})
        assert r.status==200
        flow=parse_qs(urlparse((await r.json())['url']).query)['native'][0]
        assert (await client.post('/v1/native/auth/authorize',json={'flow':flow},headers=old.headers)).status==403
        login=await register(api)
        missing=dict(login.headers);missing.pop('X-CSRF-Token')
        assert (await client.post('/v1/native/auth/authorize',json={'flow':flow},headers=missing)).status==403
        r=await client.post('/v1/native/auth/authorize',json={'flow':flow},headers=login.headers)
        assert r.status==200,await r.text()
        code=parse_qs(urlparse((await r.json())['url']).query)['code'][0]
        r=await client.post('/v1/native/auth/exchange',json={'code':code,'verifier':verifier})
        assert r.status==200,await r.text()
        pair=await r.json();headers={'Authorization':'Bearer '+pair['token']}
        assert (await client.get('/v1/sessions',headers=headers)).status==200
        assert (await client.post('/v1/pair',json={'code':'x','device_name':'x'})).status==404
        assert (await client.post('/v1/native/auth/exchange',json={'code':code,'verifier':verifier})).status==403
        await client.delete('/v1/devices/'+pair['device_id'],headers=login.headers)
        assert (await client.get('/v1/sessions',headers=headers)).status==401
        # Revoking the browser that approved a handoff invalidates an unredeemed code.
        r=await client.post('/v1/native/auth/start',json={'challenge':challenge,'state':secrets.token_urlsafe(32)})
        second=parse_qs(urlparse((await r.json())['url']).query)['native'][0]
        fresh=await register(api)
        r=await client.post('/v1/native/auth/authorize',json={'flow':second},headers=fresh.headers)
        second_code=parse_qs(urlparse((await r.json())['url']).query)['code'][0]
        await client.post('/v1/web/auth/logout',json={},headers=fresh.headers)
        assert (await client.post('/v1/native/auth/exchange',json={'code':second_code,'verifier':verifier})).status==403
    store.db.close()
