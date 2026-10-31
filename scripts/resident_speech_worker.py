"""Private JSON-lines worker. Models remain in RAM; audio never leaves this Mac."""
import base64
import contextlib
import json
import os
import queue
import subprocess
import tempfile
import wave
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.update(HF_HUB_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
OUTPUT = sys.stdout

def emit(event):
    OUTPUT.write(json.dumps(event, ensure_ascii=False)+'\n')
    OUTPUT.flush()


def main():
    # Some third-party model loaders print diagnostics. Keep the protocol clean.
    with contextlib.redirect_stdout(sys.stderr):
        import mlx.core as mx
        import numpy as np
        from mlx_audio.stt import load
        from mlx_audio.tts.utils import load_model
        started = time.monotonic()
        stt = load(str(ROOT/'models/speech/stt'))
        tts = load_model(str(ROOT/'models/speech/tts'))
        mx.eval(stt.parameters(), tts.parameters())
        loaded=time.monotonic()-started
        # Compile first-use MLX kernels before a call is connected.
        for _ in tts.generate_custom_voice(text='你好。',speaker='Serena',language='Chinese',
                stream=True,streaming_interval=0.32,max_tokens=80,verbose=False):
            pass
        stt.generate(np.zeros(16000,dtype=np.float32),language=None,max_tokens=16,verbose=False)
        emit({'type':'ready', 'load_seconds':loaded, 'warmup_seconds':time.monotonic()-started-loaded,
              'languages':['zh','en','ja','yue'], 'yue_backend':'macos-Sinji'})
        work = queue.Queue(maxsize=4)
        cancelled = set()
        guard = threading.Lock()

        def receive():
            for line in sys.stdin:
                try:
                    item = json.loads(line)
                    if item.get('op') == 'cancel':
                        with guard: cancelled.add(item['id'])
                    else:
                        work.put(item)
                except (ValueError, KeyError):
                    pass
            work.put(None)
        threading.Thread(target=receive, daemon=True).start()

        profiles = {
            'zh':('Chinese','Serena','用自然清晰的普通话简短交谈，语速适中。'),
            'en':('English','Serena','Speak clear, natural conversational English at a moderate pace.'),
            'ja':('Japanese','Ono_Anna','自然な日本語で、丁寧かつ簡潔に話してください。'),
        }
        while (request := work.get()) is not None:
            rid = request['id']; start = time.monotonic()
            generator = None
            try:
                with guard: stopped = rid in cancelled
                if stopped: continue
                if request['op'] == 'asr':
                    result = stt.generate(request['audio'], language=None, max_tokens=512, chunk_duration=15, verbose=False)
                    emit({'id':rid,'type':'transcript','text':result.text.strip(), 'language':result.language,
                          'seconds':time.monotonic()-start})
                elif request['op'] == 'tts':
                    text = request['text']
                    if not isinstance(text,str) or not text.strip() or len(text)>600:
                        raise ValueError('TTS requires 1–600 characters')
                    interval=float(request.get('interval',0.32))
                    if not 0.16 <= interval <= 2: raise ValueError('Invalid chunk interval')
                    if request['language']=='yue':
                        # Installed macOS Cantonese voice, fully local. Never disguise Mandarin as Cantonese.
                        with tempfile.TemporaryDirectory(prefix='local-voice-') as directory:
                            path=str(Path(directory)/'cantonese.wav')
                            process=subprocess.Popen(['/usr/bin/say','-v','Sinji','-o',path,
                                '--file-format=WAVE','--data-format=LEI16@24000'],stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL,stderr=sys.stderr)
                            try:
                                process.stdin.write(text.encode());process.stdin.close()
                                while process.poll() is None:
                                    with guard: stopped=rid in cancelled
                                    if stopped: break
                                    time.sleep(0.01)
                                if stopped: continue
                                if process.returncode: raise RuntimeError('Local Cantonese voice unavailable')
                                with wave.open(path,'rb') as wav:
                                    while pcm:=wav.readframes(int(24000*interval)):
                                        with guard: stopped=rid in cancelled
                                        if stopped: break
                                        emit({'id':rid,'type':'audio','pcm16':base64.b64encode(pcm).decode(),
                                              'sample_rate':24000,'seconds':time.monotonic()-start,'backend':'macos-Sinji'})
                            finally:
                                if process.poll() is None:
                                    process.terminate()
                                    try:process.wait(timeout=1)
                                    except subprocess.TimeoutExpired:process.kill();process.wait()
                        continue
                    language, speaker, instruction = profiles[request['language']]
                    speaker=request.get('speaker') or speaker
                    generator = tts.generate_custom_voice(text=text, speaker=speaker, language=language,
                        instruct=instruction, stream=True, streaming_interval=interval, max_tokens=900,
                        temperature=0.7, verbose=False)
                    for result in generator:
                        with guard: stopped = rid in cancelled
                        if stopped: break
                        samples=np.asarray(result.audio).reshape(-1)
                        pcm=(np.clip(samples,-1,1)*32767).astype('<i2').tobytes()
                        emit({'id':rid,'type':'audio','pcm16':base64.b64encode(pcm).decode(),
                              'sample_rate':result.sample_rate,'seconds':time.monotonic()-start})
                else:
                    raise ValueError('Unknown operation')
            except Exception as exc:
                emit({'id':rid,'type':'error','error':type(exc).__name__})
            finally:
                if generator is not None:
                    generator.close()
                    tts.speech_tokenizer.decoder.reset_streaming_state()
                with guard:
                    stopped = rid in cancelled
                    cancelled.discard(rid)
                emit({'id':rid,'type':'done','cancelled':stopped,'seconds':time.monotonic()-start,
                      'peak_memory_gb':mx.get_peak_memory()/1e9})
                # Release temporary computation buffers, preserve model weights.
                mx.clear_cache()

if __name__ == '__main__': main()
