"""Real local Qwen + Pi, deterministic retrieval faults, no Telegram sends."""
import asyncio
import json
import time
from pathlib import Path

from local_ai.engine import Engine, RUNTIME
from local_ai.store import Store
from local_ai.pi_agent import PiAgent, UserTools
from local_ai.task_context import review_handoff
import local_ai.pi_agent as bridge


async def main():
    folder = RUNTIME/'harness-recovery'
    folder.mkdir(exist_ok=True)
    store = Store(folder/f'test-{time.time_ns()}.sqlite3')
    engine = Engine()
    report = []
    searches = 0
    inject_fault = False
    async def search(query):
        nonlocal searches
        searches += 1
        if inject_fault and searches == 1:
            raise ValueError('搜索通道暂时超时，请调整查询或读取具体来源。')
        return {'results':[{'title':'测试餐厅甲（深圳）','url':'https://example.com/restaurant-a',
                           'snippet':'测试数据，综合及口味和环境评分见详情页'}], 'untrusted_content':True}
    async def read(url):
        if url != 'https://example.com/restaurant-a':
            raise ValueError('此测试页面不存在，请使用搜索结果给出的完整网址。')
        environment = '4.2' if inject_fault else '4.3'
        return {'url':url,'text':f'仅供软件测试。测试餐厅甲，深圳，综合评分4.5，口味4.6，环境{environment}。',
                'retrieval_status':'ok','untrusted_content':True}
    bridge.search_web, bridge.read_web = search, read
    try:
        prior = store.enqueue(1,1,'chat','把之前的图片发给我')
        store.update(prior,status='succeeded',result='已发送图片。')
        store.remember(1,'把之前的图片发给我','已发送图片。')
        failed = store.enqueue(1,1,'chat','软件测试：帮我在大众点评找到深圳综合评分4分以上，环境分低于口味分0.3的店铺；本次可以使用标注为软件测试的评分资料验证流程。')
        store.update(failed,status='failed',error='Pi 未返回正文；工具轮数达到上限')
        UserTools(store,store.get(failed),lambda _:True).record('web_search','{"query":"深圳 大众点评 分项评分"}',None,'搜索服务多次失败')
        jid = store.enqueue(1,1,'chat','为什么没能完成')
        store.update(jid,status='running')
        answer = await PiAgent(engine,store,lambda _:True,folder).run(store.get(jid))
        assert ('大众点评' in answer or '餐厅' in answer or '评分' in answer) and '图片' not in answer, answer
        assert review_handoff(store,store.get(jid),answer) is None, answer
        assert store.db.execute("SELECT count(*) FROM pi_tools WHERE job_id=? AND name='web_read'",(jid,)).fetchone()[0], answer
        report.append({'case':'failed_request_followup','answer':answer})
        store.update(jid,status='succeeded',result=answer)

        searches = 0
        inject_fault = True
        jid = store.enqueue(2,2,'chat','这是软件测试，请查找深圳综合评分大于4分，口味分比环境分至少高0.3的餐厅，列出三项分数和差值以及来源。')
        store.update(jid,status='running')
        answer = await PiAgent(engine,store,lambda _:True,folder).run(store.get(jid))
        names = [r[0] for r in store.db.execute('SELECT name FROM pi_tools WHERE job_id=?',(jid,))]
        assert searches >= 2 and 'web_read' in names and 'calculate' in names and '4.5' in answer and '0.4' in answer, answer
        report.append({'case':'retrieval_fault_recovery','answer':answer,'tools':names})
        print(json.dumps(report,ensure_ascii=False,indent=2))
        (folder/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    finally:
        await engine.stop()
        store.db.close()


if __name__ == '__main__':
    asyncio.run(main())
