// The API callback owns same-origin cookie authentication and CSRF headers.
// Audio and drafts live only in memory. Browsing another workspace never rebinds a call.
const MAX_MEMO_BYTES = 20 * 1024 * 1024;
const MAX_UPLOAD_PENDING = 16000;
const constraints = {audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true}, video: false};
const segment = value => encodeURIComponent(String(value));
const stopTracks = stream => stream?.getTracks().forEach(track => track.stop());
const message = error => error?.name === 'NotAllowedError' ? '请在网站设置中允许使用麦克风，再重新开始。' : (error?.message || '语音暂时无法连接，请重新开始。');

export class MicrophoneLease {
  owner = null;
  claim(owner) { if (this.owner !== null) return false; this.owner = owner; return true; }
  release(owner) { if (this.owner === owner) this.owner = null; }
}
const microphone = new MicrophoneLease();

export class VoicePacketGate {
  constructor(sessionID, epoch = 0) { this.sessionID = sessionID; this.epoch = epoch; this.minimumEpoch = epoch; this.packetEpoch = -1; }
  interrupt() { this.minimumEpoch = this.epoch + 1; this.packetEpoch = -1; }
  accept(event) {
    // Every server control event has both an owner and epoch, including ready.
    if (event?.type === 'audio_format') this.packetEpoch = -1;
    if (event?.session_id !== this.sessionID || !Number.isSafeInteger(event.epoch) || event.epoch < this.epoch) return false;
    this.epoch = event.epoch;
    if (event.type === 'audio_format') this.packetEpoch = event.epoch;
    return true;
  }
  takePacket() {
    const allowed = this.packetEpoch === this.epoch && this.packetEpoch >= this.minimumEpoch;
    this.packetEpoch = -1; // Each format header authorizes exactly one binary frame.
    return allowed;
  }
}

export function voiceSocketURL(path, location) {
  const base = new URL(location.href);
  const url = new URL(path, base);
  if (url.origin !== base.origin || !/^\/v1\/voice\/[^/]+\/stream$/.test(url.pathname) || url.search || url.hash || url.username || url.password) {
    throw new Error('语音暂时无法连接，请刷新页面后重试。');
  }
  url.protocol = base.protocol === 'https:' ? 'wss:' : 'ws:';
  if (!['https:', 'http:'].includes(base.protocol)) throw new Error('无法安全连接语音，请从应用首页重新打开。');
  return url.href;
}

