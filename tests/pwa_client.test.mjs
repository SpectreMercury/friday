import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { API, ApiError, Drafts, Selection, apiPath, sessionPath, safeExternalURL } from '../local_ai/pwa/core.js';
import { authenticatePasskey, credentialOptions, serializeCredential } from '../local_ai/pwa/auth.js';

const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });

test('file and artifact downloads preserve bytes but never expose active HTML or JS Blob MIME', async () => {
  const app = await readFile(new URL('../local_ai/pwa/app.js', import.meta.url), 'utf8');
  const downloadCode = app.slice(app.indexOf('async function downloadFile('), app.indexOf('async function fileSheet('));
  assert.ok(downloadCode.includes('async function download('));
  const blobs = [], paths = [], links = [], content = '<script>parent.compromised = true</script>';
  const context = { sessionPath, state: { closed: false }, pageGeneration: 1, downloadURLs: new Set(),
    request: async path => { paths.push(path); return new Blob([content], { type: 'text/html' }); },
    URL: { createObjectURL: blob => { blobs.push(blob); return 'blob:inert-download'; }, revokeObjectURL: () => {} },
    el: () => { const link = { click() {}, remove() {} }; links.push(link); return link; },
    document: { body: { append() {} } }, setTimeout: () => {},
  };
  vm.runInNewContext(downloadCode, context);
  await context.downloadFile('A', 'untrusted.html');
  await context.downloadArtifact({ session_id: 'B', id: 'job-1', artifact_filename: 'untrusted.js' });
  assert.deepEqual(paths, ['/v1/sessions/A/files/untrusted.html', '/v1/sessions/B/tasks/job-1/artifact']);
  for (const blob of blobs) { assert.equal(blob.type, 'application/octet-stream'); assert.equal(await blob.text(), content); }
  assert.deepEqual(links.map(link => link.download), ['untrusted.html', 'untrusted.js']);
});

test('all mutations use same-origin cookies and the in-memory CSRF token, including file upload', async () => {
  const calls = [], api = new API({ fetcher: async (...args) => { calls.push(args); return json({ ok: true }); } });
  api.csrf = 'csrf-secret';
  await api.request('/v1/sessions/A/messages', { method: 'POST', body: { text: '<script>not html</script>', request_id: 'once' } });
  const file = new FormData(); file.append('file', new Blob(['private']), 'same-name.txt');
  await api.request('/v1/sessions/B/files', { method: 'POST', body: file });
  for (const [, options] of calls) {
    assert.equal(options.credentials, 'same-origin'); assert.equal(options.cache, 'no-store');
    assert.equal(options.redirect, 'error'); assert.equal(options.referrerPolicy, 'no-referrer');
    assert.equal(options.headers.get('X-CSRF-Token'), 'csrf-secret'); assert.equal(options.headers.has('Authorization'), false);
  }
  assert.equal(JSON.parse(calls[0][1].body).text, '<script>not html</script>');
  assert.equal(calls[1][1].body, file); assert.equal(calls[1][1].headers.has('Content-Type'), false);
});

test('missing CSRF fails before any mutation is sent', async () => {
  let count = 0;
  const api = new API({ fetcher: async () => { count++; return json({}); } });
  await assert.rejects(api.request('/v1/sessions', { method: 'POST', body: { name: 'A' } }), error => error instanceof ApiError && error.status === 401);
  assert.equal(count, 0); assert.equal(api.controllers.size, 0);
});

test('a revoked login aborts outstanding work and never releases a late private response', async () => {
  let resolvePrivate, pendingSignal, expired = 0;
  const api = new API({ onUnauthorized: () => expired++, fetcher: (path, options) => {
    if (path.endsWith('/messages')) { pendingSignal = options.signal; return new Promise(resolve => { resolvePrivate = resolve; }); }
    return Promise.resolve(json({ error: { message: 'revoked' } }, 401));
  } });
  api.csrf = 'before-revocation';
  const pending = api.request('/v1/sessions/A/messages');
  await assert.rejects(api.request('/v1/events'), error => error.status === 401);
  assert.equal(pendingSignal.aborted, true); assert.equal(expired, 1); assert.equal(api.csrf, '');
  resolvePrivate(json({ items: ['private'] })); await assert.rejects(pending, error => error.status === 401);
  await assert.rejects(api.request('/v1/tasks'), error => error.status === 401);
});

