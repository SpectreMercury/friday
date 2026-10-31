import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
import {VoiceController, VoiceMessageRecorder, VoicePacketGate, MicrophoneLease, recordingMimeType, voiceSocketURL} from '../local_ai/pwa/voice.js';

const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise, resolve}; };
function environment() {
  const tracks = []; const nodes = []; const sockets = []; const contexts = []; const revoked = [];
  let now = 0;
  class Track {
    listeners = new Map(); stopped = false;
    stop() { this.stopped = true; }
    addEventListener(type, listener) { this.listeners.set(type, listener); }
    removeEventListener(type) { this.listeners.delete(type); }
  }
  const stream = () => { const track = new Track(); tracks.push(track); return {getTracks: () => [track]}; };
  class Context {
    state = 'running'; destination = {}; audioWorklet = {addModule: async path => { assert.equal(path, '/pwa/pcm-worklet.js'); }};
    constructor() { contexts.push(this); }
    async resume() {}
    async close() { this.closed = true; }
    createMediaStreamSource() { return {connect() {}, disconnect() {}}; }
  }
  class Node {
    sent = []; port = {postMessage: data => this.sent.push(data)};
    constructor() { nodes.push(this); }
    connect() {} disconnect() { this.disconnected = true; }
  }
  class Socket {
    readyState = 1; bufferedAmount = 0; sent = [];
    constructor(url) { this.url = url; sockets.push(this); }
    send(data) { this.sent.push(data); }
    close() { this.closed = true; this.readyState = 3; }
    event(data) { this.onmessage?.({data: typeof data === 'object' && !(data instanceof ArrayBuffer) ? JSON.stringify(data) : data}); }
  }
  class Recorder {
    static isTypeSupported(type) { return type.startsWith('audio/mp4'); }
    state = 'inactive';
    constructor(stream, options) { this.stream = stream; this.mimeType = options.mimeType; Recorder.last = this; }
    start() { this.state = 'recording'; }
    stop() { this.state = 'inactive'; queueMicrotask(() => { this.ondataavailable?.({data: new Blob([new Uint8Array([0,0,0,20,102,116,121,112,77,52,65,32,0,0,0,0])], {type: this.mimeType})}); this.onstop?.(); }); }
  }
  const env = {navigator: {mediaDevices: {getUserMedia: async () => stream()}}, AudioContext: Context, AudioWorkletNode: Node, WebSocket: Socket,
    MediaRecorder: Recorder, Blob, AbortController, crypto: {randomUUID: () => 'stable-request-id'}, performance: {now: () => now},
    URL: {createObjectURL: () => 'blob:test-draft', revokeObjectURL: url => revoked.push(url)}, location: {href: 'https://harness.test/pwa/'},
    setTimeout, clearTimeout, setInterval, clearInterval};
  return {env, tracks, nodes, sockets, contexts, stream, Recorder, revoked, advance: milliseconds => { now += milliseconds; }};
}

test('packet gate refuses foreign sessions, old epochs and unpaired binary audio after interrupt', () => {
  const gate = new VoicePacketGate('A', 0);
  assert.equal(gate.accept({type: 'audio_format', session_id: 'A', epoch: 0}), true);
  assert.equal(gate.takePacket(), true); assert.equal(gate.takePacket(), false);
  gate.interrupt();
  gate.accept({type: 'audio_format', session_id: 'A', epoch: 0}); assert.equal(gate.takePacket(), false);
  gate.accept({type: 'clear_playback', session_id: 'A', epoch: 1});
  gate.accept({type: 'audio_format', session_id: 'A', epoch: 1});
  assert.equal(gate.accept({type: 'audio_format', session_id: 'B', epoch: 100}), false); assert.equal(gate.takePacket(), false);
  assert.equal(gate.accept({type: 'recognized', session_id: 'B', epoch: 100}), false);
  assert.equal(gate.accept({type: 'audio_format', session_id: 'A', epoch: 0}), false);
  assert.equal(gate.accept({type: 'recognized', epoch: 1}), false);
  gate.accept({type: 'audio_format', session_id: 'A', epoch: 1}); assert.equal(gate.takePacket(), true);
});

test('WebSocket URL carries no bearer credentials and cannot escape the origin', () => {
  const location = {href: 'https://harness.test/pwa/'};
  assert.equal(voiceSocketURL('/v1/voice/abc/stream', location), 'wss://harness.test/v1/voice/abc/stream');
  for (const bad of ['https://evil.test/v1/voice/abc/stream', '//evil.test/v1/voice/abc/stream', '/v1/voice/abc/stream?token=abc', '/v1/voice/abc/stream#x', '/v1/sessions']) {
    assert.throws(() => voiceSocketURL(bad, location));
  }
});

