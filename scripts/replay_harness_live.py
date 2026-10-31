"""Replay the reported request with production history and live web tools.

Runs against a private SQLite copy; never sends Telegram messages.
Stop the resident service first to avoid competing for the local model.
"""
import asyncio
import json
import sqlite3
import time

from local_ai.engine import Engine, RUNTIME
from local_ai.pi_agent import PiAgent
from local_ai.store import Store


async def main():
    folder = RUNTIME/'harness-live'
    folder.mkdir(exist_ok=True)
    path = folder/f'replay-{time.time_ns()}.sqlite3'
    source = sqlite3.connect(f'file:{RUNTIME / "bot.sqlite3"}?mode=ro',uri=True)
    with sqlite3.connect(path) as destination:
        source.backup(destination)
    source.close()
    store = Store(path)
    original = dict(store.db.execute("SELECT * FROM jobs WHERE id='9c08199d5819'").fetchone())
    jid = store.enqueue(original['user_id'],original['chat_id'],'chat',original['prompt'])
    store.update(jid,status='running')
    engine = Engine()
    try:
        reply = await PiAgent(engine,store,lambda _:True,folder).run(store.get(jid))
        outcome = store.get(jid)['outcome']
        store.update(jid,status='failed' if outcome in ('blocked','needs_input') else 'succeeded',result=reply)
        events = [dict(r) for r in store.db.execute('SELECT name,args,result,error FROM pi_tools WHERE job_id=?',(jid,))]
        assert any(e['name']=='plan_task' and not e['error'] for e in events), events
        assert any(e['name'] in ('web_search','web_read') for e in events), events
        assert reply.strip() != '这次执行失败：Pi 未返回正文，请重新描述任务。', reply
        report = {'request':original['prompt'],'outcome':outcome,'reply':reply,'events':events}
        (folder/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps({'outcome':outcome,'reply':reply,'tools':[e['name'] for e in events]},ensure_ascii=False,indent=2))
    finally:
        await engine.stop()
        store.db.close()


if __name__=='__main__':
    asyncio.run(main())
