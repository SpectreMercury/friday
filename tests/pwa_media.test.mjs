import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { mediaDescriptor, conversationEntries, createMediaPreview, MediaPreviews } from '../local_ai/pwa/media.js';
import { sessionPath } from '../local_ai/pwa/core.js';

const artifact = (id = 'image', overrides = {}) => ({
  id, session_id: 'A', artifact_filename: 'picture.png', artifact_media_type: 'image/png',
  artifact_url: `/v1/sessions/A/tasks/${id}/artifact`, artifact_preview_url: `/v1/sessions/A/tasks/${id}/artifact?preview=1`, ...overrides,
});

test('preview sources require both a passive MIME and the exact endpoint in the owning session', () => {
  const item = artifact();
  assert.deepEqual(mediaDescriptor(item, 'A'), { url: item.artifact_preview_url, mime: 'image/png', kind: 'image', name: 'picture.png', sessionID: 'A' });
  assert.equal(mediaDescriptor(item, 'B'), null);
  for (const mime of ['image/svg+xml', 'text/html', 'application/javascript', 'application/octet-stream', 'image/png; charset=utf-8']) {
    assert.equal(mediaDescriptor({ ...item, artifact_media_type: mime }, 'A'), null);
  }
  for (const url of ['https://evil.test/image.png', '//evil.test/image.png', '/v1/sessions/B/tasks/image/artifact?preview=1', '/v1/sessions/A/tasks/image/artifact', `${item.artifact_preview_url}&next=evil`, '/v1/sessions/A/files/picture.png?preview=1']) {
    assert.equal(mediaDescriptor({ ...item, artifact_preview_url: url }, 'A'), null);
  }
  assert.equal(mediaDescriptor({ ...item, artifact_media_type: null }, 'A'), null, 'An extension never authorizes inline rendering');
  assert.equal(mediaDescriptor({ name: 'same name.png', media_type: 'image/png', preview_url: '/v1/sessions/A/files/same%20name.png?preview=1' }, 'A', { file: true }).kind, 'image');
  assert.equal(mediaDescriptor({ name: "Friday's (1).png", media_type: 'image/png', preview_url: '/v1/sessions/A/files/Friday%27s%20%281%29.png?preview=1' }, 'A', { file: true }).kind, 'image');
});

test('nested child artifacts appear under their original turn without child prompts or another session', () => {
  const root = { id: 'root', session_id: 'A', prompt: 'Make a film', status: 'running' };
  const child = artifact('child', { parent_job_id: 'root', prompt: 'Internal generator prompt' });
  const nested = artifact('nested', { parent_job_id: 'child', artifact_media_type: 'video/mp4' });
  const stranger = artifact('stranger', { parent_job_id: 'root', session_id: 'B' });
  const entries = conversationEntries([root, child, nested, stranger], 'A');
  assert.equal(entries.length, 1); assert.equal(entries[0].job, root);
  assert.deepEqual(entries[0].artifacts.map(item => item.id), ['child', 'nested']);
  const orphan = conversationEntries([child, nested], 'A');
  assert.equal(orphan.length, 1); assert.equal(orphan[0].job, null);
  assert.deepEqual(orphan[0].artifacts.map(item => item.id), ['child', 'nested']);
});

test('ordinary files remain downloadable and orphan/cyclic parents cannot trap conversation rendering', () => {
  const plain = artifact('document', { artifact_filename: 'notes.html', artifact_media_type: null, artifact_preview_url: null });
  assert.equal(conversationEntries([plain], 'A')[0].artifacts[0], plain);
  const cyclic = conversationEntries([artifact('a', { parent_job_id: 'b' }), artifact('b', { parent_job_id: 'a' })], 'A');
  assert.equal(cyclic.flatMap(item => item.artifacts).length, 2);
  assert.ok(cyclic.every(item => item.job === null));
});

class Node extends EventTarget {
  constructor(tag, document) { super(); this.tagName = tag.toUpperCase(); this.document = document; this.children = []; this.parentNode = null; this.dataset = {}; this.attributes = new Map(); this.pauses = 0; this.loads = 0; this.hidden = false; }
  get isConnected() { return this === this.document.body || !!this.parentNode?.isConnected; }
  append(...nodes) { for (const node of nodes) { node.remove(); node.parentNode = this; this.children.push(node); } }
  remove() { if (this.parentNode) this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1); this.parentNode = null; }
  contains(node) { return this === node || this.children.some(child => child.contains(node)); }
  setAttribute(name, value) { this.attributes.set(name, value); }
  removeAttribute(name) { this.attributes.delete(name); if (name === 'src') this.src = ''; }
  pause() { this.pauses++; }
  load() { this.loads++; }
  query(tag) { return this.children.find(child => child.tagName === tag.toUpperCase()) || this.children.map(child => child.query(tag)).find(Boolean); }
}
function documentFixture() { const document = { createElement: tag => new Node(tag, document) }; document.body = document.createElement('body'); return document; }

test('images show loading, recover from an error, and ignore late events from a replaced request', () => {
  const document = documentFixture(), view = createMediaPreview(mediaDescriptor(artifact(), 'A'), { document });
  document.body.append(view.node);
  const stage = view.node.children[0], overlay = stage.children[0], first = view.node.query('img');
  assert.equal(stage.dataset.state, 'loading'); assert.equal(first.src, '/v1/sessions/A/tasks/image/artifact?preview=1');
  assert.equal(first.loading, 'lazy'); assert.equal(first.alt, 'picture.png');
  first.dispatchEvent(new Event('error'));
  const retry = view.node.query('button'); assert.equal(stage.dataset.state, 'error'); assert.equal(retry.hidden, false);
  retry.dispatchEvent(new Event('click'));
  const second = view.node.query('img'); assert.notEqual(second, first); assert.equal(first.src, ''); assert.equal(stage.dataset.state, 'loading');
  first.dispatchEvent(new Event('load')); assert.equal(stage.dataset.state, 'loading');
  second.dispatchEvent(new Event('load')); assert.equal(stage.dataset.state, 'ready'); assert.equal(overlay.hidden, true);
});

