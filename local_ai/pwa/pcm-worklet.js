// Resampling and PCM conversion run on the audio rendering thread, never the UI thread.
// Acknowledged messages bound capture memory even if the page's main thread stalls.
class HarnessPCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.capturing = false; this.sequence = 0; this.pending = new Set();
    this.capture = new Int16Array(320); this.capturePosition = 0;
    this.weight = 0; this.sum = 0; this.ratio = sampleRate / 16000;
    this.queue = []; this.position = 0; this.generation = 0; this.failed = false;
    this.port.onmessage = ({data}) => {
      if (data.type === 'start') this.capturing = true;
      else if (data.type === 'ack') this.pending.delete(data.id);
      else if (data.type === 'clear') { this.generation = data.generation; this.queue = []; this.position = 0; }
      else if (data.type === 'stop') { this.capturing = false; this.queue = []; this.pending.clear(); this.capturePosition = 0; }
      else if (data.type === 'play' && data.generation === this.generation) {
        if (!(data.bytes instanceof ArrayBuffer) || !data.bytes.byteLength || data.bytes.byteLength % 2 || data.bytes.byteLength > 192000 || !Number.isFinite(data.rate) || data.rate < 8000 || data.rate > 96000) { this.overflow(); return; }
        const duration = this.queue.reduce((total, part, index) => total + (part.samples.length - (index ? 0 : this.position)) / part.rate, 0);
        if (this.queue.length >= 64 || duration + data.bytes.byteLength / 2 / data.rate > 8) { this.overflow(); return; }
        const view = new DataView(data.bytes); const samples = new Float32Array(view.byteLength / 2);
        for (let i = 0; i < samples.length; i++) samples[i] = view.getInt16(i * 2, true) / 32768;
        this.queue.push({samples, rate: data.rate});
      }
    };
  }
  overflow() {
    if (!this.failed) this.port.postMessage({type: 'overflow'});
    this.failed = true; this.capturing = false; this.queue = [];
  }
  input(value) {
    // Fractional box filtering preserves the streaming phase across render quanta,
    // including 44.1 kHz input. Every output sample integrates one 16 kHz interval.
    let remaining = 1;
    while (remaining > 1e-9) {
      const take = Math.min(remaining, this.ratio - this.weight);
      this.sum += value * take; this.weight += take; remaining -= take;
      if (this.weight + 1e-9 >= this.ratio) {
        const sample = Math.max(-1, Math.min(1, this.sum / this.ratio));
        this.capture[this.capturePosition++] = sample < 0 ? Math.round(sample * 32768) : Math.round(sample * 32767);
        this.weight = 0; this.sum = 0;
        if (this.capturePosition === this.capture.length) {
          if (this.pending.size >= 8) { this.overflow(); return; }
          const id = ++this.sequence; this.pending.add(id);
          // PCM is explicitly little endian; no platform typed-array assumption.
          const bytes = new ArrayBuffer(this.capture.length * 2); const view = new DataView(bytes);
          for (let i = 0; i < this.capture.length; i++) view.setInt16(i * 2, this.capture[i], true);
          this.port.postMessage({type: 'pcm', id, bytes}, [bytes]); this.capturePosition = 0;
        }
      }
    }
  }
  process(inputs, outputs) {
    const channels = inputs[0] || [];
    if (this.capturing && !this.failed && channels.length) {
      for (let frame = 0; frame < channels[0].length && this.capturing; frame++) {
        let sample = 0; for (const channel of channels) sample += channel[frame] || 0;
        this.input(sample / channels.length);
      }
    }
    const output = outputs[0]?.[0];
    if (!output) return true;
    output.fill(0);
    if (this.failed) return true;
    for (let frame = 0; frame < output.length; frame++) {
      const part = this.queue[0]; if (!part) break;
      const index = Math.floor(this.position); const fraction = this.position - index;
      const first = part.samples[index] || 0;
      const second = part.samples[index + 1] ?? (this.queue[1]?.rate === part.rate ? this.queue[1].samples[0] : first) ?? first;
      output[frame] = first + (second - first) * fraction;
      this.position += part.rate / sampleRate;
      if (this.position >= part.samples.length) {
        this.position -= part.samples.length; this.queue.shift();
        if (!this.queue.length) this.position = 0;
        else if (this.queue[0].rate !== part.rate) this.position *= this.queue[0].rate / part.rate;
      }
    }
    return true;
  }
}
registerProcessor('harness-pcm', HarnessPCMProcessor);