test('late permission resolution after stop releases microphone without creating a lease', async () => {
  const fake = environment(); const permission = deferred(); const mic = new MicrophoneLease(); let requests = 0;
  fake.env.navigator.mediaDevices.getUserMedia = () => permission.promise;
  const voice = new VoiceController({env: fake.env, mic, api: async () => { requests++; }});
  const pending = voice.start({id: 'A', name: 'A'}); await voice.stop();
  permission.resolve(fake.stream()); await pending;
  assert.equal(requests, 0); assert.equal(fake.tracks[0].stopped, true); assert.equal(mic.owner, null); assert.equal(fake.contexts[0].closed, true);
});

test('late server lease is ended in its original session after start was stopped', async () => {
  const fake = environment(); const response = deferred(); const calls = [];
  const voice = new VoiceController({env: fake.env, api: async (path, options) => { calls.push([path, options.method]); return options.method === 'POST' ? response.promise : {}; }});
  const pending = voice.start({id: 'A', name: 'A'}); await tick(); await voice.stop();
  response.resolve({id: 'late', session_id: 'A', sample_rate: 16000, epoch: 0}); await pending;
  assert.deepEqual(calls, [['/v1/sessions/A/voice', 'POST'], ['/v1/voice/late', 'DELETE']]);
  assert.equal(fake.tracks[0].stopped, true); assert.equal(fake.sockets.length, 0);
});

test('live call stays pinned, interrupt only clears speech, and backpressure releases all resources', async () => {
  const fake = environment(); const calls = []; const events = [];
  const voice = new VoiceController({env: fake.env, onEvent: event => events.push(event), api: async (path, options) => {
    calls.push([path, options.method]); return {id: 'v1', session_id: 'A', sample_rate: 16000, epoch: 0};
  }});
  const session = {id: 'A', name: '工作'}; const start = voice.start(session); await tick();
  const socket = fake.sockets[0]; socket.event({type: 'ready', session_id: 'A', epoch: 0}); await start;
  session.id = 'B'; assert.equal(voice.snapshot.sessionID, 'A');
  socket.event({type: 'recognized', session_id: 'B', epoch: 0, text: 'wrong'}); assert.equal(voice.snapshot.transcript, '');
  socket.event({type: 'recognized', session_id: 'A', epoch: 0, text: 'correct'}); assert.equal(voice.snapshot.transcript, 'correct');
  voice.interrupt(); assert.deepEqual(JSON.parse(socket.sent[0]), {type: 'interrupt', epoch: 0});
  socket.event({type: 'audio_format', session_id: 'A', epoch: 0, sample_rate: 24000, channels: 1, encoding: 'pcm_s16le'}); socket.event(new ArrayBuffer(20));
  assert.equal(fake.nodes[0].sent.filter(item => item.type === 'play').length, 0);
  socket.event({type: 'clear_playback', session_id: 'A', epoch: 1});
  socket.event({type: 'audio_format', session_id: 'A', epoch: 1, sample_rate: 22050, channels: 1, encoding: 'pcm_s16le'}); socket.event(new ArrayBuffer(20));
  assert.equal(fake.nodes[0].sent.find(item => item.type === 'play').rate, 22050);
  const lateCallback = socket.onmessage;
  socket.bufferedAmount = 16000; fake.nodes[0].port.onmessage({data: {type: 'pcm', id: 1, bytes: new ArrayBuffer(640)}}); await tick();
  assert.equal(voice.snapshot.isActive, false); assert.equal(socket.closed, true); assert.equal(fake.tracks[0].stopped, true);
  assert.equal(fake.contexts[0].closed, true); assert.deepEqual(calls, [['/v1/sessions/A/voice','POST'], ['/v1/voice/v1','DELETE']]);
  lateCallback({data: JSON.stringify({type: 'recognized', session_id: 'A', epoch: 1, text: 'late'})}); assert.equal(voice.snapshot.transcript, 'correct');
});

