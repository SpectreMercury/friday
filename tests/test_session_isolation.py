"""Adversarial workspace boundaries, skill pinning, and legacy migration."""
import json
import sqlite3
from unittest.mock import AsyncMock

import pytest

from local_ai.capabilities import BUILTINS
from local_ai.pi_agent import UserTools
from local_ai.sessions import RevisionConflict, SessionSkills
from local_ai.store import Store


@pytest.fixture
def setup(tmp_path):
    skills = tmp_path / 'skills'
    for name in ('research', 'writing'):
        directory = skills / name
        directory.mkdir(parents=True)
        (directory / 'SKILL.md').write_text(f'{name}\nPRIVATE_SKILL_{name}_VERSION_1')
    store = Store(tmp_path / 'db', skills_root=skills)
    owner = store.create_account('Owner', is_admin=True)
    a = store.create_session(owner['id'], 'A', skills=['research'])
    b = store.create_session(owner['id'], 'B', skills=['writing'])
    yield store, owner, a, b, skills
    store.db.close()


def enqueue(store, account, session, prompt='request'):
    jid = store.enqueue(account['user_id'], None, 'chat', prompt,
                        session_id=session['id'], channel='app')
    return store.get(jid)


def user_tools(store, job):
    return UserTools(store, job, lambda uid: store.allowed(uid, set(), 0),
                     workspace=store.path.parent / 'workspaces')


def record_skill(store, job, text):
    spec = next(spec for spec in BUILTINS if spec.name == 'read_skill')
    store.begin_operation(job['id'], 'read-skill', spec, {'name': 'research'})
    store.finish_operation(job['id'], 'read-skill', result={'name': 'research', 'text': text})


async def test_same_account_sessions_separate_skills_files_history_and_evidence(setup):
    store, owner, a, b, _ = setup
    ja, jb = enqueue(store, owner, a, 'A private request'), enqueue(store, owner, b, 'B request')
    ta, tb = user_tools(store, ja), user_tools(store, jb)
    assert [s['name'] for s in (await ta.execute('list_skills', {}))['skills']] == ['research']
    assert [s['name'] for s in (await tb.execute('list_skills', {}))['skills']] == ['writing']
    with pytest.raises(PermissionError):
        await tb.execute('read_skill', {'name': 'research'})
    await ta.execute('write_file', {'name': 'notes.txt', 'content': 'A PRIVATE CONTENT'})
    await tb.execute('write_file', {'name': 'notes.txt', 'content': 'B PRIVATE CONTENT'})
    assert (await ta.execute('read_file', {'name': 'notes.txt'}))['content'] == 'A PRIVATE CONTENT'
    assert (await tb.execute('read_file', {'name': 'notes.txt'}))['content'] == 'B PRIVATE CONTENT'
    store.update(ja['id'], status='succeeded', result='A private result')
    messages = store.messages(owner['user_id'], 'B again', session_id=b['id'])
    assert 'A private' not in json.dumps(messages)
    with pytest.raises(PermissionError):
        await tb.execute('task_evidence', {'job_id': ja['id']})
    with pytest.raises((ValueError, PermissionError)):
        await tb.execute('task_status', {'job_id': ja['id']})
    with pytest.raises(PermissionError):
        store.link_task(jb, ja['id'])


def test_account_cannot_use_another_accounts_session_or_request_key(setup):
    store, owner, a, b, _ = setup
    friend = store.create_account('Friend')
    foreign = store.create_session(friend['id'], 'Friend')
    original = enqueue(store, owner, a)
    for operation in (
        lambda: store.require_session(friend['id'], a['id']),
        lambda: store.workspace_directory(friend['id'], a['id']),
        lambda: store.get_session_job(friend['id'], foreign['id'], original['id']),
        lambda: store.enqueue(friend['user_id'], None, 'chat', 'foreign', session_id=a['id'], channel='app'),
        lambda: store.fork_session(friend['id'], a['id'], 'stolen', [], []),
    ):
        with pytest.raises(PermissionError):
            operation()
    job = store.enqueue(owner['user_id'], None, 'chat', 'request', session_id=b['id'], channel='app', request_id='retry')
    assert store.enqueue(owner['user_id'], None, 'chat', 'request', session_id=b['id'], channel='app', request_id='retry') == job
    with pytest.raises(ValueError, match='不同输入'):
        store.enqueue(owner['user_id'], None, 'chat', 'request', session_id=a['id'], channel='app', request_id='retry')


def test_skills_are_pinned_per_task_across_library_edits_and_restart(setup):
    store, owner, a, b, skills = setup
    first = enqueue(store, owner, a)
    old = SessionSkills(store, first['id']).read('research')
    (skills / 'research/SKILL.md').write_text('research\nNEW SKILL VERSION')
    second = enqueue(store, owner, a)
    new = SessionSkills(store, second['id']).read('research')
    assert old['text'] != new['text'] and old['version'] != new['version']
    assert SessionSkills(store, first['id']).read('research') == old
    reopened = Store(store.path, skills_root=skills)
    try:
        assert SessionSkills(reopened, first['id']).read('research') == old
        assert SessionSkills(reopened, second['id']).read('research') == new
    finally:
        reopened.db.close()


