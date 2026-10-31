"""Short-lived PKCE handoff from a passkey-authenticated browser to Friday.

The custom URL scheme carries only an expiring one-use code, never a device
credential. The initiating app must prove possession of the random verifier.
"""
import base64
import hashlib
import re
import secrets
import time
from urllib.parse import urlencode
from aiohttp import web
from .mobile_auth import MobileAuth, digest
from .pwa_routes import CeremonyLimiter, ROOT


class NativeAuth:
    def __init__(self, store, browser=None):
        self.store = store
        self.browser = browser
        self.mobile = MobileAuth(store)
        store.db.executescript('''CREATE TABLE IF NOT EXISTS native_auth_flows (
            id TEXT PRIMARY KEY, challenge TEXT NOT NULL, state TEXT NOT NULL,
            expires REAL NOT NULL, created REAL NOT NULL, account_id TEXT, code_hash TEXT UNIQUE,
            consumed INTEGER NOT NULL DEFAULT 0);''')
        if 'browser_device_id' not in {r[1] for r in store.db.execute('PRAGMA table_info(native_auth_flows)')}:
            store.db.execute('ALTER TABLE native_auth_flows ADD COLUMN browser_device_id TEXT')
        store.db.commit()

    def start(self, challenge, state):
        if not all(isinstance(v, str) and re.fullmatch(r'[A-Za-z0-9_-]{43}', v) for v in (challenge, state)):
            raise ValueError('无效的登录请求。')
        flow = secrets.token_urlsafe(32)
        with self.store.db:
            self.store.db.execute('DELETE FROM native_auth_flows WHERE expires<?', (time.time(),))
            if self.store.db.execute('SELECT COUNT(*) FROM native_auth_flows').fetchone()[0] >= 200:
                raise PermissionError('登录请求过多，请稍后重试。')
            self.store.db.execute('INSERT INTO native_auth_flows(id,challenge,state,expires,created) VALUES (?,?,?,?,?)',
                                  (flow, challenge, state, time.time() + 300, time.time()))
        return flow

    def require(self, flow):
        if not isinstance(flow, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', flow):
            raise PermissionError('登录请求无效或已过期。')
        row = self.store.db.execute('SELECT * FROM native_auth_flows WHERE id=?', (flow,)).fetchone()
        if not row or row['consumed'] or row['expires'] <= time.time():
            raise PermissionError('登录请求无效或已过期。')
        return row

    def authorize(self, flow, account_id, authenticated_at, browser_device_id=None):
        row = self.require(flow)
        if self.browser is not None and (not browser_device_id or not self.browser.device_active(browser_device_id)):
            raise PermissionError('网页登录已失效。')
        if authenticated_at < row['created']:
            raise PermissionError('请重新验证通行密钥后连接设备。')
        account = self.store.account(account_id)
        if not account['enabled'] or not account['is_admin']:
            raise PermissionError('Friday 原生客户端目前仅供所有者使用。')
        code = secrets.token_urlsafe(32)
        with self.store.db:
            updated = self.store.db.execute('UPDATE native_auth_flows SET account_id=?,code_hash=?,expires=?,browser_device_id=? WHERE id=? AND account_id IS NULL AND consumed=0',
                                            (account_id, digest(code), time.time() + 60, browser_device_id, flow))
            if updated.rowcount != 1:
                raise PermissionError('此登录请求已确认。请返回 App。')
        return 'friday-private://auth/callback?' + urlencode({'code': code, 'state': row['state']})

    def exchange(self, code, verifier):
        if not isinstance(code, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', code) or not isinstance(verifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43,128}', verifier):
            raise PermissionError('登录验证失败。')
        row = self.store.db.execute('SELECT * FROM native_auth_flows WHERE code_hash=?', (digest(code),)).fetchone()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        if not row or row['consumed'] or row['expires'] <= time.time() or not secrets.compare_digest(challenge, row['challenge']):
            raise PermissionError('登录验证失败或请求已过期。')
        if self.browser is not None and (not row['browser_device_id'] or not self.browser.device_active(row['browser_device_id'])):
            raise PermissionError('授权来源已撤销，请重新登录。')
        account = self.store.account(row['account_id'])
        if not account['enabled'] or not account['is_admin']:
            raise PermissionError('账号不可用。')
        with self.store.db:
            updated = self.store.db.execute('UPDATE native_auth_flows SET consumed=1 WHERE id=? AND consumed=0', (row['id'],))
            if updated.rowcount != 1:
                raise PermissionError('登录请求已使用。')
        return self.mobile.pair(self.mobile.invite(account['id'], ttl=30), 'Friday · iPhone')


class NativeAuthRoutes:
    public_paths = {'/native/connect', '/v1/native/auth/start', '/v1/native/auth/exchange'}

    def __init__(self, store, browser, pwa, read_body):
        self.auth = NativeAuth(store, browser)
        self.browser, self.pwa, self.read_body = browser, pwa, read_body
        self.limiter = CeremonyLimiter()

    async def start(self, request):
        self.limiter.check(request.remote)
        data = await self.read_body(request)
        flow = self.auth.start(data.get('challenge'), data.get('state'))
        # Always ask for a fresh passkey ceremony; an existing Safari session is
        # insufficient by itself to authorize a new long-lived native credential.
        return web.json_response({'url': self.browser.origin + '/login?native=' + flow})

    async def page(self, request):
        flow = request.query.get('flow')
        self.auth.require(flow)
        try:
            self.pwa.authenticate(request)
        except PermissionError:
            raise web.HTTPSeeOther('/login?native=' + flow)
        return web.FileResponse(ROOT / 'native.html')

    async def authorize(self, request):
        if not request.get('browser_token'):
            raise PermissionError('请先验证通行密钥。')
        data = await self.read_body(request)
        return web.json_response({'url': self.auth.authorize(data.get('flow'), request['device']['account_id'], request['device']['created'], request['device']['id'])})

    async def exchange(self, request):
        self.limiter.check(request.remote)
        data = await self.read_body(request)
        return web.json_response(self.auth.exchange(data.get('code'), data.get('verifier')))

    def install(self, app):
        app.add_routes([web.get('/native/connect', self.page), web.post('/v1/native/auth/start', self.start),
                        web.post('/v1/native/auth/authorize', self.authorize), web.post('/v1/native/auth/exchange', self.exchange)])