test('explicit session switch waits for old lease cleanup, then ignores old socket callbacks', async () => {
  const fake = environment(); const deletion = deferred(); const calls = [];
  const voice = new VoiceController({env: fake.env, api: async (path, options) => {
    calls.push([path, options.method]);
    if (options.method === 'DELETE' && path.endsWith('/A')) return deletion.promise;
    const id = path.split('/')[3]; return {id, session_id: id, sample_rate: 16000, epoch: 0};
  }});
  const initial = voice.start({id: 'A', name: 'A'}); await tick();
  const old = fake.sockets[0]; old.event({type: 'ready', session_id: 'A', epoch: 0}); await initial;
  const late = old.onmessage;
  const switching = voice.switchSession({id: 'B', name: 'B'}); await tick();
  assert.equal(old.closed, true); assert.equal(fake.sockets.length, 1); assert.equal(fake.tracks[0].stopped, true);
  deletion.resolve({}); await tick();
  fake.sockets[1].event({type: 'ready', session_id: 'B', epoch: 0}); await switching;
  late({data: JSON.stringify({type: 'recognized', session_id: 'A', epoch: 10, text: 'late old speech'})});
  assert.equal(voice.snapshot.sessionID, 'B'); assert.equal(voice.snapshot.transcript, '');
  assert.deepEqual(calls.slice(0, 3), [['/v1/sessions/A/voice','POST'], ['/v1/voice/A','DELETE'], ['/v1/sessions/B/voice','POST']]);
  await voice.stop();
});

test('permission failure closes the audio context and releases the shared microphone', async () => {
  const fake = environment(); const mic = new MicrophoneLease();
  fake.env.navigator.mediaDevices.getUserMedia = async () => { throw Object.assign(new Error('denied'), {name: 'NotAllowedError'}); };
  const voice = new VoiceController({env: fake.env, mic, api: async () => assert.fail('permission denied must not contact voice endpoint')});
  await voice.start({id: 'A', name: 'A'});
  assert.match(voice.snapshot.error, /允许/); assert.equal(voice.snapshot.isActive, false); assert.equal(fake.contexts[0].closed, true); assert.equal(mic.owner, null);
});

test('memo and live voice share a single microphone lease', async () => {
  const fake = environment(); const pending = deferred(); const mic = new MicrophoneLease();
  fake.env.navigator.mediaDevices.getUserMedia = () => pending.promise;
  const voice = new VoiceController({env: fake.env, mic, api: async () => ({})});
  const recorder = new VoiceMessageRecorder({env: fake.env, mic, api: async () => ({})});
  const start = recorder.start({id: 'A', name: 'A'}); await voice.start({id: 'B', name: 'B'});
  assert.match(voice.snapshot.error, /语音消息/); assert.equal(fake.contexts.length, 0);
  recorder.discard(); pending.resolve(fake.stream()); await start;
  assert.equal(fake.tracks[0].stopped, true); assert.equal(mic.owner, null);
});

test('memo keeps destination and idempotency identity for retries, revokes its memory preview on success', async () => {
  const fake = environment(); const calls = [];
  const recorder = new VoiceMessageRecorder({env: fake.env, api: async (path, options) => {
    calls.push([path, options]); if (calls.length === 1) throw new Error('offline'); return {id: 42};
  }});
  const session = {id: 'A', name: 'A'}; await recorder.start(session); session.id = 'B'; fake.advance(1000); recorder.stop(); await tick();
  assert.equal(recorder.snapshot.state, 'ready'); assert.equal(fake.tracks[0].stopped, true); assert.equal(recorder.snapshot.previewURL, 'blob:test-draft');
  assert.equal(await recorder.send(), null); assert.equal(recorder.snapshot.state, 'ready'); assert.match(recorder.snapshot.error, /未确认/);
  assert.deepEqual(await recorder.send(), {id: 42});
  assert.equal(calls[0][0], '/v1/sessions/A/voice-messages'); assert.equal(calls[1][0], calls[0][0]);
  assert.equal(calls[0][1].headers['Idempotency-Key'], calls[1][1].headers['Idempotency-Key']);
  assert.equal(calls[0][1].headers['Content-Type'], 'audio/mp4'); assert.equal(calls[0][1].body, calls[1][1].body);
  assert.equal(recorder.snapshot.state, 'idle'); assert.deepEqual(fake.revoked, ['blob:test-draft']);
});

test('memo fails closed for unsupported encoders and limits oversized recordings', async () => {
  assert.equal(recordingMimeType({isTypeSupported: type => type === 'audio/webm'}), null);
  const fake = environment(); const recorder = new VoiceMessageRecorder({env: fake.env, api: async () => ({})});
  await recorder.start({id: 'A', name: 'A'});
  fake.Recorder.last.ondataavailable({data: {size: 20 * 1024 * 1024 + 1, type: 'audio/mp4'}});
  assert.equal(recorder.snapshot.state, 'idle'); assert.match(recorder.snapshot.error, /20 MB/); assert.equal(fake.tracks[0].stopped, true);
});