def test_skill_configuration_conflicts_are_not_silent_and_do_not_change_other_session(setup):
    store, owner, a, b, _ = setup
    updated = store.update_session(owner['id'], a['id'], a['revision'], skill_names=[])
    assert updated['skill_names'] == []
    with pytest.raises(RevisionConflict):
        store.update_session(owner['id'], a['id'], a['revision'], skill_names=['writing'])
    assert store.require_session(owner['id'], b['id'])['skill_names'] == ['writing']


async def test_disabled_skill_text_cannot_be_recovered_from_historical_evidence(setup):
    store, owner, a, b, _ = setup
    old = enqueue(store, owner, a)
    secret = SessionSkills(store, old['id']).read('research')['text']
    record_skill(store, old, secret)
    store.update(old['id'], status='succeeded', result='done')
    store.update_session(owner['id'], a['id'], a['revision'], skill_names=[])
    new = enqueue(store, owner, a)
    tools = user_tools(store, new)
    with pytest.raises(PermissionError):
        await tools.execute('read_skill', {'name': 'research'})
    evidence = await tools.execute('task_evidence', {'job_id': old['id']})
    assert 'PRIVATE_SKILL_' not in json.dumps(evidence)


def test_checkpoint_repair_does_not_restore_historical_skill_body(setup):
    store, owner, a, b, _ = setup
    old = enqueue(store, owner, a)
    secret = SessionSkills(store, old['id']).read('research')['text']
    record_skill(store, old, secret)
    # Crash after a skill read is committed, before its toolResult checkpoint.
    store.save_checkpoint(old['id'], [{'role': 'assistant', 'content': [
        {'type': 'toolCall', 'id': 'read-skill', 'name': 'read_skill', 'arguments': {'name': 'research'}}]}])
    store.update(old['id'], status='failed', error='interrupted')
    store.update_session(owner['id'], a['id'], a['revision'], skill_names=[])
    new = enqueue(store, owner, a)
    carried = store.conversation(new)
    assert 'PRIVATE_SKILL_' not in json.dumps(carried)
    assert any(m['role'] == 'toolResult' for m in carried)


def test_fork_copies_only_selected_material_and_skills_without_authority(setup):
    store, owner, a, b, _ = setup
    enabled = store.update_session(owner['id'], a['id'], a['revision'], management=True)
    job = enqueue(store, owner, a, 'selected user prompt')
    store.update(job['id'], status='succeeded', result='selected final result')
    store.workspace_file(owner['id'], a['id'], 'selected.txt').write_text('selected file')
    store.workspace_file(owner['id'], a['id'], 'private.txt').write_text('not selected')
    target = store.fork_session(owner['id'], a['id'], 'Fork', [job['id']], ['selected.txt'])
    assert target['skill_names'] == enabled['skill_names'] and not target['management']
    assert store.workspace_file(owner['id'], target['id'], 'selected.txt').read_text() == 'selected file'
    assert not store.workspace_file(owner['id'], target['id'], 'private.txt').exists()
    assert store.db.execute('SELECT count(*) FROM jobs WHERE session_id=?', (target['id'],)).fetchone()[0] == 0
    imports = store.imported_context(target['id'])
    assert imports[0]['prompt'] == job['prompt'] and imports[0]['result'] == 'selected final result'
    store.workspace_file(owner['id'], a['id'], 'selected.txt').write_text('source changed')
    assert store.workspace_file(owner['id'], target['id'], 'selected.txt').read_text() == 'selected file'


async def test_symlink_and_hardlink_files_cannot_bridge_workspaces(setup):
    store, owner, a, b, _ = setup
    source = store.workspace_file(owner['id'], a['id'], 'secret.txt')
    source.write_text('PRIVATE A')
    directory = store.workspace_directory(owner['id'], b['id'])
    (directory / 'symlink.txt').symlink_to(source)
    (directory / 'hardlink.txt').hardlink_to(source)
    job = enqueue(store, owner, b)
    tools = user_tools(store, job)
    for name in ('symlink.txt', 'hardlink.txt'):
        with pytest.raises((OSError, ValueError)):
            await tools.execute('read_file', {'name': name})
        with pytest.raises((OSError, ValueError)):
            await tools.execute('write_file', {'name': name, 'content': 'overwrite'})
    assert source.read_text() == 'PRIVATE A'


def test_reset_archive_and_revocation_do_not_touch_other_sessions(setup):
    store, owner, a, b, _ = setup
    ja, jb = enqueue(store, owner, a, 'A text'), enqueue(store, owner, b, 'B text')
    store.update(jb['id'], status='succeeded', result='B reply')
    telegram=store.telegram_session(owner['user_id'],owner['user_id'])
    store.remember(owner['user_id'],'Telegram private text','Telegram reply')
    store.update_session(owner['id'], a['id'], a['revision'], archived=True)
    assert store.get(ja['id'])['status'] == 'queued'
    store.reset(owner['user_id'], a['id'])
    assert 'B text' in json.dumps(store.messages(owner['user_id'], 'followup', session_id=b['id']))
    assert 'Telegram private text' in json.dumps(store.messages(owner['user_id'],'followup'))
    store.reset(owner['user_id'],telegram['id'])
    assert 'Telegram private text' not in json.dumps(store.messages(owner['user_id'],'followup'))
    store.set_access(owner['user_id'], False)
    with pytest.raises(PermissionError):
        store.require_session(owner['id'], a['id'])
    with pytest.raises(PermissionError):
        store.get_session_job(owner['id'], b['id'], jb['id'])


