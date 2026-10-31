import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from local_ai.capabilities import BUILTINS
from local_ai.desktop_tools import DesktopTools, DESKTOP_TOOLS
from local_ai.network_tools import NetworkTools, NETWORK_TOOLS, diagnostic_url
from local_ai.pi_agent import UserTools
from local_ai.store import Store
from local_ai.tool_errors import ToolFailure


@pytest.fixture
def managed(tmp_path):
    store = Store(tmp_path / 'db')
    owner = store.create_account('Owner', is_admin=True)
    session = store.create_session(owner['id'], 'Desktop')
    session = store.update_session(owner['id'], session['id'], session['revision'], management=True)
    jid = store.enqueue(owner['user_id'], None, 'chat', 'Test desktop', session_id=session['id'], channel='app')
    tools = UserTools(store, store.get(jid), lambda _: True, tmp_path / 'workspaces', approve=AsyncMock(return_value=True))
    yield store, owner, session, tools
    store.db.close()


async def test_new_tools_hidden_and_enforced_without_management(managed):
    store, owner, session, tools = managed
    store.update_session(owner['id'], session['id'], session['revision'], management=False)
    fresh = UserTools(store, tools.job, lambda _: True, tools.workspace)
    assert not ({v['name'] for v in fresh.registry().describe()} & (DESKTOP_TOOLS | NETWORK_TOOLS))
    for name in DESKTOP_TOOLS | NETWORK_TOOLS:
        with pytest.raises(ValueError, match='管理员'):
            await tools.execute(name, {})


async def test_open_app_rechecks_revocation_after_approval(managed):
    store, owner, session, tools = managed
    async def revoke(*args, **kw):
        store.update_session(owner['id'], session['id'], session['revision'], management=False)
        return True
    tools.computer.approve = revoke
    tools.computer.desktop.invoke = AsyncMock()
    with pytest.raises(PermissionError, match='撤销'):
        await tools.execute('computer_open_app', {'bundle_id': 'com.apple.calculator'})
    tools.computer.desktop.invoke.assert_not_awaited()


async def test_screenshot_is_private_session_file_and_attachment(managed, monkeypatch):
    store, owner, session, tools = managed
    async def capture(name, args, *, screenshot):
        Image.new('RGB', (30, 20), 'white').save(screenshot)
        return {'captured': True, 'text': 'untrusted screen text', 'untrusted_content': True}
    monkeypatch.setattr(tools.computer.desktop, 'execute', capture)
    result = await tools.execute('computer_screen', {})
    path = tools.directory() / result['filename']
    assert path.is_file() and path.stat().st_mode & 0o777 == 0o600
    assert result['download_url'].startswith('/v1/sessions/' + session['id'] + '/files/')
    assert tools.attachments.owned(result['attachment_id'])['session_id'] == session['id']
    other = store.create_account('Other')
    with pytest.raises(PermissionError):
        store.workspace_file(other['id'], session['id'], result['filename'])


async def test_screenshot_revocation_during_capture_does_not_save(managed, monkeypatch):
    store, owner, session, tools = managed
    async def capture(name, args, *, screenshot):
        Image.new('RGB', (30, 20)).save(screenshot)
        store.update_session(owner['id'], session['id'], session['revision'], management=False)
        return {'captured': True}
    monkeypatch.setattr(tools.computer.desktop, 'execute', capture)
    with pytest.raises(PermissionError):
        await tools.execute('computer_screen', {})
    assert not tools.attachments.list() and not list(tools.directory().iterdir())


async def test_missing_screen_permission_is_actionable(tmp_path, monkeypatch):
    desktop = DesktopTools(AsyncMock(), lambda: None, home=tmp_path)
    monkeypatch.setattr(desktop, 'available', lambda: (True, None))
    async def run(argv, **kw):
        Path(argv[argv.index('--stdout') + 1]).write_text(json.dumps({'error': 'screen_permission_required', 'message': '需要授权'}))
        return {'exit_code': 0}
    monkeypatch.setattr('local_ai.desktop_tools.run_process', run)
    with pytest.raises(ToolFailure) as error:
        await desktop.execute('computer_screen', {}, screenshot=tmp_path / 'screen.png')
    assert error.value.requires_user and error.value.code == 'screen_permission_required'


async def test_app_launch_denied_does_not_invoke_helper(managed):
    *_, tools = managed
    tools.computer.approve.return_value = False
    tools.computer.desktop.invoke = AsyncMock()
    with pytest.raises(ValueError, match='未获确认'):
        await tools.execute('computer_open_app', {'bundle_id': 'com.apple.calculator'})
    tools.computer.desktop.invoke.assert_not_awaited()


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'https://name:pass@example.com',
    'http://example.com', 'https://example.com/?token=secret', 'https://example.com/#secret',
    'https://example.com:invalid', 'https://example.com\n--insecure', '--config=/tmp/evil'])