test('an externally aborted upload forwards its cancellation and cleans the listener', async () => {
  let observed;
  const controller = new AbortController();
  const api = new API({ fetcher: async (_path, options) => {
    observed = options.signal;
    return new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(new DOMException('Stopped', 'AbortError'))));
  } });
  api.csrf = 'csrf';
  const pending = api.request('/v1/sessions/A/voice-messages', { method: 'POST', body: new Blob(['audio']), signal: controller.signal });
  controller.abort(); await assert.rejects(pending, error => error.name === 'AbortError');
  assert.equal(observed.aborted, true); assert.equal(api.controllers.size, 0);
});

test('API addresses cannot become external or escape the authenticated API namespace', () => {
  for (const path of ['https://attacker.test/v1/data', '//attacker.test/v1/data', '/v1/../../login', '/v1/%2e%2e/login', '/v1/\\evil', '/v1/events#token']) assert.throws(() => apiPath(path));
  assert.equal(apiPath('/v1/events?after=23'), '/v1/events?after=23');
  assert.equal(sessionPath('A/B', '/files/same-name.txt'), '/v1/sessions/A%2FB/files/same-name.txt');
});

test('switching workspace cannot redirect in-flight sends or overwrite another draft', () => {
  let sequence = 0; const drafts = new Drafts(() => `request-${++sequence}`);
  drafts.set('A', 'A message'); const a = drafts.begin('A');
  drafts.set('B', 'B message'); const b = drafts.begin('B');
  assert.equal(a.sessionID, 'A'); assert.equal(b.sessionID, 'B'); assert.notEqual(a.request_id, b.request_id);
  drafts.complete('A', a.request_id); assert.equal(drafts.get('B').text, 'B message'); assert.equal(drafts.get('B').sending, true);
  drafts.failed('B', b.request_id, true); const retry = drafts.begin('B'); assert.deepEqual(retry, b);
});

test('uncertain sends retain the same body and key until acknowledged, even if input changes', () => {
  const drafts = new Drafts(() => 'same-key'); drafts.set('A', 'original'); const first = drafts.begin('A');
  assert.equal(drafts.begin('A'), null); drafts.failed('A', first.request_id, true); drafts.set('A', 'edited');
  const retry = drafts.begin('A'); assert.deepEqual(retry, first);
  drafts.complete('A', retry.request_id); assert.equal(drafts.get('A').text, '');
  drafts.clear(); assert.equal(drafts.items.size, 0);
});

test('a late workspace A response cannot paint workspace B or a later visit to A', () => {
  const selection = new Selection(); const a = selection.select('A'); const b = selection.select('B');
  assert.equal(selection.current(a), false); assert.equal(selection.current(b), true);
  const aAgain = selection.select('A'); assert.equal(selection.current(a), false); assert.equal(selection.current(aAgain), true);
});

test('authorization redirects allow only the expected HTTPS provider or same-origin Picker', () => {
  const origin = 'https://my-harness.example';
  assert.equal(safeExternalURL('/v1/google/picker?ticket=short-lived', { origin }), `${origin}/v1/google/picker?ticket=short-lived`);
  assert.equal(safeExternalURL('https://accounts.google.com/o/oauth2/v2/auth', { origin, google: true }), 'https://accounts.google.com/o/oauth2/v2/auth');
  for (const url of ['javascript:alert(1)', 'https://my-harness.example.attacker.test', 'https://user:pass@my-harness.example', 'http://my-harness.example']) assert.throws(() => safeExternalURL(url, { origin }));
  assert.throws(() => safeExternalURL('https://accounts.google.com.attacker.test', { origin, google: true }));
});

