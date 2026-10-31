"""One-use invitations and individually revocable mobile device credentials."""
import hashlib
import re
import secrets
import time
import uuid


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class MobileAuth:
    def __init__(self, store):
        self.store=store
        store.db.executescript('''
            CREATE TABLE IF NOT EXISTS mobile_invites(code_hash TEXT PRIMARY KEY,account_id TEXT NOT NULL,
                expires REAL NOT NULL,consumed REAL);
            CREATE TABLE IF NOT EXISTS mobile_devices(id TEXT PRIMARY KEY,account_id TEXT NOT NULL,
                token_hash TEXT UNIQUE NOT NULL,created REAL NOT NULL,expires REAL NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0,push_token TEXT,push_environment TEXT,device_name TEXT NOT NULL);
        ''')
        store.db.execute('CREATE TABLE IF NOT EXISTS mobile_push_subscriptions(device_id TEXT PRIMARY KEY,since_sequence INTEGER NOT NULL)')
        store.db.commit()

    def invite(self, account_id, ttl=600):
        if not self.store.account(account_id)['enabled']:
            raise PermissionError('账号已停用。')
        code=secrets.token_urlsafe(24)
        with self.store.db:
            self.store.db.execute('INSERT INTO mobile_invites VALUES (?,?,?,NULL)',(digest(code),account_id,time.time()+ttl))
        return code

    def pair(self, code, device_name):
        if not isinstance(code,str) or not 1<=len(code)<=200 or not isinstance(device_name,str) or not 1<=len(device_name)<=100:
            raise ValueError('请填写有效邀请码与设备名称。')
        now=time.time()
        row=self.store.db.execute('SELECT * FROM mobile_invites WHERE code_hash=?',(digest(code),)).fetchone()
        if not row or row['expires']<=now or row['consumed']:
            raise PermissionError('邀请码无效、已使用或已过期。')
        account=self.store.account(row['account_id'])
        if not account['enabled']:
            raise PermissionError('账号已停用。')
        token=secrets.token_urlsafe(48);did=uuid.uuid4().hex
        with self.store.db:
            changed=self.store.db.execute('UPDATE mobile_invites SET consumed=? WHERE code_hash=? AND consumed IS NULL AND expires>?',(now,digest(code),now))
            if changed.rowcount!=1:
                raise PermissionError('邀请码已使用。')
            self.store.db.execute('INSERT INTO mobile_devices(id,account_id,token_hash,created,expires,device_name) VALUES (?,?,?,?,?,?)',
                                 (did,account['id'],digest(token),now,now+90*86400,device_name))
        return {'token':token,'device_id':did,'account':{'id':account['id'],'display_name':account['display_name'],'is_admin':bool(account['is_admin'])}}

    def authenticate(self, token):
        if not isinstance(token,str) or len(token)>200:
            raise PermissionError('设备凭据无效。')
        row=self.store.db.execute('SELECT d.*,a.enabled FROM mobile_devices d JOIN accounts a ON a.id=d.account_id WHERE token_hash=?',(digest(token),)).fetchone()
        if not row or row['revoked'] or not row['enabled'] or row['expires']<=time.time():
            raise PermissionError('设备凭据无效或已撤销，请重新配对。')
        return dict(row)

    def devices(self, account_id):
        return [dict(r) for r in self.store.db.execute('SELECT id,device_name,created,expires,revoked FROM mobile_devices WHERE account_id=? ORDER BY created',(account_id,))]

    def revoke(self, account_id, device_id):
        with self.store.db:
            self.store.db.execute('UPDATE mobile_devices SET revoked=1,push_token=NULL WHERE id=? AND account_id=?',(device_id,account_id))

    def renew(self, account_id, device_id):
        token=secrets.token_urlsafe(48)
        with self.store.db:
            changed=self.store.db.execute('UPDATE mobile_devices SET token_hash=?,expires=? WHERE id=? AND account_id=? AND revoked=0 AND expires>?',
                (digest(token),time.time()+90*86400,device_id,account_id,time.time()))
            if changed.rowcount!=1:
                raise PermissionError('设备已失效。')
        return {'token':token}

    def register_push(self, account_id, device_id, token, environment):
        if not isinstance(token,str) or not re.fullmatch('[a-fA-F0-9]{32,256}',token) or environment not in ('sandbox','production'):
            raise ValueError('无效推送凭据。')
        with self.store.db:
            changed=self.store.db.execute('UPDATE mobile_devices SET push_token=?,push_environment=? WHERE id=? AND account_id=? AND revoked=0',
                                 (token,environment,device_id,account_id))
            if changed.rowcount != 1:
                raise PermissionError('设备不存在或已撤销。')
            self.store.db.execute('INSERT OR IGNORE INTO mobile_push_subscriptions SELECT ?,COALESCE(MAX(sequence),0) FROM session_events', (device_id,))
