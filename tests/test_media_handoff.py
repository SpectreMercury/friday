"""Real Pi loop: a queue acknowledgement can skip inference, never review."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from local_ai.engine import ROOT
from local_ai.pi_agent import PiAgent
from local_ai.store import Store


@pytest.mark.parametrize('composite',[False,True])
async def test_queue_handoff_still_reviews_and_repairs_the_whole_request(tmp_path,monkeypatch,composite):
    import local_ai.pi_agent as module
    monkeypatch.setattr(module,'model_status',lambda _:[])
    store=Store(tmp_path/'db')
    jid=store.enqueue(1,1,'chat','画一个蓝圆'+('，并计算123*456' if composite else ''))
    store.update(jid,status='running')
    requests=[];reviews=[]
    async def respond(request):
        payload=await request.json();requests.append(payload)
        results={m.get('tool_call_id') for m in payload['messages'] if m['role']=='tool'}
        if 'image-call' not in results:
            name,cid,args='generate_image','image-call',{'prompt':'A flat blue circle on white.'}
        elif composite and 'calc-call' not in results:
            assert reviews, 'media submission must go directly to independent review'
            name,cid,args='calculate','calc-call',{'expression':'123*456'}
        else:
            name=None
        delta={'role':'assistant','tool_calls':[{'index':0,'id':cid,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]} if name else {'role':'assistant','content':'图片已排队，123*456=56088。'}
        def chunk(d,finish):return json.dumps({'id':'r','object':'chat.completion.chunk','model':'qwen38','choices':[{'index':0,'delta':d,'finish_reason':finish}]})
        return web.Response(text=f'data: {chunk(delta,None)}\n\ndata: {chunk({},"tool_calls" if name else "stop")}\n\ndata: [DONE]\n\n',content_type='text/event-stream')
    app=web.Application();app.router.add_post('/v1/chat/completions',respond)
    server=web.AppRunner(app);await server.setup();site=web.TCPSite(server,'127.0.0.1',0);await site.start()
    port=site._server.sockets[0].getsockname()[1]
    (tmp_path/'node_modules').symlink_to(ROOT/'pi/node_modules')
    for name in ('context.mjs','progress.mjs','tool-context.mjs'):(tmp_path/name).write_text((ROOT/'pi'/name).read_text())
    runner=tmp_path/'runner.mjs';runner.write_text((ROOT/'pi/runner-v2.mjs').read_text().replace('127.0.0.1:8085',f'127.0.0.1:{port}'))
    async def review(job,answer):
        reviews.append(answer)
        if composite and len(reviews)==1:
            return {'accepted':False,'repair':True,'feedback':'还需计算123*456','outcome':'partial','answer':answer}
        return {'accepted':True,'repair':False,'outcome':'pending','answer':answer}
    agent=PiAgent(SimpleNamespace(lock=asyncio.Lock(),start=AsyncMock(),stop=AsyncMock()),store,lambda _:True,tmp_path,runner=runner,reviewer=SimpleNamespace(review=review))
    try:
        answer=await asyncio.wait_for(agent.run(store.get(jid)),15)
        assert '生成队列' in reviews[0]
        assert len(requests)==(3 if composite else 1)
        assert len(reviews)==(2 if composite else 1)
        assert not agent.progress
        assert store.db.execute('SELECT count(*) FROM jobs WHERE parent_job_id=?',(jid,)).fetchone()[0]==1
        if composite:assert '56088' in answer and store.operation(jid,'calc-call')['status']=='succeeded'
    finally:await server.cleanup();store.db.close()