def test_legacy_revoked_access_does_not_create_enabled_mobile_account(tmp_path):
    store = Store(tmp_path / 'db')
    try:
        store.db.execute('INSERT INTO access VALUES (99,0)')
        store.db.commit()
        account = store.account_for_uid(99)
        assert not account['enabled']
    finally:
        store.db.close()


def test_legacy_database_backup_and_one_default_workspace_preserve_existing_jobs(tmp_path):
    database = tmp_path / 'db'
    with sqlite3.connect(database) as legacy:
        legacy.execute('''CREATE TABLE jobs(id TEXT PRIMARY KEY,user_id INTEGER,chat_id INTEGER,
            kind TEXT,prompt TEXT,image TEXT,hd INTEGER,status TEXT,created REAL,result TEXT,error TEXT,delivered INTEGER DEFAULT 0)''')
        legacy.execute("INSERT INTO jobs(id,user_id,chat_id,kind,prompt,status,created) VALUES ('old',42,42,'chat','legacy','running',1)")
    (tmp_path / 'workspaces/42').mkdir(parents=True)
    (tmp_path / 'workspaces/42/note.txt').write_text('legacy private note')
    store = Store(database)
    try:
        backups = list(tmp_path.glob('db.pre-sessions-*.bak'))
        assert len(backups) == 1 and backups[0].stat().st_mode & 0o777 == 0o600
        with sqlite3.connect(backups[0]) as backup:
            assert 'session_id' not in {r[1] for r in backup.execute('PRAGMA table_info(jobs)')}
        job = store.get('old')
        assert job['status'] == 'running'  # Opening a DB is not owner recovery.
        account = store.account_for_uid(42)
        default = store.telegram_session(42, 42)
        assert job['account_id'] == account['id'] and job['session_id'] == default['id']
        assert store.workspace_file(account['id'], default['id'], 'note.txt').read_text() == 'legacy private note'
        fresh = store.create_session(account['id'], 'Fresh')
        assert fresh['skill_names'] == []
        assert not store.workspace_file(account['id'], fresh['id'], 'note.txt').exists()
    finally:
        store.db.close()


async def test_telegram_owner_requires_explicit_management_and_disable_takes_effect(tmp_path):
    store = Store(tmp_path / 'db')
    try:
        account = store.account_for_uid(42)
        store.db.execute('UPDATE accounts SET is_admin=1 WHERE id=?', (account['id'],))
        store.db.commit()
        session = store.telegram_session(42, 42)
        jid = store.enqueue(42, 42, 'chat', 'check my Mac')
        tools = UserTools(store, store.get(jid), lambda _: True, admin=42)
        assert not tools.host_enabled
        with pytest.raises(ValueError, match='已启用管理权限'):
            await tools.execute('computer_list', {'path': '/irrelevant'})
        enabled = store.update_session(account['id'], session['id'], session['revision'], management=True)
        tools = UserTools(store, store.get(jid), lambda _: True, admin=42)
        tools.computer.execute = AsyncMock(return_value={'entries': []})
        await tools.execute('computer_list', {'path': '/irrelevant'})
        store.update_session(account['id'], session['id'], enabled['revision'], management=False)
        with pytest.raises(ValueError, match='已启用管理权限'):
            await tools.execute('computer_list', {'path': '/irrelevant'})
        tools.computer.execute.assert_awaited_once()
    finally:
        store.db.close()


def test_revoked_legacy_user_migrates_without_restoring_access(tmp_path):
    database = tmp_path / 'db'
    with sqlite3.connect(database) as legacy:
        legacy.execute('CREATE TABLE access(user_id INTEGER PRIMARY KEY,allowed INTEGER)')
        legacy.execute('INSERT INTO access VALUES (42,0)')
        legacy.execute('''CREATE TABLE jobs(id TEXT PRIMARY KEY,user_id INTEGER,chat_id INTEGER,
            kind TEXT,prompt TEXT,image TEXT,hd INTEGER,status TEXT,created REAL,result TEXT,error TEXT,delivered INTEGER DEFAULT 0)''')
        legacy.execute("INSERT INTO jobs(id,user_id,chat_id,kind,prompt,status,created) VALUES ('old',42,42,'chat','legacy','running',1)")
    store = Store(database)
    try:
        account = store.account_for_uid(42)
        assert not account['enabled']
        job = store.get('old')
        assert job['account_id'] == account['id'] and job['session_id']
        with pytest.raises(PermissionError):
            store.require_session(account['id'], job['session_id'])
    finally:
        store.db.close()
