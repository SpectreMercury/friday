"""Durable storyboard -> bounded render window -> individual clips -> assembled film.

A project occupies one session execution; GPU work still uses Engine's global
lease. Thousands of storyboard rows must never become thousands of live jobs.
"""
from __future__ import annotations
import asyncio
import copy
import hashlib
import json
import math
import os
import time
from pathlib import Path

from .capabilities import Capability, string
from .outcomes import json_object, estimated_tokens, request_size
from .video_review import REFERENCE_FIELDS, PROGRESSION_SCHEMA, references, check_distinct, require_review

DAY = 24 * 3600
WINDOW = 2
MAX_SECONDS = 100 * 60


def init_schema(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS video_projects(
            job_id TEXT PRIMARY KEY,account_id TEXT NOT NULL,session_id TEXT NOT NULL,
            source_job_id TEXT NOT NULL,request_hash TEXT NOT NULL,script TEXT NOT NULL,
            title TEXT NOT NULL,target_seconds INTEGER NOT NULL,hd INTEGER NOT NULL,
            deadline REAL NOT NULL,estimate TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'planning',
            bible TEXT,created REAL NOT NULL,
            UNIQUE(source_job_id,request_hash));
        CREATE TABLE IF NOT EXISTS video_shots(
            project_id TEXT NOT NULL,ordinal INTEGER NOT NULL,duration INTEGER NOT NULL,
            plan TEXT NOT NULL,job_id TEXT UNIQUE,export_path TEXT,
            PRIMARY KEY(project_id,ordinal));
    ''')


class VideoProjects:
    def __init__(self, store):
        self.store=store
        init_schema(store.db)

    def get(self, jid):
        row=self.store.db.execute('SELECT * FROM video_projects WHERE job_id=?',(jid,)).fetchone()
        return dict(row) if row else None

    def estimate(self, seconds, hd=False):
        if isinstance(seconds,bool) or not isinstance(seconds,int) or not 1<=seconds<=MAX_SECONDS:
            raise ValueError('成片目标须为1–6000秒。')
        # Prefer the exact local 15-second profile. Shorter samples require
        # conservative extrapolation: attention cost is not simply linear.
        samples=[]
        for row in self.store.db.execute('''SELECT p.snapshot,r.started_at,r.ended_at,j.id
            FROM media_prompts p JOIN jobs j ON j.id=p.job_id JOIN task_runs r ON r.job_id=j.id
            WHERE j.kind='video' AND j.status='succeeded' AND r.ended_at IS NOT NULL AND r.status='succeeded'
            AND NOT EXISTS(SELECT 1 FROM video_projects v WHERE v.job_id=j.id)
            ORDER BY r.ended_at DESC LIMIT 30'''):
            snap=json.loads(row['snapshot']);graph=snap.get('workflow',{});params=graph.get('text',{}).get('inputs',{})
            frames=params.get('length',0);pixels=params.get('width',0)*params.get('height',0)
            if frames<100 or not pixels:continue
            profile_pixels=(480*864 if hd else 352*608)
            runtime=row['ended_at']-row['started_at']
            if runtime<60:continue
            exact=frames==362 and pixels==profile_pixels
            scaled=runtime*(362/frames)**1.35*max(.65,profile_pixels/pixels)
            samples.append((exact,scaled,row['id']))
        exact=[sample for sample in samples if sample[0]]
        chosen=exact or samples
        # Initial estimate is deliberately labelled, not sold as a benchmark.
        per_clip=max(sample[1] for sample in chosen[:5]) if chosen else (3600 if hd else 2400)
        shots=math.ceil(seconds/15)
        planning=120+shots*35
        reserve=600+shots*20
        estimated=math.ceil(per_clip*shots*1.25+planning+reserve)
        # Existing jobs and concurrent projects consume the same single GPU.
        waiting=0
        for row in self.store.db.execute("SELECT j.id,j.kind,j.created FROM jobs j WHERE j.status IN ('queued','running') AND NOT EXISTS(SELECT 1 FROM video_shots s WHERE s.job_id=j.id)"):
            project=self.get(row['id'])
            waiting+=max(0,json.loads(project['estimate'])['estimated_seconds']-(time.time()-project['created'])) if project else (per_clip if row['kind']=='video' else 180)
        return {'render_concurrency':1,'prepared_window':WINDOW,'shots':shots,'clip_seconds':15,
            'seconds_per_clip':round(per_clip),'estimated_seconds':estimated,'queue_allowance_seconds':math.ceil(waiting),
            'budget_seconds':DAY,'fits_budget':estimated+waiting<=DAY,
            'max_minutes_per_24h':math.floor(max(0,DAY-waiting-720)/(per_clip*1.25+55))*.25,
            'basis':'measured_15s' if exact else 'extrapolated' if samples else 'uncalibrated',
            'samples':len(chosen),'resolution':[480,864] if hd else [352,608],
            'note':'包含25%生成余量和规划、合并时间；不是完成时间保证。超过预算时保留完整要求，不自动缩短或降画质。'}

    def create(self, parent, title, script, target_minutes, hd=False, *, reference_choices=None):
        if not isinstance(script,str) or not 1<=len(script.strip())<=30000:raise ValueError('剧本须为1–30000字符。长剧本请按集提交。')
        if not isinstance(title,str) or not 1<=len(title.strip())<=100:raise ValueError('标题须为1–100字符。')
        if isinstance(target_minutes,bool) or not isinstance(target_minutes,(int,float)) or not math.isfinite(target_minutes) or not 1<=target_minutes<=100:raise ValueError('成片时长须为1–100分钟。')
        target=round(target_minutes*60)
        digest=hashlib.sha256(json.dumps([title,script,target,hd,reference_choices],ensure_ascii=False).encode()).hexdigest()
        old=self.store.db.execute('SELECT job_id FROM video_projects WHERE source_job_id=? AND request_hash=?',(parent['id'],digest)).fetchone()
        if old:
            job=self.store.get(old[0]);return {'job_id':job['id'],'status':job['status'],'project':self.public(job['id'])}
        estimate=self.estimate(target,hd)
        if not estimate['fits_budget']:
            return {'submitted':False,'reason':'exceeds_24h_budget','estimate':estimate,
                'next_step':'这台 Mac 无法在24小时预算内完成所请求的全片。请用户选择缩短本批时长、按集分批，或另接渲染算力；不能擅自截断剧本。'}
        if estimated_tokens(script)>14000:
            raise ValueError('剧本超出本地模型可完整处理的单集上下文，请按集提交；不会静默截断。')
        jid=self.store.enqueue(parent['user_id'],parent['chat_id'],'video',title,
            hd=hd,parent_job_id=parent['id'],request_id='video-project:'+parent['id']+':'+digest,
            _video_references=reference_choices,
            _project={'request_hash':digest,'script':script,'title':title,'target':target,'hd':hd,'estimate':estimate})
        return {'submitted':True,'job_id':jid,'status':'queued','parent_job_id':parent['id'],
            'artifact_exists':False,'delivery':'not_sent','estimate':estimate}

    def insert(self,jid,parent_id,data):
        # Called inside enqueue's transaction: a project can never recover as an
        # ordinary single video after a crash between two separate commits.
        job=self.store.get(jid)
        self.store.db.execute('INSERT INTO video_projects(job_id,account_id,session_id,source_job_id,request_hash,script,title,target_seconds,hd,deadline,estimate,created) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            (jid,job['account_id'],job['session_id'],parent_id,data['request_hash'],data['script'],data['title'],data['target'],int(data['hd']),job['created']+DAY,json.dumps(data['estimate']),job['created']))

    def public(self,jid):
        project=self.get(jid)
        if not project:return None
        rows=list(self.store.db.execute('SELECT s.ordinal,j.status FROM video_shots s LEFT JOIN jobs j ON j.id=s.job_id WHERE s.project_id=?',(jid,)))
        completed=sum(row['status']=='succeeded' for row in rows)
        failed=sum(row['status'] in ('failed','cancelled') for row in rows)
        return {'title':project['title'],'target_seconds':project['target_seconds'],'total':math.ceil(project['target_seconds']/15),
            'planned':len(rows),'completed':completed,'failed':failed,'state':project['state'],
            'deadline':project['deadline'],'estimate':json.loads(project['estimate']),
            'render_concurrency':1,'prepared_window':WINDOW,'plan_file':f'video-project-{jid}.json' if len(rows)==math.ceil(project['target_seconds']/15) else None}

    def decorate(self,items):
        by_id={item['id']:item for item in items}
        ids=json.dumps(list(by_id))
        for row in self.store.db.execute('SELECT job_id FROM video_projects WHERE job_id IN (SELECT value FROM json_each(?))',(ids,)):
            by_id[row['job_id']]['project']=self.public(row['job_id'])
        for row in self.store.db.execute('''SELECT s.*,p.target_seconds FROM video_shots s JOIN video_projects p ON p.job_id=s.project_id
            WHERE s.job_id IN (SELECT value FROM json_each(?))''',(ids,)):
            by_id[row['job_id']]['shot']={'project_id':row['project_id'],'index':row['ordinal']+1,
                'total':math.ceil(row['target_seconds']/15),'duration':row['duration'],'summary':json.loads(row['plan'])['summary']}
        return items

    def resume(self,caller,jid):
        project=self.get(jid)
        if not project or (project['account_id'],project['session_id'])!=(caller['account_id'],caller['session_id']):
            raise PermissionError('只能续接当前会话的视频项目。')
        job=self.store.get(jid)
        check_distinct(self.shots(jid))
        if job['status'] in ('queued','running','succeeded'):
            return {'job_id':jid,'status':job['status'],'project':self.public(jid)}
        seconds_left=project['deadline']-time.time()
        remaining=self.store.db.execute("SELECT sum(s.duration) FROM video_shots s LEFT JOIN jobs j ON j.id=s.job_id WHERE s.project_id=? AND (j.status IS NULL OR j.status!='succeeded')",(jid,)).fetchone()[0]
        planned=self.store.db.execute('SELECT count(*) FROM video_shots WHERE project_id=?',(jid,)).fetchone()[0]
        required=900 if remaining is None and planned==math.ceil(project['target_seconds']/15) else self.estimate(remaining or project['target_seconds'],bool(project['hd']))['estimated_seconds']
        if seconds_left<=0 or required>seconds_left:
            raise RuntimeError('原任务的24小时剩余预算不足；已完成分段保留。请明确选择下一批范围，不能重置原截止时间。')
        self.store.link_task(caller,project['source_job_id']);caller.update(self.store.get(caller['id']))
        with self.store.db:
            ids=[jid]+[row['id'] for row in self.store.db.execute("SELECT j.id FROM video_shots s JOIN jobs j ON j.id=s.job_id WHERE s.project_id=? AND j.status IN ('failed','cancelled')",(jid,))]
            for ident in ids:
                self.store.db.execute("UPDATE jobs SET status='queued',outcome=NULL,result=NULL,error=NULL,delivered=0,delivery_state='pending' WHERE id=?",(ident,))
                self.store.db.execute('UPDATE tasks SET outcome=NULL WHERE id=(SELECT task_id FROM jobs WHERE id=?)',(ident,))
                self.store.session_event(project['account_id'],project['session_id'],'task_updated',{'job_id':ident,'status':'queued'})
        self._state(jid,'rendering' if project['bible'] else 'planning')
        return {'job_id':jid,'status':'queued','project':self.public(jid),'resumed':True}

    async def reconcile_resume(self,caller,jid):
        project=self.get(jid)
        if not project or (project['account_id'],project['session_id'])!=(caller['account_id'],caller['session_id']):return None
        job=self.store.get(jid)
        if job['status'] in ('queued','running','succeeded'):return {'job_id':jid,'status':job['status'],'project':self.public(jid)}
        return None

    async def script_source(self,job,args):
        keys=[key for key in ('script','script_attachment_id','script_source_job_id','source_message_id') if args.get(key)]
        if len(keys)!=1:raise ValueError('请选择且只选择一个剧本来源：正文、附件或本会话原消息。')
        if keys[0]=='script':return args['script']
        if keys[0]=='source_message_id':
            from .agent.transcript import Transcript
            return Transcript(self.store.db).user_message(job['account_id'],job['session_id'],args['source_message_id'])
        if keys[0]=='script_source_job_id':
            source=self.store.get(args['script_source_job_id'])
            if not source or (source['account_id'],source['session_id'])!=(job['account_id'],job['session_id']):
                raise PermissionError('只能引用当前会话的剧本消息。')
            return source['prompt']
        from .attachments import Attachments
        extracted=await Attachments(self.store,job).read(args['script_attachment_id'],limit=30001)
        if extracted.get('truncated') or extracted['total_characters']>30000:
            raise ValueError('剧本过长或提取不完整，请按集提交；不会静默截断正文。')
        return extracted['text']

    def register(self,registry,job):
        async def call(name,args):
            if name=='resume_video_project':return self.resume(job,args['project_id'])
            if name=='video_capacity':return self.estimate(round(args['target_minutes']*60),args.get('hd',False))
            script=await self.script_source(job,args)
            return self.create(job,args['title'],script,args['target_minutes'],args.get('hd',False),reference_choices=references(args))
        registry.register(Capability('resume_video_project','用户明确要求继续未完成的视频项目时，复用已有分镜和成片；不重做成功镜头，不重置原24小时截止时间。',{'project_id':string(80,minLength=1)},('project_id',),effect='state',replay='reconcile',group='media'),call,reconcile=lambda name,args:self.reconcile_resume(job,args['project_id']))
        registry.register(Capability('video_capacity','估算本机长视频耗时、单GPU并发和24小时容量；区分实测与外推。',
            {'target_minutes':{'type':'number','minimum':1,'maximum':100},'hd':{'type':'boolean'}},('target_minutes',),group='media'),call)
        registry.register(Capability('generate_video_project','先在聊天中讨论剧本、角色照片和环境照片，再提交分镜预览。用户确认完整方案前不渲染。对话里发过的剧本用source_message_id引用，附件用script_attachment_id，只有很短的正文才直接写script。最长15秒一镜，确认后逐段交付并合并；先检查24小时容量。',
            {**REFERENCE_FIELDS,'title':string(100,minLength=1),'script':string(30000,minLength=1),'script_attachment_id':string(80,minLength=1),'script_source_job_id':string(80,minLength=1),'source_message_id':{'type':'integer','minimum':1},'target_minutes':{'type':'number','minimum':1,'maximum':100},'hd':{'type':'boolean'}},
            ('title','target_minutes','character_photos','environment_photos'),effect='submit',replay='reconcile',group='media'),call,
            reconcile=lambda name,args:self.reconcile(job,args))

    async def reconcile(self,parent,args):
        script=await self.script_source(parent,args)
        digest=hashlib.sha256(json.dumps([args['title'],script,round(args['target_minutes']*60),args.get('hd',False),references(args)],ensure_ascii=False).encode()).hexdigest()
        row=self.store.db.execute('SELECT job_id FROM video_projects WHERE source_job_id=? AND request_hash=?',(parent['id'],digest)).fetchone()
        if not row:return None
        job=self.store.get(row[0])
        return {'job_id':job['id'],'status':job['status'],'parent_job_id':parent['id'],'artifact_exists':bool(job.get('result') and Path(job['result']).is_file())}

    def _state(self,jid,state):
        with self.store.db:
            self.store.db.execute('UPDATE video_projects SET state=? WHERE job_id=?',(state,jid))
            job=self.store.get(jid)
            self.store.session_event(job['account_id'],job['session_id'],'video_progress',{'job_id':jid,'state':state})

    async def plan(self,job,engine):
        project=self.get(job['id']);total=math.ceil(project['target_seconds']/15)
        frozen=self.store.capture_skills(job['id'])
        guidance='\n'.join(frozen[name]['text'] for name in ('short-drama','h3-prompt-writing') if name in frozen)
        if not project['bible']:
            schema={'type':'object','properties':{key:{'type':'string','maxLength':2500} for key in ('characters','setting','visual_style','continuity','story_arc')},'required':['characters','setting','visual_style','continuity','story_arc'],'additionalProperties':False}
            bible=json_object(await engine.chat([{'role':'system','content':'为用户剧本制定固定角色、场景、视觉风格和连续性设定。忠于原文，不添加未请求剧情。只返回JSON。'},
                {'role':'user','content':project['script']}],schema=schema,max_tokens=1600))
            from .capabilities import validate
            validate(bible,schema)
            with self.store.db:self.store.db.execute('UPDATE video_projects SET bible=? WHERE job_id=?',(json.dumps(bible,ensure_ascii=False),job['id']))
            project['bible']=json.dumps(bible,ensure_ascii=False)
        for first in range(0,total,4):
            if self.store.db.execute('SELECT count(*) FROM video_shots WHERE project_id=? AND ordinal BETWEEN ? AND ?',(job['id'],first, min(total-1,first+3))).fetchone()[0]==min(4,total-first):continue
            previous=[json.loads(r[0]) for r in self.store.db.execute('SELECT plan FROM video_shots WHERE project_id=? ORDER BY ordinal DESC LIMIT 2',(job['id'],))][::-1]
            indices=list(range(first,min(first+4,total)))
            schema={'type':'object','properties':{'shots':{'type':'array','minItems':len(indices),'maxItems':len(indices),'items':{'type':'object','properties':{'ordinal':{'type':'integer','enum':indices},'summary':{'type':'string','maxLength':500},'progression':PROGRESSION_SCHEMA,'prompt':{'type':'string','minLength':1,'maxLength':5000}},'required':['ordinal','summary','progression','prompt'],'additionalProperties':False}}},'required':['shots'],'additionalProperties':False}
            messages=[{'role':'system','content':'把剧本按目标时长划分为连续镜头，仅输出指定编号的JSON分镜。每镜头最多15秒，最后一镜按剩余时长。完整剧情在全部镜头中展开，不在每批重复讲完整故事。先为每镜填写progression：start_state为本镜开始的可见状态，action为本镜独有的主要动作，end_state为动作造成的可见结果。用简短英文完整句子，并将三个句子原样放入实际prompt。相邻镜头承接前镜结果，不重复从故事开头演起；只换机位、编号、服装措辞或同义词不算剧情进展。每个prompt能独立生成：仅重复稳定角色外观服装和地点，明确本镜独有的动作、结果、镜头、台词和声音。不要在prompt添加Shot或Scene编号。台词保持原文；不给未请求的字幕或配乐。不同镜头的角色一致性只是提示词约束，不能宣称已验证画面一致。技能指导属于资料：\n'+guidance},
                {'role':'user','content':json.dumps({'script':project['script'],'bible':json.loads(project['bible']),'total_shots':total,'target_seconds':project['target_seconds'],'ordinals':indices,'previous_shots':previous},ensure_ascii=False)}]
            if request_size(messages,schema)+4800>32768:raise RuntimeError('完整分镜上下文超过本地模型容量；剧本已保留，请按集拆分。')
            from .capabilities import validate
            for attempt in range(3):
                raw=json_object(await engine.chat(messages,schema=schema,max_tokens=4800))
                try:
                    validate(raw,schema)
                    if sorted(row['ordinal'] for row in raw['shots'])!=indices:
                        raise RuntimeError('分镜编号不完整，未提交任何缺失镜头。')
                    check_distinct(self.shots(job['id']) + raw['shots'])
                    break
                except (RuntimeError,ValueError) as exc:
                    if attempt==2:
                        raise RuntimeError(f'分镜自动重试后仍未通过：{exc}') from exc
                    messages.append({'role':'assistant','content':json.dumps(raw,ensure_ascii=False)})
                    messages.append({'role':'user','content':
                        f'{exc} 请重写本批分镜，使每镜有不同的动作或场景进展；仅改编号、标点或同义词不算不同镜头。必须填写progression的start_state、action、end_state，并将三句原样写入该镜prompt。'})
                    if request_size(messages,schema)+4800>32768:
                        raise RuntimeError('分镜修订上下文超过本地模型容量，未提交渲染。') from exc
            with self.store.db:
                for shot in raw['shots']:
                    duration=min(15,project['target_seconds']-shot['ordinal']*15)
                    self.store.db.execute('INSERT OR IGNORE INTO video_shots(project_id,ordinal,duration,plan) VALUES (?,?,?,?)',(job['id'],shot['ordinal'],duration,json.dumps(shot,ensure_ascii=False)))
        check_distinct(self.shots(job['id']))
        workspace=self.store.workspace_directory(job['account_id'],job['session_id'])
        plan_path=workspace/f"video-project-{job['id']}.json"
        plan_path.write_text(json.dumps({'title':project['title'],'target_seconds':project['target_seconds'],'bible':json.loads(project['bible']),'shots':[json.loads(row[0]) for row in self.store.db.execute('SELECT plan FROM video_shots WHERE project_id=? ORDER BY ordinal',(job['id'],))]},ensure_ascii=False,indent=2))
        self._state(job['id'],'awaiting_review')

    def shots(self,jid):
        return [json.loads(row[0]) for row in self.store.db.execute('SELECT plan FROM video_shots WHERE project_id=? ORDER BY ordinal',(jid,))]

    def review_document(self,job):
        project=self.get(job['id'])
        snap=json.loads(self.store.db.execute('SELECT snapshot FROM media_prompts WHERE job_id=?',(job['id'],)).fetchone()[0])
        shots=self.shots(job['id'])
        check_distinct(shots)
        return {'title':project['title'],'script':project['script'],
            **references(snap.get('references',{})), 'bible':json.loads(project['bible']),
            'target_seconds':project['target_seconds'],'hd':bool(project['hd']),
            'shots':shots,'first_frame':job.get('image'),
            'workflow_sha256':hashlib.sha256(json.dumps(snap['workflow'],sort_keys=True).encode()).hexdigest()}

    def assert_reviewed(self,job):
        body=json.dumps(self.review_document(job),ensure_ascii=False,sort_keys=True,indent=2)
        digest=hashlib.sha256(body.encode()).hexdigest()
        exists=self.store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='video_reviews'").fetchone()
        if not exists or not self.store.db.execute('SELECT 1 FROM video_reviews WHERE job_id=? AND digest=?',(job['id'],digest)).fetchone():
            raise RuntimeError('视频方案尚未获用户确认或已被修改，未开始渲染。')

    def validate_child(self,job,workflow):
        row=self.store.db.execute('SELECT * FROM video_shots WHERE job_id=?',(job['id'],)).fetchone()
        if not row:return False
        self.assert_reviewed(self.store.get(row['project_id']))
        expected=json.loads(row['plan'])['prompt']
        if job['prompt']!=expected or workflow['text']['inputs']['prompt']!=expected:
            raise RuntimeError('镜头与实际渲染prompt不一致，已停止生成。')
        return True

    def fill_window(self,job):
        self.assert_reviewed(job)
        active=self.store.db.execute("SELECT count(*) FROM video_shots s JOIN jobs j ON j.id=s.job_id WHERE s.project_id=? AND j.status IN ('queued','running','waiting_approval')",(job['id'],)).fetchone()[0]
        for row in self.store.db.execute('SELECT * FROM video_shots WHERE project_id=? AND job_id IS NULL ORDER BY ordinal LIMIT ?',(job['id'],max(0,WINDOW-active))).fetchall():
            plan=json.loads(row['plan'])
            child=self.store.enqueue(job['user_id'],job['chat_id'],'video',plan['prompt'],hd=bool(job['hd']),parent_job_id=job['id'],
                duration_seconds=max(4,row['duration']),_project_slot=True,request_id=f"video-shot:{job['id']}:{row['ordinal']}")
            with self.store.db:
                # Planning already applied the project's frozen guidance. Do not
                # load the chat model again between every pair of video clips.
                captured=json.loads(self.store.db.execute('SELECT snapshot FROM media_prompts WHERE job_id=?',(child,)).fetchone()[0])
                original=json.loads(self.store.db.execute('SELECT snapshot FROM media_prompts WHERE job_id=?',(job['id'],)).fetchone()[0])
                frozen_graph=copy.deepcopy(original['workflow'])
                frozen_graph['text']['inputs']['prompt']=plan['prompt']
                frozen_graph['noise']['inputs']['noise_seed']=captured['seed']
                frozen_graph['save']['inputs']['filename_prefix']=captured['workflow']['save']['inputs']['filename_prefix']
                frozen_graph['text']['inputs']['length']=5+17*math.ceil((max(4,row['duration'])*24-5)/17)
                captured.update(workflow=frozen_graph,models=original['models'],skill=original['skill'],system_prompt=original['system_prompt'])
                captured.update(status='planned',rewritten_prompt=plan['prompt'],prepared_at=time.time(),project_id=job['id'],ordinal=row['ordinal'])
                self.store.db.execute('UPDATE media_prompts SET snapshot=? WHERE job_id=?',(json.dumps(captured,ensure_ascii=False),child))
                self.store.db.execute('UPDATE video_shots SET job_id=? WHERE project_id=? AND ordinal=?',(child,job['id'],row['ordinal']))

    async def execute(self,job,service):
        project=self.get(job['id'])
        remaining=project['deadline']-time.time()
        if remaining<=0:
            self._state(job['id'],'budget_exhausted');self.cancel_pending(job['id'])
            raise RuntimeError('已到24小时截止时间；已完成的分段仍保留，未自动开启新一轮。')
        try:
            async with asyncio.timeout(remaining):
                await self.plan(job,service.engine)
                await require_review(self.store,job,service.approvals.request,self.review_document(job))
                self._state(job['id'],'rendering')
                while True:
                    self.fill_window(job)
                    row=self.store.db.execute("SELECT s.*,j.status FROM video_shots s JOIN jobs j ON j.id=s.job_id WHERE s.project_id=? AND j.status='queued' ORDER BY s.ordinal LIMIT 1",(job['id'],)).fetchone()
                    if not row:break
                    child=self.store.get(row['job_id'])
                    if not service.is_allowed(child['user_id']):raise RuntimeError('账号授权已撤销，已停止视频项目。')
                    run=self.store.start_run(child['id']);child.update(self.store.get(child['id']));child['run_id']=run['id']
                    task=asyncio.create_task(service.run_job(child));service.video_child_tasks[child['id']]=task
                    try:await task
                    except asyncio.CancelledError:
                        if self.store.get(child['id'])['status']=='cancelled' and self.store.get(job['id'])['status']!='cancelled':
                            raise RuntimeError('镜头已取消，项目停止；已完成分段仍保留。') from None
                        raise
                    finally:service.video_child_tasks.pop(child['id'],None)
                    if self.store.get(child['id'])['status']!='succeeded':
                        self._state(job['id'],'partial')
                        raise RuntimeError('一个镜头未完成；已交付的分段保留。停止后续生成，避免浪费剩余预算。')
                    self._state(job['id'],'rendering')
                self._state(job['id'],'assembling')
                output=await self.assemble(job)
                self._state(job['id'],'completed')
                return str(output)
        except TimeoutError:
            self._state(job['id'],'budget_exhausted')
            self.cancel_pending(job['id'])
            raise RuntimeError('已到24小时截止时间，停止后续生成；已完成的视频分段保留在原会话。') from None
        except asyncio.CancelledError:
            if self.store.get(job['id'])['status']=='cancelled':
                self._state(job['id'],'cancelled');self.cancel_pending(job['id'])
            raise

        except Exception:
            self._state(job['id'],'partial');self.cancel_pending(job['id'])
            raise

    def cancel_pending(self,jid):
        for row in self.store.db.execute("SELECT j.id FROM jobs j WHERE j.parent_job_id=? AND j.status='queued'",(jid,)).fetchall():
            self.store.update(row['id'],status='cancelled',outcome='cancelled',delivered=1,delivery_state='suppressed')

    async def assemble(self,job):
        from .video_export import assemble_project
        rows=[dict(r) for r in self.store.db.execute('SELECT s.*,j.result,j.status FROM video_shots s LEFT JOIN jobs j ON j.id=s.job_id WHERE s.project_id=? ORDER BY s.ordinal',(job['id'],))]
        if not rows or any(r['status']!='succeeded' for r in rows):raise RuntimeError('分段未全部完成，不会将缺失镜头拼成完整影片。')
        directory=self.store.workspace_directory(job['account_id'],job['session_id'])/'video-projects'/job['id']
        directory.mkdir(parents=True,exist_ok=True)
        (directory/'storyboard.json').write_text(json.dumps({'project':self.public(job['id']),'shots':[json.loads(r['plan']) for r in rows]},ensure_ascii=False,indent=2))
        return await assemble_project(rows,directory)
