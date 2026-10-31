"""Real-model steering and mixed workload; isolated data, no Telegram transport."""
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from local_ai.engine import Engine,ROOT
from local_ai.store import Store
from local_ai.pi_agent import PiAgent,UserTools
from local_ai.providers import Providers
from local_ai.attachments import Attachments


async def main():
    root=ROOT/'runtime/system-upgrade'/('lifecycle-'+str(int(time.time())));root.mkdir(parents=True)
    sources=list((ROOT/'local_ai').glob('*.py'))+[ROOT/'pi/runner-v2.mjs',ROOT/'config/providers.json']
    hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    (root/'source-hashes.json').write_text(json.dumps(hashes,indent=2))
    store=Store(root/'db');engine=Engine();agent=PiAgent(engine,store,lambda _:True,root)
    original=UserTools.execute;original_provider=Providers.execute;blocked=asyncio.Event();release=asyncio.Event();inserted=[];results=[];active=[]
    async def execute(self,name,args):
        result=await original(self,name,args)
        if self.job['user_id']==203 and name=='calculate' and not inserted:
            incoming=store.enqueue(203,203,'chat','不要写任何文件，只回答算出的总数，单位是件，并读取新附件，回复中带上其中的项目标题。')
            title=root/'title.txt';title.write_text('项目标题：海鸥计划')
            Attachments(store,store.get(incoming),root).add(title,'title.txt')
            inserted.append(incoming)
        return result
    async def provider_execute(self,name,args):
        result=await original_provider(self,name,args)
        if name=='api_weather':
            assert not engine.lock.locked()
            blocked.set();await release.wait()
        return result
    UserTools.execute=execute
    Providers.execute=provider_execute
    async def run(uid,prompt):
        jid=store.enqueue(uid,uid,'chat',prompt);store.update(jid,status='running')
        answer=await agent.run(store.get(jid));store.update(jid,status='succeeded',result=answer)
        return jid,answer
    try:
        slow=asyncio.create_task(run(201,'通过 api_weather 查询北纬22.54554、东经114.0683的当前温度，附来源。'))
        active.append(slow)
        await asyncio.wait_for(blocked.wait(),150)
        start=time.monotonic()
        fast=await asyncio.wait_for(run(202,'用一句话说明 TCP 提供什么。'),60)
        assert not slow.done(),'External wait must remain pending while fast task completes'
        assert store.get(fast[0])['outcome']=='completed'
        results.append({'case':'real_model_and_api_mixed_wait','passed':True,'fast_seconds':round(time.monotonic()-start,2),'answer':fast[1],
                        'method':'真实模型与天气API；在API返回后人为等待，检查另一会话可完成'})
        release.set();slow_result=await asyncio.wait_for(slow,120)
        assert store.get(slow_result[0])['outcome']=='completed'
        steering=await asyncio.wait_for(run(203,'先计算31箱、每箱24件，一共有多少件，再保存到 inventory.md。'),180)
        assert '744' in steering[1] and '海鸥计划' in steering[1]
        assert not (root/'workspaces/203/inventory.md').exists()
        assert len(store.task_facts(store.get(steering[0]))['user_requests'])==2
        assert store.get(steering[0])['outcome']=='completed'
        results.append({'case':'real_model_mid_tool_correction','passed':True,'answer':steering[1],'task':store.task_facts(store.get(steering[0]))})
        media=await asyncio.wait_for(run(204,'请生成一张蓝色背景上的白色纸船图片。'),180)
        children=[dict(r) for r in store.db.execute('SELECT * FROM jobs WHERE parent_job_id=?',(media[0],))]
        assert len(children)==1 and children[0]['kind']=='image' and children[0]['status']=='queued'
        assert store.get(media[0])['outcome']=='pending'
        results.append({'case':'real_model_media_handoff','passed':True,'answer':media[1],'outcome':'pending','child_id':children[0]['id'],
                        'method':'真实模型创建隔离的媒体子任务；未渲染、未发送'})
    except Exception as exc:
        results.append({'case':'lifecycle_error','passed':False,'error':str(exc),'type':type(exc).__name__})
    finally:
        release.set()
        for task in active:
            if not task.done():task.cancel()
        await asyncio.gather(*active,return_exceptions=True)
        UserTools.execute=original
        Providers.execute=original_provider
        await engine.stop();store.db.close()
        stable=all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest for name,digest in hashes.items())
        (root/'source-stable.json').write_text(json.dumps({'unchanged':stable}))
        (root/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
        print(json.dumps(results,ensure_ascii=False,indent=2),flush=True)
        print('RESULTS '+str(root/'results.json'),flush=True)
    return stable and len(results)==3 and all(r['passed'] for r in results)
if __name__=='__main__':sys.exit(0 if asyncio.run(main()) else 1)
