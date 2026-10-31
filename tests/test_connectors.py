import json
import sqlite3
import time
from urllib.parse import parse_qs, urlsplit

import pytest

from local_ai.capabilities import CapabilityRegistry
from local_ai.connectors import ConnectorManager, MemorySecretStore, GOOGLE_SCOPE, google_request
from local_ai.tool_errors import ToolFailure


class SessionStore:
    def __init__(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE accounts(id TEXT PRIMARY KEY,is_admin INTEGER,enabled INTEGER);
            INSERT INTO accounts VALUES ('owner',1,1),('friend',0,1);
            CREATE TABLE sessions(id TEXT PRIMARY KEY,account_id TEXT,revision INTEGER,management INTEGER);
            INSERT INTO sessions VALUES ('A','owner',1,1),('B','owner',1,0),('C','friend',1,0);
        ''')
    def account(self, account_id):
        row = self.db.execute('SELECT * FROM accounts WHERE id=?', (account_id,)).fetchone()
        return dict(row) if row else None
    def require_session(self, account_id, session_id):
        row = self.db.execute('SELECT * FROM sessions WHERE id=? AND account_id=?', (session_id, account_id)).fetchone()
        if not row: raise PermissionError('Session not owned')
        return dict(row)


@pytest.fixture
def setup():
    store, secrets = SessionStore(), MemorySecretStore()
    calls = []
    async def transport(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith('/token'): return {'access_token': 'new-token', 'refresh_token': 'refresh', 'expires_in': 3600, 'scope': GOOGLE_SCOPE}
        if method == 'GET' and '/documents/' in url:
            return {'documentId': 'allowed', 'title': 'A doc', 'tabs': [{'documentTab': {'body': {'content': [{'paragraph': {'elements': [{'textRun': {'content': 'Hello'}}]}}]}}}]}
        if url.endswith(':batchUpdate'): return {'documentId': 'allowed', 'writeControl': {'requiredRevisionId': 'next'}}
        if method == 'POST': return {'documentId': 'created'}
        return {'id': url.rsplit('/', 1)[-1], 'name': 'Bound doc'}
    manager = ConnectorManager(store, secrets, {'GOOGLE_CLIENT_ID': 'client', 'GOOGLE_CLIENT_SECRET': 'secret', 'GOOGLE_REDIRECT_URI': 'https://example.com/v1/oauth/google/callback'}, transport)
    store.db.execute("INSERT INTO connector_connections VALUES ('google1','owner','google','Work','connected','secret1',0)")
    store.db.commit()
    secrets.set('secret1', json.dumps({'access_token': 'private-token', 'refresh_token': 'refresh', 'expires_at': time.time() + 3600}))
    return manager, store, calls, secrets


def test_atomic_bindings_revision_and_ownership(setup):
    manager, store, calls, secrets = setup
    result = manager.set_bindings('owner', 'A', [{'connection_id': 'google1', 'resource_ids': ['allowed'], 'can_create': True}], 1)
    assert result['revision'] == 2
    with pytest.raises(ValueError, match='revision_conflict'):
        manager.set_bindings('owner', 'A', [], 1)
    with pytest.raises(PermissionError):
        manager.set_bindings('friend', 'C', [{'connection_id': 'google1'}], 1)
    assert manager.list_bindings('owner', 'B')['bindings'] == []
    assert manager.list_bindings('owner', 'A')['bindings'][0]['resource_ids'] == ['allowed']


@pytest.mark.asyncio
async def test_different_session_and_revocation_cannot_reuse_cached_registry(setup):
    manager, store, calls, _ = setup
    manager.bind('owner', 'A', 'google1', ['allowed'])
    registry = CapabilityRegistry()
    manager.register(registry, {'account_id': 'owner', 'session_id': 'A'})
    assert await registry.execute('google_read_doc', {'connection_id': 'google1', 'document_id': 'allowed'}) == {
        'document_id': 'allowed', 'title': 'A doc', 'revision_id': None, 'text': 'Hello', 'next_offset': None, 'untrusted_content': True}
    for account, session, document in [('owner', 'A', 'other'), ('owner', 'B', 'allowed'), ('friend', 'A', 'allowed')]:
        with pytest.raises(PermissionError):
            await manager.execute(account, session, 'google_read_doc', {'connection_id': 'google1', 'document_id': document})
    assert len(calls) == 1
    manager.set_bindings('owner', 'A', [], 2)
    with pytest.raises(ToolFailure, match='未绑定'):
        await registry.execute('google_read_doc', {'connection_id': 'google1', 'document_id': 'allowed'})
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_create_only_grants_result_to_originating_session(setup):
    manager, store, calls, _ = setup
    manager.bind('owner', 'A', 'google1', [], can_create=True)
    manager.bind('owner', 'B', 'google1', [])
    result = await manager.execute('owner', 'A', 'google_create_doc', {'connection_id': 'google1', 'title': 'New'})
    assert result['document_id'] == 'created'
    assert manager.list_bindings('owner', 'A')['bindings'][0]['resource_ids'] == ['created']
    assert manager.list_bindings('owner', 'B')['bindings'][0]['resource_ids'] == []
    with pytest.raises(PermissionError):
        await manager.execute('owner', 'B', 'google_create_doc', {'connection_id': 'google1', 'title': 'New'})
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_oauth_bound_to_initial_account_pkce_single_use_no_db_credentials(setup):
    manager, store, calls, secrets = setup
    first = manager.oauth_start('friend', 'C')
    query = parse_qs(urlsplit(first['auth_url']).query)
    assert query['code_challenge_method'] == ['S256']
    assert query['scope'] == [GOOGLE_SCOPE]
    result = await manager.oauth_exchange(first['state'], 'code')
    assert result['id'] in {c['id'] for c in manager.list_connections('friend')}
    assert result['id'] not in {c['id'] for c in manager.list_connections('owner')}
    assert calls[0][2]['data']['code_verifier']
    with pytest.raises(PermissionError): await manager.oauth_exchange(first['state'], 'code')
    assert len(calls) == 2
    dump = '\n'.join(store.db.iterdump())
    assert 'new-token' not in dump and 'private-token' not in dump and first['state'] not in dump
    assert manager.list_bindings('friend', 'C')['bindings'] == []


@pytest.mark.asyncio
async def test_refresh_and_document_revision_are_forwarded(setup):
    manager, store, calls, secrets = setup
    manager.bind('owner', 'A', 'google1', ['allowed'])
    secrets.set('secret1', json.dumps({'access_token': 'expired', 'refresh_token': 'refresh', 'expires_at': 0}))
    result = await manager.execute('owner', 'A', 'google_append_doc', {'connection_id': 'google1', 'document_id': 'allowed', 'text': 'Hello', 'revision_id': 'before'})
    assert result['updated']
    assert calls[0][1].endswith('/token')
    assert calls[1][2]['headers']['Authorization'] == 'Bearer new-token'
    assert calls[1][2]['json']['writeControl'] == {'requiredRevisionId': 'before'}


@pytest.mark.asyncio
async def test_revoke_during_refresh_prevents_resource_request(setup):
    manager, store, calls, secrets = setup
    manager.bind('owner', 'A', 'google1', ['allowed'])
    secrets.set('secret1', json.dumps({'access_token': 'expired', 'refresh_token': 'refresh', 'expires_at': 0}))
    async def refresh(method, url, **kwargs):
        manager.disconnect('owner', 'google1')
        return {'access_token': 'new-token', 'expires_in': 3600}
    manager.request = refresh
    with pytest.raises(PermissionError):
        await manager.execute('owner', 'A', 'google_read_doc', {'connection_id': 'google1', 'document_id': 'allowed'})
    assert secrets.get('secret1') is None


@pytest.mark.asyncio
async def test_write_failure_is_not_retried(setup):
    manager, store, calls, _ = setup
    manager.bind('owner', 'A', 'google1', [], can_create=True)
    count = 0
    async def fail(method, url, **kwargs):
        nonlocal count
        count += 1
        raise ToolFailure('google_network_error', 'lost', uncertain=True)
    manager.request = fail
    with pytest.raises(ToolFailure) as error:
        await manager.execute('owner', 'A', 'google_create_doc', {'connection_id': 'google1', 'title': 'New'})
    assert error.value.uncertain and count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('url', ['http://docs.googleapis.com/v1', 'https://evil.example/token', 'https://docs.googleapis.com:444/v1'])
async def test_transport_rejects_non_provider_endpoints(url):
    with pytest.raises(ValueError): await google_request('GET', url)


def test_disconnect_invalidates_bindings_and_removes_credentials(setup):
    manager, store, calls, secrets = setup
    manager.bind('owner', 'A', 'google1', ['allowed'])
    manager.disconnect('owner', 'google1')
    assert manager.list_bindings('owner', 'A') == {'revision': 3, 'bindings': []}
    assert secrets.get('secret1') is None
    assert manager.list_connections('owner')[0]['status'] == 'disconnected'
