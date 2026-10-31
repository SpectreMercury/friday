import json
from urllib.parse import parse_qs, urlsplit

import pytest

from local_ai.google_picker import GooglePicker
from local_ai.sessions import RevisionConflict
from test_connectors import setup


def make_picker(setup):
    manager, store, calls, secrets = setup
    manager.config.update({'GOOGLE_PICKER_API_KEY': 'browser-api-key', 'GOOGLE_PICKER_APP_ID': '123456789'})
    picker = GooglePicker(manager)
    return picker, manager, store, calls, secrets


def ticket(result): return parse_qs(urlsplit(result['url']).query)['ticket'][0]


@pytest.mark.asyncio
async def test_picker_explicit_selection_only_binds_origin_session(setup):
    picker, manager, store, calls, secrets = make_picker(setup)
    value = ticket(picker.start('owner', 'A', 'google1', 1))
    page = await picker.page(value)
    assert 'private-token' in page['html'] and 'refresh' not in page['html']
    assert 'no-store' in page['headers']['Cache-Control']
    assert "frame-ancestors 'none'" in page['headers']['Content-Security-Policy']
    assert 'history.replaceState' in page['html']
    result = await picker.select(value, ['existing-doc'])
    assert result['bindings'] == [{'connection_id': 'google1', 'resource_ids': ['existing-doc'], 'can_create': False}]
    assert manager.list_bindings('owner', 'B')['bindings'] == []
    assert value not in '\n'.join(store.db.iterdump())
    with pytest.raises(PermissionError): await picker.select(value, ['second-doc'])


@pytest.mark.asyncio
async def test_selection_preserves_creation_permission_and_existing_ids(setup):
    picker, manager, store, calls, secrets = make_picker(setup)
    manager.bind('owner', 'A', 'google1', ['old'], can_create=True)
    value = ticket(picker.start('owner', 'A', 'google1', 2))
    await picker.page(value)
    result = await picker.select(value, ['new', 'old'])
    assert result['bindings'] == [{'connection_id': 'google1', 'resource_ids': ['new', 'old'], 'can_create': True}]


@pytest.mark.asyncio
async def test_revision_conflict_does_not_overwrite_new_configuration(setup):
    picker, manager, store, calls, secrets = make_picker(setup)
    value = ticket(picker.start('owner', 'A', 'google1', 1))
    await picker.page(value)
    manager.bind('owner', 'A', 'google1', ['other'])
    with pytest.raises(RevisionConflict): await picker.select(value, ['new'])
    assert manager.list_bindings('owner', 'A')['bindings'][0]['resource_ids'] == ['other']


@pytest.mark.asyncio
async def test_ticket_wrong_account_revoke_expire_and_reopen(setup):
    picker, manager, store, calls, secrets = make_picker(setup)
    with pytest.raises(PermissionError): picker.start('friend', 'A', 'google1', 1)
    with pytest.raises(PermissionError): picker.start('friend', 'C', 'google1', 1)
    value = ticket(picker.start('owner', 'A', 'google1', 1))
    with pytest.raises(PermissionError): await picker.select(value, ['new'])
    await picker.page(value)
    with pytest.raises(PermissionError): await picker.page(value)
    store.db.execute('UPDATE connector_picker_tickets SET expires_at=0')
    with pytest.raises(PermissionError): await picker.select(value, ['new'])


@pytest.mark.asyncio
async def test_unverified_google_id_does_not_grant_binding(setup):
    picker, manager, store, calls, secrets = make_picker(setup)
    value = ticket(picker.start('owner', 'A', 'google1', 1))
    await picker.page(value)
    async def denied(*args, **kwargs): raise PermissionError('Google rejected file')
    manager.request = denied
    with pytest.raises(PermissionError): await picker.select(value, ['new'])
    assert manager.list_bindings('owner', 'A')['bindings'] == []


@pytest.mark.asyncio
async def test_html_escapes_provider_token_instead_of_injecting_script(setup):
    picker, manager, store, calls, secrets = make_picker(setup)
    secrets.set('secret1', json.dumps({'access_token': '</script><script>alert(1)</script>', 'expires_at': 10**11}))
    value = ticket(picker.start('owner', 'A', 'google1', 1))
    page = await picker.page(value)
    assert '</script><script>alert' not in page['html']
    assert '\\u003c/script\\u003e' in page['html']