test('video/audio use native streaming controls, stop loading on disposal, and cannot update another workspace', () => {
  for (const [mime, kind] of [['video/mp4', 'video'], ['audio/wav', 'audio']]) {
    const document = documentFixture(); let active = true, ready = 0;
    const view = createMediaPreview(mediaDescriptor(artifact('clip', { artifact_media_type: mime }), 'A'), { document, active: () => active, onReady: () => ready++ });
    document.body.append(view.node); const media = view.node.query(kind), stage = view.node.children[0];
    assert.equal(media.controls, true); assert.equal(media.preload, 'metadata'); assert.equal(media.src, '/v1/sessions/A/tasks/clip/artifact?preview=1');
    assert.ok(!media.autoplay); if (kind === 'video') assert.equal(media.playsInline, true);
    media.dispatchEvent(new Event('loadedmetadata')); assert.equal(ready, 1);
    active = false; media.dispatchEvent(new Event('error')); assert.equal(stage.dataset.state, 'ready');
    view.dispose(); assert.equal(media.pauses, 1); assert.equal(media.loads, 1); assert.equal(media.src, '');
    media.dispatchEvent(new Event('loadedmetadata')); assert.equal(ready, 1);
  }
});

test('a detached media event cannot reveal content and cached views retain playback until their container closes', () => {
  const document = documentFixture(), manager = new MediaPreviews({ document });
  const descriptor = mediaDescriptor(artifact('movie', { artifact_media_type: 'video/mp4' }), 'A');
  const first = manager.get('conversation', descriptor); first.query('video').dispatchEvent(new Event('loadedmetadata'));
  assert.equal(first.children[0].dataset.state, 'loading');
  document.body.append(first); const video = first.query('video'); video.currentTime = 4;
  assert.equal(manager.get('conversation', descriptor), first); assert.equal(video.currentTime, 4);
  const sheet = document.createElement('dialog'); document.body.append(sheet);
  const second = manager.get('files', descriptor); sheet.append(second); assert.notEqual(first, second);
  manager.releaseWithin(sheet); assert.equal(second.query('video'), undefined); assert.equal(video.pauses, 0);
  first.remove(); manager.prune(); assert.equal(video.pauses, 1); assert.equal(manager.items.size, 0);
});

test('publishing a new artifact version replaces the same task player and releases the old stream', () => {
  const document = documentFixture(); let ready = 0;
  const manager = new MediaPreviews({ document, onReady: () => ready++ });
  const descriptor = version => mediaDescriptor(artifact('movie', { artifact_media_type: 'video/mp4', artifact_version: version }), 'A');
  const first = manager.get('conversation', descriptor('first'));
  document.body.append(first); const oldVideo = first.query('video'); oldVideo.currentTime = 9;
  assert.equal(oldVideo.src, '/v1/sessions/A/tasks/movie/artifact?preview=1&version=first');
  assert.equal(manager.get('conversation', descriptor('first')), first);
  assert.equal(oldVideo.currentTime, 9, 'Unchanged polling preserves playback');
  const second = manager.get('conversation', descriptor('second'));
  document.body.append(second);
  assert.notEqual(second, first); assert.equal(manager.items.size, 1);
  assert.equal(oldVideo.pauses, 1); assert.equal(oldVideo.loads, 1); assert.equal(oldVideo.src, '');
  assert.equal(first.query('video'), undefined);
  oldVideo.dispatchEvent(new Event('loadedmetadata')); assert.equal(ready, 0);
  const newVideo = second.query('video');
  assert.equal(newVideo.src, '/v1/sessions/A/tasks/movie/artifact?preview=1&version=second');
  newVideo.dispatchEvent(new Event('loadedmetadata')); assert.equal(ready, 1);
  assert.equal(second.children[0].dataset.state, 'ready');
});

test('artifact versions are encoded after endpoint validation and never authorize a different source', () => {
  const document = documentFixture();
  const item = artifact('movie', { artifact_media_type: 'video/mp4', artifact_version: 'next&redirect=https://evil.test/#' });
  const view = createMediaPreview(mediaDescriptor(item, 'A'), { document });
  assert.equal(view.node.query('video').src, '/v1/sessions/A/tasks/movie/artifact?preview=1&version=next%26redirect%3Dhttps%3A%2F%2Fevil.test%2F%23');
  assert.equal(mediaDescriptor({ ...item, artifact_preview_url: item.artifact_preview_url + '&version=next' }, 'A'), null);
});

test('downloads arriving after a workspace change never create a private Blob URL', async () => {
  const source = await readFile(new URL('../local_ai/pwa/app.js', import.meta.url), 'utf8');
  const code = source.slice(source.indexOf('async function downloadFile('), source.indexOf('async function fileSheet('));
  let finish, created = 0;
  const context = { sessionPath, state: { closed: false }, pageGeneration: 1, downloadURLs: new Set(), request: () => new Promise(resolve => { finish = resolve; }), URL: { createObjectURL() { created++; } } };
  vm.runInNewContext(code, context);
  const pending = context.downloadFile('A', 'private.png'); context.pageGeneration++;
  finish(new Blob(['private'])); await pending; assert.equal(created, 0);
});