export class VoiceController {
  constructor({api, onChange = () => {}, onEvent = () => {}, env = globalThis, mic = microphone}) {
    this.api = api; this.onChange = onChange; this.onEvent = onEvent; this.env = env; this.mic = mic;
    this.current = null; this.ending = null; this.error = null; this.transcript = '';
    this.pagehide = () => { void this.stop(); };
    env.addEventListener?.('pagehide', this.pagehide);
  }
  get snapshot() {
    const call = this.current;
    return {sessionID: call?.session.id ?? null, sessionName: call?.session.name ?? '', state: call?.state ?? '尚未通话',
      isStarting: !!call?.starting, isActive: !!call, transcript: this.transcript, error: this.error,
      supported: !!(this.env.navigator?.mediaDevices?.getUserMedia && (this.env.AudioContext || this.env.webkitAudioContext) && this.env.AudioWorkletNode)};
  }
  emit() { this.onChange(this.snapshot); }
  owns(call) { return this.current === call; }
  async start(session) {
    if (this.current || this.ending) { this.error = '请先结束当前通话，再开始新的通话。'; this.emit(); return; }
    if (!session?.id) return;
    const call = {session: {id: session.id, name: session.name}, starting: true, state: '等待麦克风权限…', playbackGeneration: 0, disposers: []};
    if (!this.mic.claim(call)) { this.error = '正在录制语音消息，请先停止录音。'; this.emit(); return; }
    this.current = call; this.error = null; this.transcript = ''; this.emit();
    try {
      if (!this.snapshot.supported) throw new Error('当前浏览器不支持实时语音，请使用新版 Safari 或发送文字。');
      // Resume while this start still belongs to a user gesture.
      const Context = this.env.AudioContext || this.env.webkitAudioContext;
      call.context = new Context({latencyHint: 'interactive'});
      // Handle rejection immediately: permission may remain pending after stop().
      const resumed = call.context.resume().then(() => null, error => error);
      const stream = await this.env.navigator.mediaDevices.getUserMedia(constraints);
      if (!this.owns(call)) { stopTracks(stream); return; }
      call.stream = stream;
      const resumeError = await resumed;
      if (resumeError) throw resumeError;
      if (!this.owns(call)) return;
      await call.context.audioWorklet.addModule('/pwa/pcm-worklet.js');
      if (!this.owns(call)) return;
      call.node = new this.env.AudioWorkletNode(call.context, 'harness-pcm', {numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1]});
      call.source = call.context.createMediaStreamSource(stream);
      call.source.connect(call.node); call.node.connect(call.context.destination);
      call.node.port.onmessage = ({data}) => {
        if (!this.owns(call)) return;
        if (data.type === 'overflow') { void this.fail(call, new Error('音频处理出现延迟，通话已结束。请重新开始。')); return; }
        if (data.type !== 'pcm') return;
        try {
          if (call.socket?.readyState !== 1 || call.socket.bufferedAmount + data.bytes.byteLength > MAX_UPLOAD_PENDING) {
            throw new Error('语音发送不畅，通话已结束。请检查网络后重新开始。');
          }
          call.socket.send(data.bytes);
          call.node.port.postMessage({type: 'ack', id: data.id});
        } catch (error) { void this.fail(call, error); }
      };
      call.node.onprocessorerror = () => { void this.fail(call, new Error('音频处理已停止，请重新开始通话。')); };
      call.context.onstatechange = () => {
        if (this.owns(call) && !call.starting && ['interrupted', 'suspended'].includes(call.context.state)) {
          void this.fail(call, new Error('通话被系统暂停。请返回页面重新开始，已提交的任务会继续。'));
        }
      };
      for (const track of stream.getTracks()) {
        const ended = () => { void this.fail(call, new Error('麦克风已断开。请检查麦克风后重新开始通话，已提交的任务会继续。')); };
        track.addEventListener('ended', ended); call.disposers.push(() => track.removeEventListener('ended', ended));
      }
      call.state = '正在准备语音…'; this.emit();
      const lease = await this.api(`/v1/sessions/${segment(call.session.id)}/voice`, {method: 'POST'});
      if (!this.owns(call)) { await this.endLease(lease); return; }
      call.lease = lease;
      if (lease.session_id !== call.session.id || lease.sample_rate !== 16000 || (lease.input_format && (lease.input_format.encoding !== 'pcm_s16le' || lease.input_format.channels !== 1))) {
        throw new Error('语音暂时无法使用，请刷新页面后重试。');
      }
      call.gate = new VoicePacketGate(call.session.id, lease.epoch ?? 0);
      call.state = '正在连接…'; this.emit();
      const url = voiceSocketURL(lease.stream_path || `/v1/voice/${segment(lease.id)}/stream`, this.env.location);
      const socket = new this.env.WebSocket(url); call.socket = socket; socket.binaryType = 'arraybuffer';
      await new Promise((resolve, reject) => {
        call.ready = resolve; call.rejectReady = reject;
        call.connectTimer = this.env.setTimeout(() => reject(new Error('语音连接超时，请检查网络后重新开始。')), 15000);
        socket.onmessage = event => {
          if (!this.owns(call)) return;
          try { this.receive(call, event.data); } catch (error) { void this.fail(call, error); }
        };
        socket.onerror = () => { void this.fail(call, new Error('语音连接中断，请检查网络后重新开始。')); };
        socket.onclose = () => { void this.fail(call, new Error('通话已结束，可以重新开始。已提交的任务会在原会话继续。')); };
      });
      if (!this.owns(call)) return;
      call.starting = false; this.emit();
    } catch (error) { if (this.owns(call)) await this.fail(call, error); }
  }
  receive(call, payload) {
    if (!this.owns(call)) return;
    if (typeof payload !== 'string') {
      if (!call.gate.takePacket()) return;
      if (!(payload instanceof ArrayBuffer) || !payload.byteLength || payload.byteLength % 2 || payload.byteLength > 192000 || !call.outputRate) {
        throw new Error('收到的语音无法播放，请重新开始通话。');
      }
      call.node.port.postMessage({type: 'play', bytes: payload, rate: call.outputRate, generation: call.playbackGeneration}, [payload]);
      return;
    }
    if (payload.length > 65536) throw new Error('语音连接异常，请重新开始通话。');
    const event = JSON.parse(payload);
    if (!call.gate.accept(event)) return;
    if (event.type === 'ready') {
      if (call.context.state !== 'running') throw new Error('浏览器暂停了音频，请重新点击开始通话。');
      call.state = '正在听'; call.node.port.postMessage({type: 'start'});
      this.env.clearTimeout(call.connectTimer); call.ready?.(); call.ready = null; call.rejectReady = null;
    } else if (event.type === 'audio_format') {
      if (event.encoding !== 'pcm_s16le' || event.channels !== 1 || !Number.isInteger(event.sample_rate) || event.sample_rate < 8000 || event.sample_rate > 96000) throw new Error('收到的语音无法播放，请重新开始通话。');
      call.outputRate = event.sample_rate;
    } else if (event.type === 'clear_playback') this.clearPlayback(call);
    else if (event.type === 'recognized') this.transcript = event.text || '';
    else if (event.type === 'state') call.state = ({listening: '正在听', thinking: '正在思考', speaking: '正在回答'})[event.state] || '正在处理任务…';
    else if (event.type === 'error') throw new Error(event.message || '语音连接异常，请重新开始通话。');
    this.onEvent(event); this.emit();
  }
  clearPlayback(call) { call.playbackGeneration++; call.node?.port.postMessage({type: 'clear', generation: call.playbackGeneration}); }
  interrupt() {
    const call = this.current;
    if (!call?.gate || call.socket?.readyState !== 1) return;
    this.clearPlayback(call); call.gate.interrupt(); call.state = '正在听'; this.emit();
    try { call.socket.send(JSON.stringify({type: 'interrupt', epoch: call.gate.epoch})); } catch (error) { void this.fail(call, error); }
  }
  async switchSession(session) { await this.stop(); return this.start(session); }
  async fail(call, error) { if (!this.owns(call)) return; this.error = message(error); await this.stop(); }
  async endLease(lease) {
    if (!lease?.id) return;
    const controller = new this.env.AbortController(); let timer;
    try {
      const expired = new Promise(resolve => { timer = this.env.setTimeout(() => { controller.abort(); resolve(); }, 8000); });
      await Promise.race([this.api(`/v1/voice/${segment(lease.id)}`, {method: 'DELETE', signal: controller.signal}), expired]);
    } catch { /* Disconnect/expiry also releases the server lease. */ }
    finally { this.env.clearTimeout(timer); }
  }
  async stop() {
    const call = this.current;
    if (!call) return this.ending;
    this.current = null; // Reject all already queued callbacks before touching audio.
    this.env.clearTimeout(call.connectTimer); call.rejectReady?.(new Error('通话已停止。'));
    for (const dispose of call.disposers) dispose();
    if (call.socket) { call.socket.onmessage = call.socket.onerror = call.socket.onclose = null; call.socket.close(); }
    if (call.node) { call.node.port.onmessage = null; call.node.onprocessorerror = null; call.node.port.postMessage({type: 'stop'}); call.node.disconnect(); }
    call.source?.disconnect(); stopTracks(call.stream); this.mic.release(call);
    if (call.context) call.context.onstatechange = null;
    const closing = call.context?.close().catch(() => {});
    this.emit();
    const ending = Promise.all([closing, this.endLease(call.lease)]);
    this.ending = ending;
    try { await ending; } finally { if (this.ending === ending) this.ending = null; }
  }
  async destroy() { this.env.removeEventListener?.('pagehide', this.pagehide); await this.stop(); }
}

