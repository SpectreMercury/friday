"""System-browser Google Picker, authorized by expiring account/session tickets.

Only the short-lived Google access token reaches Google's browser UI. Refresh
credentials stay in Keychain. The agent never receives either token or ticket.
"""
import hashlib
import html
import json
import re
import secrets
import time
from urllib.parse import urlencode, urlsplit

from .connectors import RESOURCE_ID
from .sessions import RevisionConflict


class GooglePicker:
    def __init__(self, connectors, public_origin=None, device_authorizer=None):
        self.connectors, self.store, self.db = connectors, connectors.store, connectors.db
        self.device_authorizer = device_authorizer
        raw = public_origin or connectors.config.get('MOBILE_PUBLIC_URL') or connectors.config.get('GOOGLE_REDIRECT_URI', '')
        parsed = urlsplit(raw)
        self.origin = f'{parsed.scheme}://{parsed.netloc}' if parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password else None
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS connector_picker_tickets(
                ticket_hash TEXT PRIMARY KEY,account_id TEXT NOT NULL,session_id TEXT NOT NULL,
                connection_id TEXT NOT NULL,revision INTEGER NOT NULL,expires_at REAL NOT NULL,
                opened INTEGER NOT NULL DEFAULT 0,consumed INTEGER NOT NULL DEFAULT 0
            );
        ''')
        if 'device_id' not in {r[1] for r in self.db.execute('PRAGMA table_info(connector_picker_tickets)')}:
            self.db.execute('ALTER TABLE connector_picker_tickets ADD COLUMN device_id TEXT')
        self.db.commit()

    def _config(self):
        config = self.connectors.config
        key, app_id = config.get('GOOGLE_PICKER_API_KEY', ''), config.get('GOOGLE_PICKER_APP_ID', '')
        if not self.origin or not key or not re.fullmatch(r'[0-9]{1,20}', app_id):
            raise ValueError('请配置 Google Picker API key、Cloud project number 与 HTTPS public origin。')
        return key, app_id

    def start(self, account_id, session_id, connection_id, revision, device_id=None):
        self._config()
        session = self.store.require_session(account_id, session_id)
        self.connectors._connection(account_id, connection_id)
        if type(revision) is not int or revision != session['revision']: raise RevisionConflict('revision_conflict')
        ticket = secrets.token_urlsafe(32)
        digest, expires = hashlib.sha256(ticket.encode()).hexdigest(), time.time() + 600
        with self.db:
            self.db.execute('DELETE FROM connector_picker_tickets WHERE expires_at<?', (time.time(),))
            self.db.execute('INSERT INTO connector_picker_tickets(ticket_hash,account_id,session_id,connection_id,revision,expires_at,opened,consumed,device_id) VALUES (?,?,?,?,?,?,0,0,?)',
                (digest, account_id, session_id, connection_id, revision, expires, device_id))
        return {'url': self.origin + '/v1/google/picker?' + urlencode({'ticket': ticket}), 'expires_at': expires}

    def _ticket(self, ticket):
        if not isinstance(ticket, str) or not 20 <= len(ticket) <= 100: raise PermissionError('无效选择授权。')
        digest = hashlib.sha256(ticket.encode()).hexdigest()
        row = self.db.execute('SELECT * FROM connector_picker_tickets WHERE ticket_hash=?', (digest,)).fetchone()
        if not row or row['consumed'] or row['expires_at'] <= time.time(): raise PermissionError('选择授权已过期或已使用，请回到 App 重新选择。')
        self._authorize_state(row)
        return dict(row)

    def _authorize_state(self, row):
        if row['device_id']:
            device = self.db.execute('SELECT * FROM mobile_devices WHERE id=? AND account_id=?', (row['device_id'], row['account_id'])).fetchone()
            if not device or device['revoked'] or device['expires'] <= time.time() or (self.device_authorizer and not self.device_authorizer(row['device_id'])):
                raise PermissionError('设备登录已失效，请重新登录。')
        self.store.require_session(row['account_id'], row['session_id'])
        self.connectors._connection(row['account_id'], row['connection_id'])

    async def page(self, ticket):
        key, app_id = self._config()
        state = self._ticket(ticket)
        with self.db:
            changed = self.db.execute('UPDATE connector_picker_tickets SET opened=1 WHERE ticket_hash=? AND opened=0 AND consumed=0', (state['ticket_hash'],))
            if changed.rowcount != 1: raise PermissionError('此选择页面已经打开，请回到 App 重新选择。')
        connection = self.connectors._connection(state['account_id'], state['connection_id'])
        token_headers = await self.connectors._headers(connection)
        self._ticket(ticket)
        session = self.store.require_session(state['account_id'], state['session_id'])
        if session['revision'] != state['revision']: raise RevisionConflict('revision_conflict')
        config = json.dumps({'ticket': ticket, 'accessToken': token_headers['Authorization'].removeprefix('Bearer '),
            'apiKey': key, 'appId': app_id, 'origin': self.origin}, ensure_ascii=True)
        # Escaping < blocks script-close injection even if a provider token is malformed.
        config = config.replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
        nonce = secrets.token_urlsafe(24)
        title = html.escape(session.get('name', '当前工作空间'))
        body = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>选择 Google 文档</title>
<style>:root{color-scheme:dark}*{box-sizing:border-box}body{font:14px/1.65 -apple-system,system-ui,sans-serif;max-width:28rem;margin:0 auto;padding:24px 16px;color:#ededeb;background:#000000}header{padding-bottom:16px;margin-bottom:28px;border-bottom:1px solid #303030;font-weight:600;font-size:15px}h1{font-size:20px;line-height:1.4;margin:0 0 16px}button{font:500 13px/1.5 -apple-system,system-ui,sans-serif;min-height:36px;margin-top:12px;padding:7px 14px;border:0;border-radius:8px;background:#ededeb;color:#171717}button:disabled{opacity:.45}button:focus-visible{outline:2px solid #bbb;outline-offset:4px}p{overflow-wrap:anywhere}.muted{color:#aaa;font-size:12px}#status{font-size:13px;margin-top:16px}</style>
</head><body><header>Friday</header><h1>选择 Google 文档</h1><p>添加到「<strong>__TITLE__</strong>」</p>
<p class="muted">此会话可以读取和修改所选文档，修改会写入原件。其他会话的权限不变。</p>
<button id="open" disabled>正在加载 Google…</button><p id="status" role="status"></p>
<script nonce="__NONCE__">
const config=__CONFIG__;
// Remove the one-use ticket from browser history before loading third-party scripts.
history.replaceState(null,'','/v1/google/picker');
const button=document.getElementById('open'), status=document.getElementById('status');
let submitted=false;
async function selected(data){
 if(data.action===google.picker.Action.CANCEL){status.textContent='未选择文档，可重新打开选择器。';return;}
 if(data.action!==google.picker.Action.PICKED||submitted)return;
 submitted=true;button.disabled=true;status.textContent='正在确认权限并保存…';
 try{
  const response=await fetch('/v1/google/picker/selection',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'omit',body:JSON.stringify({ticket:config.ticket,ids:data.docs.map(doc=>doc.id)})});
  if(!response.ok){status.textContent=response.status===409?'工作空间配置已改变。请返回 App 刷新后重新选择。':'无法保存选择，请返回 App 重新开始。';return;}
  config.accessToken='';config.ticket='';status.textContent='文档已添加，可以返回会话。';button.hidden=true;
 }catch(error){status.textContent='连接中断，请返回 App 刷新资源列表确认结果。';}
}
button.addEventListener('click',()=>{
 const view=new google.picker.DocsView(google.picker.ViewId.DOCS).setMimeTypes('application/vnd.google-apps.document').setIncludeFolders(false);
 const picker=new google.picker.PickerBuilder().addView(view).enableFeature(google.picker.Feature.MULTISELECT_ENABLED)
 .setOAuthToken(config.accessToken).setDeveloperKey(config.apiKey).setAppId(config.appId)
 .setOrigin(config.origin).setCallback(selected).build();picker.setVisible(true);
});
const script=document.createElement('script');script.src='https://apis.google.com/js/api.js';
script.onload=()=>gapi.load('picker',{callback:()=>{button.disabled=false;button.textContent='选择文档';},onerror:()=>{status.textContent='Google 选择器加载失败，请检查网络。';}});
script.onerror=()=>{status.textContent='无法连接 Google，请检查网络后返回 App 重试。';};document.head.appendChild(script);
</script></body></html>'''
        body = body.replace('__TITLE__', title).replace('__NONCE__', nonce).replace('__CONFIG__', config)
        csp = ("default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'; "
               f"script-src 'nonce-{nonce}' https://apis.google.com https://www.gstatic.com; "
               "style-src 'unsafe-inline'; img-src data: https://*.google.com https://*.gstatic.com https://*.googleusercontent.com; "
               "frame-src https://docs.google.com https://drive.google.com https://accounts.google.com; "
               "connect-src 'self' https://*.googleapis.com https://apis.google.com")
        return {'html': body, 'headers': {'Cache-Control': 'no-store, private', 'Pragma': 'no-cache',
            'Referrer-Policy': 'strict-origin', 'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY', 'Content-Security-Policy': csp}}

    async def select(self, ticket, ids):
        state = self._ticket(ticket)
        if not state['opened']: raise PermissionError('请先打开 Google 文档选择器。')
        if not isinstance(ids, list) or not 1 <= len(ids) <= 100 or any(not isinstance(v, str) or not RESOURCE_ID.fullmatch(v) for v in ids):
            raise ValueError('选择的文档 ID 无效。')
        current = self.connectors.list_bindings(state['account_id'], state['session_id'])
        if current['revision'] != state['revision']: raise RevisionConflict('revision_conflict')
        connection = self.connectors._connection(state['account_id'], state['connection_id'])
        headers = await self.connectors._headers(connection)
        # Claim before remote reads so simultaneous callbacks never both bind.
        with self.db:
            changed = self.db.execute('UPDATE connector_picker_tickets SET consumed=1 WHERE ticket_hash=? AND consumed=0 AND expires_at>?', (state['ticket_hash'], time.time()))
            if changed.rowcount != 1: raise PermissionError('选择授权已使用或过期。')
        ids = sorted(set(ids))
        for resource_id in ids:
            # A callback is not proof that Google granted the app access.
            self.connectors._connection(state['account_id'], state['connection_id'])
            result = await self.connectors.request('GET', 'https://www.googleapis.com/drive/v3/files/' + resource_id,
                params={'fields': 'id,mimeType', 'supportsAllDrives': 'true'}, headers=headers)
            if result.get('id') != resource_id: raise PermissionError('Google 未确认所选文件。')
        bindings = current['bindings']
        existing = next((item for item in bindings if item['connection_id'] == state['connection_id']), None)
        if existing:
            existing['resource_ids'] = sorted(set(existing['resource_ids']) | set(ids))
        else:
            bindings.append({'connection_id': state['connection_id'], 'resource_ids': ids, 'can_create': False})
        self._authorize_state(state)
        return self.connectors.set_bindings(state['account_id'], state['session_id'], bindings, state['revision'])