test('WebAuthn registration decodes binary fields and does not mutate server options', () => {
  const options = { challenge: 'AQID', user: { id: 'BAUG', name: 'owner', displayName: 'Owner' }, excludeCredentials: [{ type: 'public-key', id: 'BwgJ' }] };
  const converted = credentialOptions(options, true);
  assert.deepEqual([...new Uint8Array(converted.challenge)], [1, 2, 3]); assert.deepEqual([...new Uint8Array(converted.user.id)], [4, 5, 6]);
  assert.deepEqual([...new Uint8Array(converted.excludeCredentials[0].id)], [7, 8, 9]); assert.equal(options.challenge, 'AQID');
});

test('login signs the server challenge and verifies exactly that credential without persisting a token', async () => {
  const calls = [], credential = { id: 'credential-id', rawId: new Uint8Array([1, 2, 3]).buffer, type: 'public-key', response: {
    clientDataJSON: new Uint8Array([4]).buffer, authenticatorData: new Uint8Array([5]).buffer,
    signature: new Uint8Array([6]).buffer, userHandle: new Uint8Array([7]).buffer,
  }, getClientExtensionResults: () => ({}) };
  const result = await authenticatePasskey({ credentials: { get: async ({ publicKey }) => {
    assert.deepEqual([...new Uint8Array(publicKey.challenge)], [1, 2, 3]); assert.equal(publicKey.userVerification, 'required'); return credential;
  } }, fetcher: async (path, options) => {
    calls.push({ path, options, body: JSON.parse(options.body) });
    return path.endsWith('/options') ? json({ challenge_id: 'challenge-one', publicKey: { challenge: 'AQID', userVerification: 'required' } }) : json({ authenticated: true });
  } });
  assert.equal(result.authenticated, true); assert.deepEqual(calls[0].body, {});
  assert.equal(calls[1].body.challenge_id, 'challenge-one'); assert.equal(calls[1].body.credential.response.signature, 'Bg');
  for (const call of calls) { assert.equal(call.options.credentials, 'same-origin'); assert.equal(call.options.cache, 'no-store'); assert.equal(call.options.redirect, 'error'); }
  assert.throws(() => serializeCredential({ type: 'password' }));
});

test('cancelled passkey prompts never submit a verification request', async () => {
  let calls = 0;
  await assert.rejects(authenticatePasskey({ credentials: { get: async () => { throw new DOMException('cancelled', 'NotAllowedError'); } }, fetcher: async () => { calls++; return json({ challenge_id: 'unused', publicKey: { challenge: 'AQID' } }); } }), error => error.name === 'NotAllowedError');
  assert.equal(calls, 1);
});

