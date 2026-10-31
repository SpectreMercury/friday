"""Private, same-origin PWA delivery and WebAuthn HTTP ceremonies."""
from __future__ import annotations

from collections import OrderedDict, deque
import secrets
import time
from pathlib import Path

from aiohttp import web


ROOT = Path(__file__).with_name('pwa')
PUBLIC_ASSETS = {'login.js', 'auth.js', 'style.css', 'icon.svg', 'manifest.webmanifest', 'sw.js', 'offline.html',
                 'friday-icon.png', 'friday-icon-192.png', 'friday-icon-512.png', 'friday-apple-touch-icon.png'}
PRIVATE_ASSETS = {'native.js', 'app.js', 'core.js', 'voice.js', 'pcm-worklet.js', 'message-format.js', 'media.js', 'drama.js'}
CEREMONIES = {
    '/v1/web/auth/register/options', '/v1/web/auth/register/verify',
    '/v1/web/auth/login/options', '/v1/web/auth/login/verify',
}


def check_origin(request, expected, *, required=False):
    """Trust the configured origin only, never forwarded headers or CORS alone."""
    origin = request.headers.get('Origin')
    if (required and not origin) or (origin is not None and origin != expected):
        raise PermissionError('请求来源不受信任。')
    if request.headers.get('Sec-Fetch-Site') not in (None, 'none', 'same-origin'):
        raise PermissionError('不允许跨站请求。')


class CeremonyLimiter:
    """Bounded process-local backstop; no trust in spoofable proxy IP headers."""
    def __init__(self):
        self.global_attempts = deque()
        self.clients = OrderedDict()

    def check(self, remote):
        now = time.monotonic()
        cutoff = now - 60
        while self.global_attempts and self.global_attempts[0] <= cutoff:
            self.global_attempts.popleft()
        bucket = self.clients.setdefault(remote or 'unknown', deque())
        self.clients.move_to_end(remote or 'unknown')
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= 30 or len(self.global_attempts) >= 60:
            raise web.HTTPTooManyRequests(text='验证请求过多，请稍后重试。', headers={'Retry-After': '60'})
        bucket.append(now)
        self.global_attempts.append(now)
        while len(self.clients) > 256:
            self.clients.popitem(last=False)


class PWARoutes:
    def __init__(self, browser, read_body, close_device_voice):
        self.browser, self.read_body = browser, read_body
        self.close_device_voice = close_device_voice
        self.limiter = CeremonyLimiter()

    def public(self, path):
        return path in {'/', '/login', '/app'} | CEREMONIES or path in {'/pwa/' + p for p in PUBLIC_ASSETS}

    def authenticate(self, request):
        token = request.cookies.get(self.browser.cookie_name)
        row = self.browser.authenticate(token)
        writing = request.method not in {'GET', 'HEAD', 'OPTIONS'}
        websocket = request.headers.get('Upgrade', '').lower() == 'websocket'
        check_origin(request, self.browser.origin, required=writing or websocket)
        if writing and not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''), self.browser.csrf(token)):
            raise PermissionError('页面验证已失效，请刷新后重试。')
        request['browser_token'] = token
        return row

    async def entry(self, request):
        try:
            self.authenticate(request)
            signed_in = True
        except PermissionError:
            signed_in = False
        if request.path == '/':
            raise web.HTTPSeeOther('/app' if signed_in else '/login')
        if request.path == '/app':
            if not signed_in:
                raise web.HTTPSeeOther('/login')
            return web.FileResponse(ROOT / 'app.html')
        return web.FileResponse(ROOT / 'login.html')

    async def asset(self, request):
        name = request.match_info['name']
        if name not in PUBLIC_ASSETS | PRIVATE_ASSETS:
            raise web.HTTPNotFound()
        response = web.FileResponse(ROOT / name)
        if name.endswith('.js'):
            response.content_type = 'text/javascript'
        elif name == 'manifest.webmanifest':
            response.content_type = 'application/manifest+json'
        if name == 'sw.js':
            response.headers['Service-Worker-Allowed'] = '/'
        return response

    async def ceremony(self, request):
        check_origin(request, self.browser.origin, required=True)
        if request.content_type != 'application/json':
            raise ValueError('验证请求必须使用 JSON。')
        self.limiter.check(request.remote)
        data = await self.read_body(request)
        registration = '/register/' in request.path
        if request.path.endswith('/options'):
            flow = secrets.token_urlsafe(32)
            if registration:
                result = self.browser.passkeys.begin_registration(data.get('code'), data.get('label'), flow_id=flow)
            else:
                result = self.browser.passkeys.begin_authentication(flow_id=flow)
            response = web.json_response(result)
            self.browser.set_flow(response, flow)
            return response
        flow = request.cookies.get(self.browser.flow_cookie_name)
        finish = self.browser.passkeys.finish_registration if registration else self.browser.passkeys.finish_authentication
        identity = finish(data.get('challenge_id'), data.get('credential'), flow_id=flow)
        previous = request.cookies.get(self.browser.cookie_name)
        old_device = None
        if previous:
            try:
                old_device = self.browser.authenticate(previous, touch=False)['id']
            except PermissionError:
                pass
        token = self.browser.issue(identity, previous)
        if old_device:
            await self.close_device_voice(old_device)
        response = web.json_response({'authenticated': True})
        self.browser.set_cookie(response, token)
        self.browser.clear_flow(response)
        return response

    async def session(self, request):
        token = request.get('browser_token')
        if not token:
            return web.json_response({'error': {'code': 'unauthorized', 'message': '请用通行密钥登录。'}}, status=401)
        return web.json_response(self.browser.public_session(token))

    async def logout(self, request):
        token = request.get('browser_token')
        if not token:
            raise PermissionError('请用通行密钥登录。')
        did = self.browser.revoke_token(token)
        if did:
            await self.close_device_voice(did)
        response = web.json_response({'logged_out': True})
        self.browser.clear_cookie(response)
        self.browser.clear_flow(response)
        response.headers['Clear-Site-Data'] = '"cache", "cookies", "storage"'
        return response

    def install(self, app):
        app.add_routes([web.get('/', self.entry), web.get('/login', self.entry), web.get('/app', self.entry),
                        web.get('/pwa/{name}', self.asset),
                        web.get('/v1/web/auth/session', self.session), web.post('/v1/web/auth/logout', self.logout)])
        app.add_routes([web.post(path, self.ceremony) for path in sorted(CEREMONIES)])


def pwa_security_headers(request, response, browser):
    # Google Picker has a separate, deliberately narrow policy for Google's UI.
    if request.path == '/v1/google/picker':
        return
    websocket_origin = browser.origin.replace('https://', 'wss://').replace('http://', 'ws://')
    response.headers.setdefault('Content-Security-Policy',
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob:; "
        f"connect-src 'self' {websocket_origin}; media-src 'self' blob:; worker-src 'self'; "
        "manifest-src 'self'; font-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Cross-Origin-Resource-Policy'] = 'same-origin'
    response.headers['Permissions-Policy'] = 'camera=(), geolocation=(), microphone=(self), payment=()'
