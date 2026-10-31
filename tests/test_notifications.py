import json
import sqlite3
from types import SimpleNamespace

import pytest

from local_ai.notifications import APNsNotifier


class Client:
    def __init__(self, status=200): self.calls, self.status = [], status
    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return SimpleNamespace(status_code=self.status)


def setup(status=200):
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.executescript('''
        CREATE TABLE accounts(id TEXT PRIMARY KEY,enabled INTEGER);
        INSERT INTO accounts VALUES ('a',1),('b',1);
        CREATE TABLE sessions(id TEXT PRIMARY KEY,account_id TEXT);
        INSERT INTO sessions VALUES ('s','a'),('other','b');
        CREATE TABLE mobile_devices(id TEXT PRIMARY KEY,account_id TEXT,expires REAL,revoked INTEGER,push_token TEXT,push_environment TEXT);
        CREATE TABLE session_events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,account_id TEXT,session_id TEXT,kind TEXT,payload TEXT,created REAL);
    ''')
    db.execute('INSERT INTO mobile_devices VALUES (?,?,?,?,?,?)', ('phone', 'a', 10**11, 0, 'aa'*32, 'sandbox'))
    db.execute('INSERT INTO mobile_devices VALUES (?,?,?,?,?,?)', ('otherphone', 'b', 10**11, 0, 'bb'*32, 'production'))
    db.commit()
    client = Client(status)
    notifier = APNsNotifier(SimpleNamespace(db=db), config={'APNS_TEAM_ID': '1234567890', 'APNS_KEY_ID': 'abcdefghij', 'APNS_KEY_FILE': '/not-read', 'APNS_BUNDLE_ID': 'app.test'}, client=client, signer=lambda: 'test-jwt')
    return notifier, db, client


def event(db, payload, kind='task_updated'):
    db.execute('INSERT INTO session_events(account_id,session_id,kind,payload,created) VALUES (?,?,?,?,0)', ('a','s',kind,json.dumps(payload)))
    db.commit()


@pytest.mark.asyncio
async def test_notifications_target_current_account_and_do_not_contain_task_text():
    notifier, db, client = setup()
    event(db, {'job_id': 'job', 'status': 'succeeded', 'result': 'private-content'})
    assert notifier.collect() == 1
    assert notifier.collect() == 0
    await notifier.flush()
    assert len(client.calls) == 1
    url, options = client.calls[0]
    assert url.endswith('aa'*32)
    assert options['json']['session_id'] == 's'
    assert 'private-content' not in json.dumps(options)
    assert db.execute('SELECT status FROM apns_outbox').fetchone()[0] == 'accepted'
    await notifier.flush()
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_revoked_device_does_not_receive_already_queued_push():
    notifier, db, client = setup()
    event(db, {'job_id': 'job', 'status': 'failed'})
    notifier.collect()
    db.execute("UPDATE mobile_devices SET revoked=1 WHERE id='phone'")
    await notifier.flush()
    assert client.calls == []
    assert db.execute('SELECT status FROM apns_outbox').fetchone()[0] == 'suppressed'


@pytest.mark.asyncio
async def test_apns_410_removes_stale_registration():
    notifier, db, client = setup(410)
    event(db, {'job_id': 'job'}, 'approval_requested')
    notifier.collect()
    await notifier.flush()
    assert db.execute("SELECT push_token FROM mobile_devices WHERE id='phone'").fetchone()[0] is None
    assert db.execute('SELECT status FROM apns_outbox').fetchone()[0] == 'suppressed'


@pytest.mark.asyncio
async def test_transient_failure_retries_stable_id_and_stops_after_five():
    notifier, db, client = setup(503)
    event(db, {'job_id': 'job', 'outcome': 'needs_input'})
    notifier.collect()
    for _ in range(7):
        db.execute('UPDATE apns_outbox SET retry_after=0')
        await notifier.flush()
    assert len(client.calls) == 5
    assert len({o['headers']['apns-id'] for _, o in client.calls}) == 1
    assert db.execute('SELECT status FROM apns_outbox').fetchone()[0] == 'failed'