test('service worker never caches app navigation, authenticated APIs or files', async () => {
  const source = await readFile(new URL('../local_ai/pwa/sw.js', import.meta.url), 'utf8');
  const handlers = {}, cached = [], added = [], fetched = [];
  const offline = new Response('offline shell');
  const context = { URL, Response, self: { location: { origin: 'https://harness.example' }, addEventListener: (type, callback) => { handlers[type] = callback; }, skipWaiting: async () => {}, clients: { claim: async () => {} } },
    caches: { open: async () => ({ addAll: async paths => added.push(...paths) }), match: async request => { cached.push(typeof request === 'string' ? request : new URL(request.url).pathname); return offline; }, keys: async () => [], delete: async () => {} },
    fetch: async request => { fetched.push(request.url); return new Response('network-only private content'); },
  };
  vm.runInNewContext(source, context);
  let installation; handlers.install({ waitUntil: promise => { installation = promise; } }); await installation;
  assert.deepEqual(added, ['/pwa/friday-icon-192.png', '/pwa/friday-icon-512.png', '/pwa/friday-apple-touch-icon.png', '/pwa/offline.html', '/pwa/style.css']);
  for (const path of ['/v1/sessions/A/messages', '/v1/sessions/A/files/private.txt', '/v1/sessions/A/tasks/image/artifact?preview=1', '/v1/sessions/A/files/audio.wav?preview=1', '/pwa/app.js', '/pwa/media.js']) {
    let intercepted = false; handlers.fetch({ request: { url: `https://harness.example${path}`, method: 'GET', mode: 'same-origin' }, respondWith: () => { intercepted = true; } }); assert.equal(intercepted, false);
  }
  let navigation; handlers.fetch({ request: { url: 'https://harness.example/app', method: 'GET', mode: 'navigate' }, respondWith: promise => { navigation = promise; } });
  assert.equal(await (await navigation).text(), 'network-only private content'); assert.deepEqual(cached, []); assert.equal(fetched.length, 1);
  context.fetch = async () => { throw new TypeError('offline'); };
  handlers.fetch({ request: { url: 'https://harness.example/app', method: 'GET', mode: 'navigate' }, respondWith: promise => { navigation = promise; } });
  assert.equal(await (await navigation).text(), 'offline shell');
  assert.deepEqual(cached, ['/pwa/offline.html']);
  let publicStyle;
  handlers.fetch({ request: { url: 'https://harness.example/pwa/style.css', method: 'GET', mode: 'same-origin' }, respondWith: promise => { publicStyle = promise; } });
  await publicStyle; assert.equal(cached.at(-1), '/pwa/style.css');
});