test('memo automatically stops at three minutes and retains only an in-memory draft', async () => {
  const fake = environment(); let limit;
  fake.env.setTimeout = (callback, milliseconds) => { assert.equal(milliseconds, 180000); limit = callback; return 123; };
  fake.env.clearTimeout = () => {};
  const recorder = new VoiceMessageRecorder({env: fake.env, api: async () => ({})});
  await recorder.start({id: 'A', name: 'A'}); fake.advance(180000); limit(); await tick();
  assert.equal(fake.Recorder.last.state, 'inactive'); assert.equal(fake.tracks[0].stopped, true);
  assert.equal(recorder.snapshot.state, 'ready'); assert.equal(recorder.snapshot.elapsed, 180); recorder.discard();
});

test('discard aborts uploading and ignores its late completion', async () => {
  const fake = environment(); const response = deferred(); let signal;
  const recorder = new VoiceMessageRecorder({env: fake.env, api: async (_path, options) => { signal = options.signal; return response.promise; }});
  await recorder.start({id: 'A', name: 'A'}); fake.advance(1000); recorder.stop(); await tick();
  const upload = recorder.send(); recorder.discard(); assert.equal(signal.aborted, true);
  response.resolve({id: 42}); assert.equal(await upload, null); assert.equal(recorder.snapshot.state, 'idle');
});

const workletSource = await readFile(new URL('../local_ai/pwa/pcm-worklet.js', import.meta.url), 'utf8');
function worklet(rate) {
  let Processor; const sent = [];
  const context = vm.createContext({AudioWorkletProcessor: class {port = {postMessage: event => sent.push(event)};}, sampleRate: rate,
    registerProcessor: (_name, ctor) => { Processor = ctor; }, ArrayBuffer, DataView, Float32Array, Int16Array, Set, Math, Number});
  vm.runInContext(workletSource, context); return {node: new Processor(), sent};
}
test('worklet resamples 44.1 and 48 kHz input to exactly 16 kHz little-endian PCM with bounded messages', () => {
  for (const rate of [44100, 48000]) {
    const {node, sent} = worklet(rate); node.port.onmessage({data: {type: 'start'}});
    for (let offset = 0; offset < rate; offset += 128) {
      const input = new Float32Array(Math.min(128, rate - offset)).fill(.5);
      node.process([[input]], [[new Float32Array(input.length)]]);
      for (const event of sent) if (event.type === 'pcm') node.port.onmessage({data: {type: 'ack', id: event.id}});
    }
    const frames = sent.filter(event => event.type === 'pcm'); assert.equal(frames.length, 50);
    assert.equal(frames.reduce((count, frame) => count + frame.bytes.byteLength / 2, 0), 16000);
    assert.equal(new DataView(frames[0].bytes).getInt16(0, true), 16384);
  }
  const {node, sent} = worklet(48000); node.port.onmessage({data: {type: 'start'}});
  for (let i = 0; i < 200; i++) node.process([[new Float32Array(128)]], [[new Float32Array(128)]]);
  assert.equal(sent.filter(event => event.type === 'pcm').length, 8); assert.equal(sent.filter(event => event.type === 'overflow').length, 1);
});

test('worklet clears queued speech by generation and refuses more than eight seconds of audio', () => {
  const {node, sent} = worklet(48000);
  const pcm = count => { const bytes = new ArrayBuffer(count * 2); const view = new DataView(bytes); for (let i = 0; i < count; i++) view.setInt16(i * 2, 16384, true); return bytes; };
  node.port.onmessage({data: {type: 'play', generation: 0, rate: 24000, bytes: pcm(24000)}});
  let output = new Float32Array(128); node.process([], [[output]]); assert.equal(output[0], .5);
  node.port.onmessage({data: {type: 'clear', generation: 1}});
  node.port.onmessage({data: {type: 'play', generation: 0, rate: 24000, bytes: pcm(24000)}});
  output.fill(1); node.process([], [[output]]); assert.equal(output.every(sample => sample === 0), true);
  for (let i = 0; i < 9; i++) node.port.onmessage({data: {type: 'play', generation: 1, rate: 24000, bytes: pcm(24000)}});
  assert.equal(sent.filter(event => event.type === 'overflow').length, 1);
});