def test_only_actionable_events_queued_and_cursor_survives_restart():
    notifier, db, client = setup()
    event(db, {'job_id': 'job', 'status': 'running'})
    event(db, {'job_id': 'job', 'delivered': 1})
    event(db, {'name': 'private session'}, 'session_created')
    notifier.collect()
    assert db.execute('SELECT count(*) FROM apns_outbox').fetchone()[0] == 0
    restarted = APNsNotifier(SimpleNamespace(db=db), config={})
    assert restarted.collect() == 0


def test_alerts_do_not_claim_pending_partial_or_continued_tasks_completed():
    for outcome in ('pending', 'continued'):
        assert APNsNotifier._alert({'kind': 'task_updated', 'payload': json.dumps({'status': 'succeeded', 'outcome': outcome})}) is None
    assert APNsNotifier._alert({'kind': 'task_updated', 'payload': json.dumps({'status': 'waiting_approval', 'outcome': 'needs_input'})}) is None
    partial = APNsNotifier._alert({'kind': 'task_updated', 'payload': json.dumps({'status': 'succeeded', 'outcome': 'partial'}), 'session_id': 's', 'account_id': 'a', 'sequence': 1})
    assert partial['kind'] == 'task_partial'
    assert partial['aps']['alert']['body'] == '任务已完成部分内容，请查看结果。'


def test_real_es256_signing_and_private_key_permissions(tmp_path):
    import jwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    notifier, db, client = setup()
    key = ec.generate_private_key(ec.SECP256R1())
    path = tmp_path / 'apns.p8'
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    path.chmod(0o600)
    notifier.config['APNS_KEY_FILE'] = str(path)
    notifier.signer = None
    token = notifier._token()
    assert jwt.decode(token, key.public_key(), algorithms=['ES256'])['iss'] == '1234567890'
    assert jwt.get_unverified_header(token)['kid'] == 'abcdefghij'
    assert notifier._token() == token
    notifier._jwt = None
    path.chmod(0o644)
    with pytest.raises(PermissionError): notifier._token()

@pytest.mark.asyncio
async def test_friday_cards_group_by_session_and_latest_task_update_wins():
    notifier,db,client=setup()
    event(db,{'job_id':'job','description':'secret action'},kind='approval_requested')
    event(db,{'job_id':'job','status':'succeeded','result':'private content'})
    assert notifier.collect()==2
    assert db.execute("SELECT count(*) FROM apns_outbox WHERE status='suppressed'").fetchone()[0]==1
    await notifier.flush()
    assert len(client.calls)==1
    payload=client.calls[0][1]['json']
    assert payload['account_id']=='a'
    assert payload['aps']['category']=='FRIDAY_RESULT'
    assert payload['aps']['thread-id']=='friday.s'
    assert payload['aps']['alert']['title']=='Friday'
    assert 'private content' not in json.dumps(payload)
    assert 'secret action' not in json.dumps(payload)
    collapse=client.calls[0][1]['headers']['apns-collapse-id']
    event(db,{'job_id':'job','status':'failed'})
    notifier.collect();await notifier.flush()
    assert client.calls[1][1]['headers']['apns-collapse-id']==collapse
    assert client.calls[1][1]['json']['aps']['category']=='FRIDAY_STOPPED'

@pytest.mark.asyncio
async def test_completed_shot_gets_numbered_private_push_without_story_text():
    notifier,db,client=setup()
    event(db,payload={'job_id':'clip-2','status':'succeeded','outcome':'completed','video_shot':{'index':2,'total':4},'result':'private story'})
    assert notifier.collect()==1
    await notifier.flush()
    payload=client.calls[0][1]['json']
    assert payload['aps']['alert']['body']=='镜头 2 / 4 已完成，点开查看。'
    assert 'private story' not in json.dumps(payload)
    event(db,payload={'job_id':'never-started','status':'cancelled','delivery_state':'suppressed'})
    notifier.collect();await notifier.flush()
    assert len(client.calls)==1