test('private UI sources contain no HTML injection sink, external scripts or browser persistence', async () => {
  for (const file of ['app.js', 'login.js', 'core.js', 'auth.js', 'media.js']) {
    const source = await readFile(new URL(`../local_ai/pwa/${file}`, import.meta.url), 'utf8');
    assert.doesNotMatch(source, /\b(?:innerHTML|outerHTML|insertAdjacentHTML|localStorage|sessionStorage|indexedDB|eval)\b/);
  }
  for (const file of ['app.html', 'login.html']) {
    const source = await readFile(new URL(`../local_ai/pwa/${file}`, import.meta.url), 'utf8');
    assert.doesNotMatch(source, /<script\b[^>]+src=["']https?:|\son[a-z]+\s*=|\sstyle\s*=/i);
    assert.match(source, /<script type="module" src="\/pwa\/\w+\.js"><\/script>/);
  }
});

// A minimal DOM boundary lets the actual app functions run without duplicating
// their interaction logic or relying on the implementation's rendered strings.
class InteractionNode extends EventTarget {
  constructor(tag, document) {
    super(); this.tagName = tag.toUpperCase(); this.document = document;
    this.children = []; this.dataset = {}; this.attributes = new Map();
    this.className = ''; this.parentNode = null; this._text = ''; this.open = false;
    this.replaceCount = 0; this.disabled = false;
  }
  get isConnected() { return this === this.document.body || !!this.parentNode?.isConnected; }
  set textContent(value) { this._text = String(value); for (const child of this.children) child.parentNode = null; this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  append(...nodes) { for (const node of nodes) { node.parentNode = this; this.children.push(node); } }
  replaceChildren(...nodes) {
    this.replaceCount++; this._text = '';
    for (const child of this.children) child.parentNode = null;
    this.children = []; this.append(...nodes);
  }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  querySelectorAll(selector) {
    const matches = node => selector.startsWith('.') ? node.className.split(/\s+/).includes(selector.slice(1)) : node.tagName === selector.toUpperCase();
    return this.children.flatMap(child => [...(matches(child) ? [child] : []), ...child.querySelectorAll(selector)]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  showModal() { this.open = true; }
  close() { this.open = false; queueMicrotask(() => this.dispatchEvent(new Event('close'))); }
  focus() { this.document.activeElement = this; }
  click() { if (!this.disabled) this.dispatchEvent(new Event('click')); }
}

async function interactionHarness() {
  const source = await readFile(new URL('../local_ai/pwa/app.js', import.meta.url), 'utf8');
  const segment = (start, end) => {
    const from = source.indexOf(start), to = source.indexOf(end, from);
    assert.ok(from >= 0 && to > from, `Actual app boundary exists: ${start}`);
    return source.slice(from, to);
  };
  const document = { createElement: tag => new InteractionNode(tag, document) };
  document.body = document.createElement('body');
  const toastNode = document.createElement('p'); document.querySelector = selector => selector === '#toast' ? toastNode : null;
  const errors = [], calls = { discarded: 0, stopped: 0, sent: 0, started: 0 };
  const context = {
    document, state: { closed: false, sessions: [{ id: 'A', name: 'Workspace A' }], jobs: new Map(), approvals: new Map(), auth: { account: {} } },
    main: document.createElement('main'), banner: document.createElement('aside'), drafts: { clear() {} }, closeSheet() {},
    mediaPreviews: { clear() {} }, downloadURLs: new Set(),
    memoState: {}, selection: { generation: 1 }, sessionName: id => `Workspace ${id}`,
    report: error => errors.push(error), refreshWorkspace: async () => {}, toast: () => {},
    recorder: { discard: () => calls.discarded++, stop: () => calls.stopped++, start: () => calls.started++, send: async () => { calls.sent++; return {}; } },
  };
  vm.runInNewContext([
    segment('function el(', 'function section('),
    segment('function hint(text)', 'function disclosure('),
    segment('let pendingConfirmation = null;', 'function replaceSession('),
    segment('function renderRecording(view)', 'async function poll('),
    segment('function clearPrivateUI()', 'function expire('),
    'globalThis.interactions = { confirmAction, confirmation, renderRecording, clearPrivateUI };',
  ].join('\n'), context);
  const view = document.createElement('div'); view.dataset.sessionId = 'A'; document.body.append(view);
  return { ...context.interactions, context, view, calls, errors, document };
}

const settleInteractions = () => new Promise(resolve => setImmediate(resolve));
const confirmSpec = { title: 'Confirm action', detail: 'A meaningful mutation', action: 'Proceed', danger: true };

test('actual confirmation accepts one click once and stale buttons cannot accept a replacement prompt', async () => {
  const ui = await interactionHarness();
  let accepted = 0;
  const first = ui.confirmAction(confirmSpec).then(value => { if (value) accepted++; return value; });
  const proceed = ui.confirmation.querySelectorAll('button')[1];
  assert.equal(ui.document.activeElement, ui.confirmation.querySelectorAll('button')[0]);
  proceed.click(); proceed.click();
  assert.equal(await first, true); assert.equal(accepted, 1); assert.equal(ui.confirmation.open, false);
  const second = ui.confirmAction(confirmSpec);
  const staleProceed = ui.confirmation.querySelectorAll('button')[1];
  const third = ui.confirmAction({ ...confirmSpec, title: 'Replacement' });
  assert.equal(await second, false);
  let resolved = false; third.then(() => { resolved = true; });
  staleProceed.click(); await settleInteractions();
  assert.equal(resolved, false); assert.equal(ui.confirmation.open, true);
  ui.confirmation.querySelectorAll('button')[0].click(); assert.equal(await third, false);
  assert.deepEqual(ui.errors, []);
});

test('Escape and private-UI clearing cancel actual confirmation without authorizing work', async () => {
  for (const cancel of ['escape', 'clear']) {
    const ui = await interactionHarness(); let mutations = 0;
    const result = ui.confirmAction(confirmSpec).then(accepted => { if (accepted) mutations++; return accepted; });
    const staleProceed = ui.confirmation.querySelectorAll('button')[1];
    if (cancel === 'escape') {
      const event = new Event('cancel', { cancelable: true }); ui.confirmation.dispatchEvent(event);
      assert.equal(event.defaultPrevented, true);
    } else {
      ui.context.state.closed = true; ui.clearPrivateUI();
    }
    staleProceed.click();
    assert.equal(await result, false); assert.equal(mutations, 0); assert.equal(ui.confirmation.open, false);
    if (cancel === 'clear') assert.equal(await ui.confirmAction(confirmSpec), false);
    assert.deepEqual(ui.errors, []);
  }
});

test('recording timer ticks retain actual button and audio nodes while updating only the clock', async () => {
  const ui = await interactionHarness();
  ui.context.memoState = { sessionID: 'A', sessionName: 'Workspace A', state: 'recording', elapsed: 1, supported: true, draftReady: false };
  ui.renderRecording(ui.view);
  const replacements = ui.view.replaceCount, stop = ui.view.querySelector('button'), clock = ui.view.querySelector('.record-timer');
  assert.equal(clock.textContent, '00:01');
  ui.context.memoState = { ...ui.context.memoState, elapsed: 65 };
  ui.renderRecording(ui.view);
  assert.equal(ui.view.replaceCount, replacements); assert.equal(ui.view.querySelector('button'), stop);
  assert.equal(ui.view.querySelector('.record-timer'), clock); assert.equal(clock.textContent, '01:05');
  stop.click(); assert.equal(ui.calls.stopped, 1);
  ui.context.memoState = { ...ui.context.memoState, state: 'ready', draftReady: true, previewURL: 'blob:recording-preview' };
  ui.renderRecording(ui.view);
  const audio = ui.view.querySelector('audio'), send = ui.view.querySelector('button'), readyReplacements = ui.view.replaceCount;
  audio.currentTime = 3.5;
  ui.context.memoState = { ...ui.context.memoState, elapsed: 66 }; ui.renderRecording(ui.view);
  assert.equal(ui.view.replaceCount, readyReplacements); assert.equal(ui.view.querySelector('audio'), audio);
  assert.equal(audio.currentTime, 3.5); assert.equal(ui.view.querySelector('button'), send);
  assert.deepEqual(ui.errors, []);
});

test('uploading with a retained draft has no immediate discard, resend or start-recording action', async () => {
  const ui = await interactionHarness();
  ui.context.memoState = { sessionID: 'A', sessionName: 'Workspace A', state: 'uploading', elapsed: 8, supported: true, draftReady: true, previewURL: 'blob:pending-upload' };
  ui.renderRecording(ui.view);
  const actions = ui.view.querySelectorAll('button');
  assert.equal(actions.length, 1, 'Only the confirmed stop-upload action is available');
  actions[0].click(); await settleInteractions();
  assert.equal(ui.calls.discarded, 0); assert.equal(ui.calls.sent, 0); assert.equal(ui.calls.started, 0);
  assert.equal(ui.confirmation.open, true);
  ui.confirmation.querySelectorAll('button')[0].click(); await settleInteractions();
  assert.equal(ui.calls.discarded, 0);
  actions[0].click(); actions[0].click(); await settleInteractions();
  const proceed = ui.confirmation.querySelectorAll('button')[1]; proceed.click(); proceed.click();
  await settleInteractions();
  assert.equal(ui.calls.discarded, 1); assert.equal(ui.calls.sent, 0); assert.equal(ui.calls.started, 0);
  assert.deepEqual(ui.errors, []);
});

test('clearing private UI while an upload confirmation is pending cannot authorize discard', async () => {
  const ui = await interactionHarness();
  ui.context.memoState = { sessionID: 'A', state: 'uploading', supported: true, draftReady: true };
  ui.renderRecording(ui.view); ui.view.querySelector('button').click();
  const staleProceed = ui.confirmation.querySelectorAll('button')[1];
  ui.context.state.closed = true; ui.clearPrivateUI(); staleProceed.click(); await settleInteractions();
  assert.equal(ui.calls.discarded, 0); assert.equal(ui.calls.sent, 0); assert.deepEqual(ui.errors, []);
});
