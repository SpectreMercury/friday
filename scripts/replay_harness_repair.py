"""Isolated, non-explicit live replay of the video planning failure path.

Uses the real local chat model, injects one duplicate storyboard response, and
stops at the durable owner-review boundary. No production job or GPU render is
created. Stop the resident service before running to avoid model contention.
"""
import asyncio
import json
import time

from local_ai.engine import Engine, RUNTIME
from local_ai.harness import HarnessService
from local_ai.store import Store


class DuplicateOnceEngine(Engine):
    def __init__(self):
        super().__init__()
        self.shot_attempts = 0

    async def chat(self, messages, *, schema=None, max_tokens=2048):
        if schema and 'shots' in schema.get('properties', {}):
            self.shot_attempts += 1
            if self.shot_attempts == 1:
                indices=schema['properties']['shots']['items']['properties']['ordinal']['enum']
                beat={'start_state':'A blue ball is beside a table.',
                      'action':'The ball rolls across a white floor.',
                      'end_state':'The ball stops beside a door.'}
                return json.dumps({'shots':[{'ordinal':ordinal,'summary':'Injected duplicate for replay',
                                             'progression':beat,'prompt':' '.join(beat.values())}
                                            for ordinal in indices]})
        return await super().chat(messages,schema=schema,max_tokens=max_tokens)


async def main():
    folder=RUNTIME/'harness-repair-replay'/str(time.time_ns())
    folder.mkdir(parents=True)
    store=Store(folder/'replay.sqlite3',skills_root=folder/'skills')
    engine=DuplicateOnceEngine()
    try:
        account=store.create_account('replay-owner')
        session=store.create_session(account['id'],'safe-replay')
        parent_id=store.enqueue(account['user_id'],None,'chat','制作一分钟蓝色小球短片',
                                session_id=session['id'],channel='app')
        parent=store.get(parent_id)
        submitted=store.video_projects.create(
            parent,'蓝色小球的旅程',
            '四个连续镜头：蓝色小球先从桌上滚落，再越过一座纸桥，随后在木地板上转弯，最后停在一盆绿植旁。每镜动作与机位不同。',
            1,reference_choices={'character_photos':'用户选择不用角色照片：无人物',
                                 'environment_photos':'用户选择不用环境照片：纯色布景'})
        assert submitted['submitted']
        project_id=submitted['job_id']
        service=HarnessService(folder,store=store,engine=engine)
        run=store.start_run(project_id)
        project=store.get(project_id)
        project['run_id']=run['id']
        await service.run_job(project)
        project=store.get(project_id)
        shots=store.video_projects.shots(project_id)
        approvals=service.approvals.list_for_session(account['id'],session['id'])
        children=store.db.execute('SELECT count(*) FROM jobs WHERE parent_job_id=?',(project_id,)).fetchone()[0]
        assert engine.shot_attempts>=2, 'duplicate plan was not retried'
        assert len(shots)==4 and len({row['prompt'] for row in shots})==4
        assert project['status']=='waiting_approval' and len(approvals)==1
        assert children==0, 'rendering began before review'
        report={'passed':True,'project_id':project_id,'shot_attempts':engine.shot_attempts,
                'planned_shots':len(shots),'approval_requests':len(approvals),
                'render_jobs':children,'project_status':project['status']}
        (folder/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps({'report':str(folder/'report.json'),**report},ensure_ascii=False))
    finally:
        await engine.stop()
        store.db.close()


if __name__=='__main__':
    asyncio.run(main())