def test_diagnostic_url_rejects_unsafe_arguments(url):
    with pytest.raises(ValueError): diagnostic_url(url)


async def test_probe_preserves_tls_and_handles_auth_response(tmp_path, monkeypatch):
    run = AsyncMock(return_value={'exit_code': 0, 'stdout': json.dumps({'http_code': 401, 'ssl_verify_result': 0}), 'stderr': ''})
    monkeypatch.setattr('local_ai.network_tools.run_process', run)
    result = await NetworkTools(AsyncMock(), lambda: None, home=tmp_path).probe('https://example.com/login')
    assert result['transport_ok'] and result['certificate_verified'] and result['http_status'] == 401
    argv = run.await_args.args[0]
    assert '--disable' in argv and '--head' in argv and '--insecure' not in argv and '--location' not in argv


def fake_network(tmp_path, *, reachable=True, verify=True, probe_error=False):
    network = NetworkTools(AsyncMock(), lambda: None, home=tmp_path)
    state = {'selected': 'old', 'writes': []}
    async def api(path, *, method='GET', data=None, params=None):
        if path.endswith('/delay'): return {'delay': 50} if reachable else {}
        if method == 'PUT':
            state['selected'] = data['name']; state['writes'].append(data['name']); return {}
        return {'type': 'Selector', 'now': state['selected'], 'all': ['old', 'new']}
    network.api = api
    network.probe = AsyncMock(side_effect=TimeoutError() if probe_error else None,
                             return_value={'service_ok': verify, 'transport_ok': verify})
    return network, state


REPAIR = {'action': 'select_proxy', 'group': 'Proxy', 'node': 'new', 'url': 'https://example.com'}


async def test_failed_candidate_never_changes_proxy(tmp_path):
    network, state = fake_network(tmp_path, reachable=False)
    with pytest.raises(ToolFailure, match='候选'):
        await network.execute('network_repair', REPAIR)
    assert state['writes'] == []
    network.confirm.assert_not_awaited()


@pytest.mark.parametrize('verify', [True, False])
async def test_proxy_switch_verifies_and_rolls_back_failure(tmp_path, verify):
    network, state = fake_network(tmp_path, verify=verify)
    result = await network.execute('network_repair', REPAIR)
    assert result['verified'] is verify
    assert state['writes'] == (['new'] if verify else ['new', 'old'])
    assert state['selected'] == ('new' if verify else 'old')
    assert 'old → new' in network.confirm.await_args.args[0]


async def test_proxy_probe_exception_rolls_back(tmp_path):
    network, state = fake_network(tmp_path, probe_error=True)
    with pytest.raises(ToolFailure, match='已核实恢复'):
        await network.execute('network_repair', REPAIR)
    assert state['writes'] == ['new', 'old']


async def test_external_proxy_change_during_approval_is_preserved(tmp_path):
    network, state = fake_network(tmp_path)
    async def changed(*args): state['selected'] = 'user-choice'
    network.confirm = changed
    with pytest.raises(ToolFailure, match='其他操作'):
        await network.execute('network_repair', REPAIR)
    assert state['selected'] == 'user-choice' and not state['writes']


async def test_repair_revocation_prevents_mutation(tmp_path):
    network, state = fake_network(tmp_path)
    def denied(): raise PermissionError('revoked')
    network.guard = denied
    with pytest.raises(PermissionError):
        await network.execute('network_repair', REPAIR)
    assert not state['writes']


def test_mutations_are_not_blindly_replayed():
    specs = {item.name: item for item in BUILTINS}
    for name in DESKTOP_TOOLS | NETWORK_TOOLS:
        assert specs[name].access == 'admin'
    for name in ('computer_screen', 'computer_open_app', 'network_repair'):
        assert specs[name].replay == 'reconcile'


async def test_mac_discovery_explains_permission_gap_and_can_enable(managed):
    from local_ai.outcome_evidence import evidence_catalog
    store, owner, session, tools = managed
    store.update_session(owner['id'], session['id'], session['revision'], management=False)
    result = await tools.execute('capabilities', {'group': 'computer'})
    assert result['requires_user'] and result['code']=='mac_management_required'
    assert result['access']['can_enable'] and not result['access']['mac_management']
    assert not result['capabilities'] and 'Mac 管理' in result['message']
    spec = next(s for s in BUILTINS if s.name == 'capabilities')
    store.begin_operation(tools.job['id'], 'discover', spec, {'group':'computer'})
    store.finish_operation(tools.job['id'], 'discover', result=result)
    catalog = evidence_catalog(store, tools.job, store.task_facts(tools.job))
    assert any(v['status']=='needs_input' and v['kind']=='operation' for v in catalog)
