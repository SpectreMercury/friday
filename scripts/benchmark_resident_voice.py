"""Offline local speech/chat benchmark; no phone calls. Stop the bot before running."""
import asyncio
import base64
import contextlib
import json
import sys
import time
import wave
from pathlib import Path
import aiohttp
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from local_ai.engine import Engine,ROOT
from local_ai.voice_runtime import LocalVoiceSession
from local_ai.voice_language import response_language
from local_ai.voice_audio import TurnDetector

ROOT_TEST=ROOT/'runtime/resident-voice-test'

async def synthesize(speech,text,language,name,interrupt=False):
    start=time.monotonic();first=None;chunks=[];timestamps=[];done={};rate=24000
    async with contextlib.aclosing(speech.events('tts',text=text,language=language)) as events:
        async for event in events:
            if event['type']=='error':raise RuntimeError(event['error'])
            if event['type']=='audio':
                stamp=time.monotonic()-start
                if first is None:first=stamp
                timestamps.append(stamp);rate=event['sample_rate']
                chunks.append(base64.b64decode(event['pcm16']))
                if interrupt:
                    await speech.interrupt()
            if event['type']=='done':done=event
    elapsed=time.monotonic()-start
    path=ROOT_TEST/(name+'.wav')
    with wave.open(str(path),'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(rate);wav.writeframes(b''.join(chunks))
    duration=sum(len(c) for c in chunks)/2/rate
    # Largest delay beyond available audio after first packet (zero means continuous playback is feasible).
    available=0;gap=0
    for stamp,chunk in zip(timestamps,chunks):
        gap=max(gap,stamp-(first or 0)-available)
        available+=len(chunk)/2/rate
    return {'language':language,'text':text,'file':str(path),'first_audio_seconds':first,
            'total_seconds':elapsed,'audio_seconds':duration,'compute_per_audio_second':elapsed/duration if duration else None,
            'max_playback_gap_seconds':gap,'chunks':len(chunks),'cancelled':done.get('cancelled'),
            'cancel_ack_after_first_seconds':elapsed-first if interrupt and first else None,
            'peak_memory_gb':done.get('peak_memory_gb')}

async def chat(text,language):
    start=time.monotonic()
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
        async with session.post('http://127.0.0.1:8085/v1/chat/completions',json={
            'model':'qwen38','messages':[
                {'role':'system','content':('Respond in colloquial Hong Kong Cantonese. ' if language=='yue' else '')+'You are an AI assistant calling a restaurant to book a table for two at 7 PM tomorrow under the name Alex. The next message is the restaurant speaking. Reply in the same language, in one short sentence, at most 20 words. Do not claim the booking is confirmed.'},
                {'role':'user','content':text}],
            'temperature':0.2,'max_tokens':100,'chat_template_kwargs':{'enable_thinking':False}}) as response:
            response.raise_for_status();body=await response.json()
    return body['choices'][0]['message']['content'],time.monotonic()-start

async def main():
    ROOT_TEST.mkdir(exist_ok=True,parents=True)
    report={'started_at':time.time(),'offline':True,'phone_transport':False,'samples':[]}
    try:
        async with LocalVoiceSession(Engine(),ROOT_TEST) as speech:
            report['worker']=speech.ready
            for lang,text in [('zh','你好，请问几位？'),('zh','你好，我想预订明天晚上七点两位。'),
                              ('en','Good evening. How many guests?'),('ja','こんばんは。何名様ですか。'),
                              ('yue','你好呀，請問你哋幾多位呀？')]:
                item=await synthesize(speech,text,lang,f'{lang}-{len(report["samples"])}')
                item['recognition']=await speech.transcribe(item['file'])
                report['samples'].append(item)
                print(json.dumps(item,ensure_ascii=False),flush=True)
                # Roundtrip through the telephone speech codec, then decode to the 16 kHz ASR format.
                phone=ROOT_TEST/f'{lang}-phone.wav'
                packed=ROOT_TEST/f'{lang}-mulaw.wav'
                for arguments in [
                    ['-i',item['file'],'-ar','8000','-ac','1','-c:a','pcm_mulaw',str(packed)],
                    ['-i',str(packed),'-ar','16000','-ac','1','-c:a','pcm_s16le',str(phone)]]:
                    proc=await asyncio.create_subprocess_exec('/opt/homebrew/bin/ffmpeg','-nostdin','-v','error','-y',*arguments)
                    if await proc.wait():raise RuntimeError('Phone codec conversion failed')
                item['phone_codec_recognition']=await speech.transcribe(phone)
                with wave.open(str(phone),'rb') as wav:pcm=wav.readframes(wav.getnframes())
                detector=TurnDetector();endpoints=[]
                for offset in range(0,len(pcm),640):endpoints.extend(detector.feed(pcm[offset:offset+640]))
                endpoints.extend(detector.feed(b'\0'*640*25))
                item['vad_utterances']=sum(e['type']=='utterance' for e in endpoints)
                assert item['vad_utterances']>0
                if lang in ('en','ja','yue') or len(report['samples'])==1:
                    started=time.monotonic()
                    transcript=await speech.transcribe(item['file'])
                    selected=response_language(transcript)
                    reply,seconds=await chat(transcript['text'],selected['language'])
                    spoken=await synthesize(speech,reply,lang,f'{lang}-reply')
                    report.setdefault('conversations',[]).append({'language':lang,'recognized':transcript,
                        'reply':reply,'selected_language':selected,'llm_seconds':seconds,'first_response_after_audio_end_seconds':transcript['seconds']+seconds+spoken['first_audio_seconds'],
                        'speech':spoken})
                    print(json.dumps(report['conversations'][-1],ensure_ascii=False),flush=True)
            report['interrupt']=await synthesize(speech,'你好，我正在帮你查询餐厅的预订情况，请稍等一下。','zh','interrupted',True)
            assert report['interrupt']['cancelled'] and report['interrupt']['chunks']==1
            report['after_interrupt']=await synthesize(speech,'好的。','zh','after-interrupt')
            assert report['after_interrupt']['chunks']>0
    finally:
        (ROOT_TEST/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
asyncio.run(main())
