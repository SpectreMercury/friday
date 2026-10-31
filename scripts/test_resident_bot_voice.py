"""Real local STT -> Pi/Qwen -> TTS integration without Telegram delivery."""
import asyncio,json,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from local_ai.engine import Engine,ROOT
from local_ai.speech import Speech
from local_ai.pi_agent import PiAgent
from local_ai.store import Store

async def main():
    runtime=ROOT/'runtime/resident-voice-test'
    engine=Engine();speech=Speech(engine);store=Store(runtime/f'bot-{int(time.time())}.sqlite3')
    report={}
    try:
        start=time.monotonic()
        text=await speech.transcribe(runtime/'zh-0.wav','resident-integration-input')
        speech_pid=speech.worker.proc.pid
        report['recognized']=text;report['stt_cold_seconds']=time.monotonic()-start
        jid=store.enqueue(501,501,'chat','请用一句简短中文回复：你好，很高兴认识你。')
        store.update(jid,status='running')
        answer=await PiAgent(engine,store,lambda _:True,runtime).run(store.get(jid))
        chat_pid=engine.process.pid
        start=time.monotonic();audio=await speech.synthesize(answer,'resident-integration-reply')
        report.update(reply=answer,audio=str(audio),tts_seconds=time.monotonic()-start)
        assert speech.worker.proc.pid==speech_pid
        assert engine.process.pid==chat_pid
        start=time.monotonic();await speech.synthesize('好的。','resident-integration-second')
        report['second_tts_seconds']=time.monotonic()-start
        assert speech.worker.proc.pid==speech_pid and engine.process.pid==chat_pid
        report.update(speech_process_reused=True,chat_process_preserved=True,telegram_sent=False)
    finally:
        await engine.stop();store.db.close()
        report['speech_closed']=speech.worker.proc is None
        (runtime/'bot-integration-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(report,ensure_ascii=False),flush=True)
asyncio.run(main())
