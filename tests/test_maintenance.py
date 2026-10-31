import sqlite3

import pytest

from local_ai.capabilities import CapabilityRegistry
from local_ai.maintenance import MaintenanceManager, Watchdog, probe_health


class Store:
    def __init__(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE accounts(id TEXT PRIMARY KEY,is_admin INTEGER,enabled INTEGER);
            INSERT INTO accounts VALUES ('owner',1,1),('friend',0,1);
            CREATE TABLE sessions(id TEXT PRIMARY KEY,account_id TEXT,management INTEGER);
            INSERT INTO sessions VALUES ('A','owner',1),('B','owner',0),('C','friend',1);
        ''')
    def account(self, account_id):
        row = self.db.execute('SELECT * FROM accounts WHERE id=?', (account_id,)).fetchone()
        return dict(row) if row else None
    def require_session(self, account_id, session_id):
        row = self.db.execute('SELECT * FROM sessions WHERE id=? AND account_id=?', (session_id, account_id)).fetchone()
        if not row: raise PermissionError('Not owned')
        return dict(row)


def test_only_owner_management_session_can_queue_known_service():
    manager = MaintenanceManager(Store())
    for account, session in [('owner', 'B'), ('friend', 'C'), ('friend', 'A')]:
        with pytest.raises(PermissionError): manager.restart(account, session, 'harness', 'req')
    with pytest.raises(ValueError): manager.restart('owner', 'A', 'networksetup', 'req')
    first = manager.restart('owner', 'A', 'harness', 'req')
    assert first == manager.restart('owner', 'A', 'harness', 'req')
    assert first['state'] == 'queued'


def test_watchdog_restart_receipt_and_deduplication():
    store, calls = Store(), []
    manager = MaintenanceManager(store)
    request = manager.restart('owner', 'A', 'harness', 'req')
    watchdog = Watchdog(store.db, restart=calls.append, clock=lambda: 10**11)
    watchdog.process_requests()
    watchdog.process_requests()
    assert calls == ['harness']
    assert manager.restart('owner', 'A', 'harness', 'req')['state'] == 'succeeded'


def test_watchdog_rechecks_revoked_permission():
    store, calls = Store(), []
    manager = MaintenanceManager(store)
    manager.restart('owner', 'A', 'harness', 'req')
    store.db.execute('UPDATE sessions SET management=0 WHERE id="A"')
    watchdog = Watchdog(store.db, restart=calls.append, clock=lambda: 10**11)
    watchdog.process_requests()
    assert not calls
    assert store.db.execute('SELECT state FROM maintenance_requests').fetchone()[0] == 'failed'


def test_auto_recovery_has_persistent_retry_limit_and_resets_when_healthy():
    store, calls, now = Store(), [], [100.0]
    watchdog = Watchdog(store.db, restart=calls.append, clock=lambda: now[0])
    for _ in range(12):
        watchdog.observe('tunnel', False)
        now[0] += 600
    assert calls == ['tunnel'] * 3
    restarted = Watchdog(store.db, restart=calls.append, clock=lambda: now[0])
    assert not restarted.observe('tunnel', False)
    restarted.observe('tunnel', True)
    for _ in range(3): restarted.observe('tunnel', False)
    assert len(calls) == 4


def test_watchdog_does_not_replay_interrupted_request():
    store, calls = Store(), []
    manager = MaintenanceManager(store)
    manager.restart('owner', 'A', 'tunnel', 'req')
    store.db.execute("UPDATE maintenance_requests SET state='running'")
    watchdog = Watchdog(store.db, restart=calls.append, clock=lambda: 10**11)
    watchdog.recover_interrupted()
    watchdog.process_requests()
    assert not calls
    assert store.db.execute('SELECT state FROM maintenance_requests').fetchone()[0] == 'uncertain'


@pytest.mark.asyncio
async def test_cached_registry_cannot_bypass_revoked_management():
    store = Store()
    manager = MaintenanceManager(store)
    registry = CapabilityRegistry(admin=True)
    manager.register(registry, {'id': 'job', 'account_id': 'owner', 'session_id': 'A'})
    assert {d['name'] for d in registry.definitions()} == {'mac_status', 'mac_restart_service'}
    store.db.execute('UPDATE sessions SET management=0 WHERE id="A"')
    assert registry.definitions() == []


def test_health_probe_does_not_allow_plaintext_remote():
    with pytest.raises(ValueError): probe_health('http://example.com/healthz')
