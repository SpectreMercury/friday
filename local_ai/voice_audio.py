"""Transport-independent 16 kHz PCM turn detection and interruptible playback."""
from collections import deque
import webrtcvad


class TurnDetector:
    rate = 16000
    frame_ms = 20
    frame_bytes = 640

    def __init__(self, vad=None, silence_ms=400, max_seconds=12):
        if silence_ms < 100 or max_seconds < 1: raise ValueError('Invalid endpoint settings')
        self.vad = vad or webrtcvad.Vad(2)
        self.end_frames = (silence_ms+self.frame_ms-1)//self.frame_ms
        self.max_frames = int(max_seconds*1000/self.frame_ms)
        self.buffer = bytearray()
        self.preroll = deque(maxlen=10)
        self.reset()

    def reset(self):
        self.active = False
        self.voiced = 0
        self.quiet = 0
        self.frames = []

    def feed(self, data):
        """Accept arbitrary PCM chunk boundaries; return ordered start/utterance events."""
        if len(data)>32000: raise ValueError('Feed at most one second of PCM at a time')
        self.buffer.extend(data)
        events = []
        while len(self.buffer)>=self.frame_bytes:
            frame=bytes(self.buffer[:self.frame_bytes]);del self.buffer[:self.frame_bytes]
            speech=self.vad.is_speech(frame,self.rate)
            if not self.active:
                self.preroll.append(frame)
                self.voiced=self.voiced+1 if speech else 0
                if self.voiced>=3:
                    self.active=True;self.frames=list(self.preroll);self.preroll.clear()
                    events.append({'type':'speech_start'})
                continue
            self.frames.append(frame)
            self.quiet=0 if speech else self.quiet+1
            if self.quiet>=self.end_frames or len(self.frames)>=self.max_frames:
                events.append({'type':'utterance','pcm16':b''.join(self.frames),
                               'forced_split':len(self.frames)>=self.max_frames})
                self.reset()
        return events

    def flush(self):
        result=b''.join(self.frames) if self.active else b''
        self.reset();self.buffer.clear();self.preroll.clear()
        return result


class PlaybackBuffer:
    """Epoch invalidation drops already-buffered and late-arriving old speech."""
    def __init__(self):
        self.epoch=0
        self.audio=bytearray()
        self.sample_rate=None

    def clear(self):
        self.epoch+=1;self.audio.clear();self.sample_rate=None
        return self.epoch

    def append(self, epoch, pcm16, sample_rate):
        if epoch!=self.epoch: return False
        if sample_rate<=0 or len(pcm16)%2: raise ValueError('Invalid PCM format')
        if self.sample_rate is not None and self.sample_rate!=sample_rate:
            raise ValueError('Sample rate changed mid-utterance')
        self.sample_rate=sample_rate
        self.audio.extend(pcm16)
        return True

    def read_frame(self):
        if not self.sample_rate: return b''
        size=self.sample_rate*2//50  # 20 ms, mono signed PCM16
        if len(self.audio)<size: return b''
        frame=bytes(self.audio[:size]);del self.audio[:size]
        return frame

    def finish(self):
        tail=bytes(self.audio);self.audio.clear()
        return tail