export function recordingMimeType(Recorder) {
  if (!Recorder?.isTypeSupported) return null;
  return ['audio/mp4;codecs=mp4a.40.2', 'audio/mp4'].find(type => Recorder.isTypeSupported(type)) || null;
}

export class VoiceMessageRecorder {
  constructor({api, onChange = () => {}, env = globalThis, mic = microphone}) {
    this.api = api; this.onChange = onChange; this.env = env; this.mic = mic;
    this.current = null; this.error = null;
    this.pagehide = () => this.discard();
    this.hidden = () => { if (env.document?.hidden && ['recording', 'requestingPermission', 'stopping'].includes(this.current?.state)) { this.discard(); this.error = '页面进入后台，录音已停止并清除，请重新录制。'; this.emit(); } };
    env.addEventListener?.('pagehide', this.pagehide); env.document?.addEventListener('visibilitychange', this.hidden);
  }
  get snapshot() {
    const memo = this.current;
    return {sessionID: memo?.session.id ?? null, sessionName: memo?.session.name ?? '', state: memo?.state ?? 'idle',
      elapsed: memo?.elapsed ?? 0, previewURL: memo?.previewURL ?? null, draftReady: !!memo?.blob, error: this.error,
      supported: !!(this.env.navigator?.mediaDevices?.getUserMedia && recordingMimeType(this.env.MediaRecorder))};
  }
  emit() { this.onChange(this.snapshot); }
  async start(session) {
    if (!session?.id || ['recording', 'requestingPermission', 'stopping', 'uploading'].includes(this.current?.state)) return;
    this.discard();
    const memo = {session: {id: session.id, name: session.name}, state: 'requestingPermission', elapsed: 0, chunks: [], size: 0};
    if (!this.mic.claim(memo)) { this.error = '请先结束实时通话，再录制语音消息。'; this.emit(); return; }
    this.current = memo; this.error = null; this.emit();
    try {
      const mimeType = recordingMimeType(this.env.MediaRecorder);
      if (!mimeType || !this.env.navigator?.mediaDevices?.getUserMedia) throw new Error('当前浏览器无法录制语音消息，请改用新版 Safari 或发送文字。');
      const stream = await this.env.navigator.mediaDevices.getUserMedia(constraints);
      if (this.current !== memo) { stopTracks(stream); return; }
      memo.stream = stream;
      const recorder = new this.env.MediaRecorder(stream, {mimeType, audioBitsPerSecond: 64000}); memo.recorder = recorder;
      if (!/^audio\/mp4(?:;|$)/i.test(recorder.mimeType)) throw new Error('当前浏览器的录音无法发送，请改用新版 Safari 或发送文字。');
      memo.mimeType = recorder.mimeType;
      memo.ended = () => { if (this.current === memo && memo.state === 'recording') this.fail(memo, new Error('麦克风已断开，录音已清除。请检查麦克风后重新录制。')); };
      for (const track of stream.getTracks()) track.addEventListener('ended', memo.ended);
      recorder.ondataavailable = ({data}) => {
        if (this.current !== memo || !data.size) return;
        if (data.type && !/^audio\/mp4(?:;|$)/i.test(data.type)) { this.fail(memo, new Error('录音格式发生变化，请重新录制。')); return; }
        memo.size += data.size;
        if (memo.size > MAX_MEMO_BYTES) { this.fail(memo, new Error('录音超过 20 MB，已停止并清除。请缩短录音后重试。')); return; }
        memo.chunks.push(data);
      };
      recorder.onerror = () => this.fail(memo, new Error('未能保存录音，请重新录制。'));
      recorder.onstop = () => { void this.finish(memo); };
      memo.started = this.env.performance.now(); memo.state = 'recording'; recorder.start(1000);
      memo.timer = this.env.setInterval(() => {
        if (this.current !== memo || memo.state !== 'recording') return;
        memo.elapsed = Math.min(180, (this.env.performance.now() - memo.started) / 1000);
        if (memo.elapsed >= 180) this.stop(); else this.emit();
      }, 200);
      memo.limit = this.env.setTimeout(() => { if (this.current === memo) this.stop(); }, 180000); this.emit();
    } catch (error) { if (this.current === memo) this.fail(memo, error); }
  }
  release(memo) {
    this.env.clearInterval(memo.timer); this.env.clearTimeout(memo.limit);
    for (const track of memo.stream?.getTracks() || []) track.removeEventListener('ended', memo.ended);
    stopTracks(memo.stream); memo.stream = null; this.mic.release(memo);
  }
  stop() {
    const memo = this.current;
    if (memo?.state === 'requestingPermission') { this.discard(); return; }
    if (memo?.state !== 'recording') return;
    memo.elapsed = Math.min(180, (this.env.performance.now() - memo.started) / 1000); memo.state = 'stopping';
    try { memo.recorder.stop(); } catch (error) { this.fail(memo, error); return; }
    this.release(memo); this.emit();
  }
  async finish(memo) {
    if (this.current !== memo) return;
    this.release(memo);
    try {
      if (memo.elapsed < .2 || !memo.size || memo.size > MAX_MEMO_BYTES) throw new Error('录音过短或超过限制，请重新录制。');
      const blob = new this.env.Blob(memo.chunks, {type: memo.mimeType}); memo.chunks = [];
      const prefix = new Uint8Array(await blob.slice(0, 12).arrayBuffer());
      if (this.current !== memo) return;
      if (prefix.length < 12 || String.fromCharCode(...prefix.slice(4, 8)) !== 'ftyp') throw new Error('录音文件无法使用，请重新录制。');
      memo.blob = blob; memo.requestID = this.env.crypto.randomUUID(); memo.previewURL = this.env.URL.createObjectURL(blob); memo.state = 'ready'; this.emit();
    } catch (error) { if (this.current === memo) this.fail(memo, error); }
  }
  async send() {
    const memo = this.current;
    if (memo?.state !== 'ready' || !memo.blob) return null;
    memo.state = 'uploading'; memo.upload = new this.env.AbortController(); this.error = null; this.emit();
    try {
      const job = await this.api(`/v1/sessions/${segment(memo.session.id)}/voice-messages`, {method: 'POST', body: memo.blob,
        headers: {'Content-Type': 'audio/mp4', 'Idempotency-Key': memo.requestID}, signal: memo.upload.signal});
      if (this.current !== memo) return null;
      this.discard(); return job;
    } catch (error) {
      if (this.current !== memo) return null;
      memo.state = 'ready'; memo.upload = null; this.error = `发送未确认：${message(error)} 请先查看任务列表，也可以重试发送这段录音。`; this.emit(); return null;
    }
  }
  fail(memo, error) { if (this.current !== memo) return; this.discard(); this.error = message(error); this.emit(); }
  discard() {
    const memo = this.current; this.current = null; this.error = null;
    if (memo) {
      memo.upload?.abort();
      if (memo.recorder) { memo.recorder.ondataavailable = memo.recorder.onstop = memo.recorder.onerror = null; if (memo.recorder.state !== 'inactive') memo.recorder.stop(); }
      this.release(memo); if (memo.previewURL) this.env.URL.revokeObjectURL(memo.previewURL); memo.blob = null; memo.chunks = [];
    }
    this.emit();
  }
  destroy() { this.env.removeEventListener?.('pagehide', this.pagehide); this.env.document?.removeEventListener('visibilitychange', this.hidden); this.discard(); }
}
