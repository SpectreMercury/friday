"""Authenticated mobile ingress; all execution belongs to HarnessService."""
from __future__ import annotations
import asyncio
import contextlib
import hashlib
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from urllib.parse import quote
from aiohttp import web

from .attachments import Attachments, MAX_FILE_BYTES
from .mobile_auth import MobileAuth
from .mobile_voice import VoiceHub, VoiceError
from .sessions import RevisionConflict
from .google_picker import GooglePicker
from .browser_auth import BrowserAuth
from .pwa_routes import PWARoutes, check_origin, pwa_security_headers
from .media import media_type, image_thumbnail
from .job_presentation import job_title, status_text, hidden_sessions_sql
from .job_presentation import screen_artifact_path, add_job_timing, management_action


def public_session(session):
    return {k:v for k,v in session.items() if k not in ('workspace_key','account_id')}


def public_job(job, store=None):
    result={k:job.get(k) for k in ('id','session_id','task_id','parent_job_id','kind','prompt','result','error','status','outcome','created')}
    result['title']=job_title(store,job)
    result['status_text']=status_text(job)
    if job.get('request_id'):
        result['request_id']=job['request_id']
    if store and management_action(store,job):
        result['required_action']='enable_mac_management'
        result['result']='当前会话未开启「Mac 管理」，本次电脑操作未执行。开启后请重试原请求。'
    if job['kind'] in ('image','video'):
        result['result']=None
    screenshot = screen_artifact_path(store, job) if store and job['kind'] == 'chat' else None
    value = job.get('result') if job['kind'] in ('image','video') else screenshot or job.get('voice_result')
    if value:
        result['artifact_url']=f"/v1/sessions/{job['session_id']}/tasks/{job['id']}/artifact"
        result['artifact_filename']='Mac 截图.png' if screenshot else Path(value).name
        result['artifact_media_type']=media_type(value)
        path = Path(value)
        try:
            stat = path.stat()
            result['artifact_version'] = hashlib.sha256(f'{stat.st_mtime_ns}:{stat.st_size}'.encode()).hexdigest()[:24]
        except OSError:
            result['artifact_version'] = 'missing'
        if (result['artifact_media_type'] or '').startswith('image/'):
            result['artifact_thumbnail_url'] = result['artifact_url'] + '?thumbnail=1'
        result['artifact_preview_url']=result['artifact_url']+'?preview=1' if result['artifact_media_type'] else None
    return result


def sent_media(store, job_ids):
    """Media a chat turn sent with send_media. In the App nothing is uploaded,
    so the reply must show these items itself or the user never sees them."""
    if not job_ids:
        return {}
    found = {}
    for row in store.db.execute(f"SELECT job_id,result FROM tool_operations WHERE job_id IN ({','.join('?'*len(job_ids))}) "
                                "AND name='send_media' AND status='succeeded' ORDER BY started_at", job_ids):
        items = []
        with contextlib.suppress(ValueError, TypeError, AttributeError):
            items = json.loads(row['result'] or '{}').get('items') or []
        ids = found.setdefault(row['job_id'], [])
        for item in items:
            if isinstance(item, dict) and item.get('status') == 'available' and isinstance(item.get('id'), str) and item['id'] not in ids:
                ids.append(item['id'])
    return {k: v for k, v in found.items() if v}


def create_app(service, *, web_origin=None):
    store=service.store
    auth=MobileAuth(store)
    voice=VoiceHub(service.engine,store,service.enqueue_mobile,store.require_session)
    service.mobile_voice=voice
    thumbnail_slots=asyncio.Semaphore(2)
    pair_attempts={}
    voice_devices={}
    picker=GooglePicker(store.connectors)
    if web_origin is None and os.environ.get('PWA_ENABLED', '1') == '1':
        web_origin = os.environ.get('MOBILE_PUBLIC_URL') or None
        if not web_origin and os.environ.get('PWA_ENABLED') == '1':
            raise ValueError('启用 PWA 前请配置固定的 MOBILE_PUBLIC_URL HTTPS 域名。')
    browser = BrowserAuth(store, web_origin) if web_origin else None
    if browser:
        picker.device_authorizer = browser.device_active
    pwa = None
    native_routes = None
    native_enabled = os.environ.get('MOBILE_NATIVE_ENABLED', '0' if browser else '1') == '1'
    native_pairing = native_enabled and os.environ.get('MOBILE_NATIVE_PAIRING', '1') == '1'

    def reauthenticate(request):
        if request.get('browser_token'):
            return browser.authenticate(request['browser_token'])
        return auth.authenticate(request.headers.get('Authorization', '').partition(' ')[2])

    async def close_device_voice(did):
        for vid, device_id in list(voice_devices.items()):
            if device_id == did:
                row = store.db.execute('SELECT account_id FROM mobile_devices WHERE id=?', (did,)).fetchone()
                if row:
                    with contextlib.suppress(VoiceError):
                        await voice.end(row['account_id'], vid)
                voice_devices.pop(vid, None)

    @web.middleware
    async def guard(request,handler):
        try:
            public = request.path in ('/healthz','/v1/pair','/v1/oauth/google/callback','/v1/google/picker','/v1/google/picker/selection') or (pwa and pwa.public(request.path)) or (native_routes and request.path in native_routes.public_paths)
            if not public:
                scheme,_,token=request.headers.get('Authorization','').partition(' ')
                try:
                    if scheme == 'Bearer' and native_enabled:
                        request['device']=auth.authenticate(token)
                    elif not scheme and browser:
                        request['device']=browser.authenticate(request.cookies.get(browser.cookie_name), touch=False)
                    else:
                        raise PermissionError('请先验证身份。')
                except PermissionError as exc:
                    return web.json_response({'error':{'code':'unauthorized','message':str(exc)}},status=401)
                if not scheme and browser:
                    request['device']=pwa.authenticate(request)
                elif browser and request.headers.get('Origin'):
                    check_origin(request, browser.origin)
            response=await handler(request)
            response.headers['Cache-Control']='no-store'
            response.headers['X-Content-Type-Options']='nosniff'
            return response
        except RevisionConflict as exc:
            return web.json_response({'error':{'code':'revision_conflict','message':str(exc)}},status=409)
        except VoiceError as exc:
            return web.json_response({'error':{'code':exc.code,'message':str(exc)}},status=exc.status)
        except PermissionError as exc:
            return web.json_response({'error':{'code':'forbidden','message':str(exc)}},status=403)
        except (ValueError,KeyError,TypeError,json.JSONDecodeError) as exc:
            return web.json_response({'error':{'code':'invalid_request','message':str(exc)[:300]}},status=400)
        except FileNotFoundError:
            return web.json_response({'error':{'code':'not_found','message':'文件不存在。'}},status=404)
        except web.HTTPException:
            raise
        except Exception:
            logging.error('Mobile request failed: %s %s',request.method,request.path)
            return web.json_response({'error':{'code':'service_error','message':'请求未完成，请查看本机服务状态。'}},status=500)

    app=web.Application(middlewares=[guard],client_max_size=MAX_FILE_BYTES+65536)
    async def security_headers(request,response):
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        if browser:
            pwa_security_headers(request, response, browser)
    app.on_response_prepare.append(security_headers)

    def owner(request):
        return request['device']['account_id']

    def session(request):
        return store.require_session(owner(request),request.match_info['sid'])

    async def body(request):
        if request.content_length and request.content_length>65536:
            raise web.HTTPRequestEntityTooLarge(max_size=65536,actual_size=request.content_length)
        chunks=bytearray()
        async for chunk in request.content.iter_chunked(8192):
            chunks.extend(chunk)
            if len(chunks)>65536:
                raise web.HTTPRequestEntityTooLarge(max_size=65536,actual_size=len(chunks))
        value=json.loads(chunks)
        if not isinstance(value,dict):
            raise ValueError('请求正文必须为JSON对象。')
        if request.get('device'):
            # An upload can outlive a browser/device revocation. Check again at
            # admission, after awaiting the entire (possibly chunked) body.
            reauthenticate(request)
        return value

    async def health(request):
        return web.json_response({'ready':True,'api_version':1,'build_id':service.build_id,
            'upgrade_ready':not getattr(service,'active_runs',{}) and not getattr(service.engine,'voice_reserved',False) and not getattr(voice,'_lease',None)})

    async def pair(request):
        if not native_pairing:
            raise web.HTTPNotFound()
        # No proxy-forwarded address trust: gateway applies its own rate limit.
        key=request.remote or 'unknown';now=time.monotonic()
        recent=[t for t in pair_attempts.get(key,[]) if now-t<60]
        if len(recent)>=10:
            return web.json_response({'error':{'code':'rate_limited','message':'请稍后重试配对。'}},status=429)
        if len(pair_attempts)>1000:
            pair_attempts.clear()
        pair_attempts[key]=[*recent,now]
        data=await body(request)
        return web.json_response(auth.pair(data.get('code'),data.get('device_name')))

    async def sessions(request):
        if request.method=='GET':
            account=owner(request)
            hidden={r[0] for r in store.db.execute('SELECT session_id FROM drama_projects WHERE account_id=?',(account,))}
            items=[public_session(s) for s in store.list_sessions(account) if s['id'] not in hidden]
            # One line of context per session: the latest turn and what needs attention.
            latest={}
            for row in store.db.execute('''SELECT j.* FROM jobs j WHERE j.account_id=? AND j.parent_job_id IS NULL
                    AND j.kind IN ('chat','image','video') AND j.id NOT IN (SELECT job_id FROM deleted_jobs) AND j.created=(SELECT max(k.created) FROM jobs k
                    WHERE k.session_id=j.session_id AND k.parent_job_id IS NULL AND k.kind IN ('chat','image','video')
                    AND k.id NOT IN (SELECT job_id FROM deleted_jobs))''',(account,)):
                latest[row['session_id']]=dict(row)
            counts={r['session_id']:dict(r) for r in store.db.execute('''SELECT session_id,
                    sum(status IN ('queued','running')) AS active, sum(status='waiting_approval') AS waiting
                    FROM jobs WHERE account_id=? GROUP BY session_id''',(account,))}
            for item in items:
                last=latest.get(item['id'])
                if last:
                    item['last']={'title':job_title(store,last),'status':last['status'],'status_text':status_text(last),'created':last['created']}
                count=counts.get(item['id']) or {}
                item['active']=count.get('active') or 0
                item['waiting']=count.get('waiting') or 0
            # Most recently used first: an active conversation should not sink below new empty ones.
            items.sort(key=lambda item:max(item.get('created') or 0,(item.get('last') or {}).get('created') or 0),reverse=True)
            return web.json_response({'items':items})
        data=await body(request)
        name=data.get('name')
        # No name: start talking now; the server titles it from the first exchange.
        auto=name is None or (isinstance(name,str) and not name.strip())
        return web.json_response(public_session(store.create_session(owner(request),name,auto_title=auto)),status=201)

    stopping=set()
    def stop_later(job_id):
        # Deleting answers at once: the job is marked cancelled now and the
        # running work winds down in the background, however long that takes.
        store.update(job_id,status='cancelled',outcome='cancelled',delivered=1,delivery_state='suppressed')
        task=asyncio.create_task(service.cancel_job(job_id))
        stopping.add(task);task.add_done_callback(stopping.discard)

    def stop_session_jobs(sid):
        for row in store.db.execute("SELECT id FROM jobs WHERE session_id=? AND status IN ('queued','running','waiting_approval')",(sid,)).fetchall():
            stop_later(row['id'])

    async def one_session(request):
        current=session(request)
        if request.method=='GET':
            return web.json_response(public_session(current))
        if request.method=='DELETE':
            # Into 「最近删除」: running work stops now, the content waits for restore or purge.
            stop_session_jobs(current['id'])
            store.delete_session(owner(request),current['id'])
            return web.json_response({'deleted':True,'id':current['id']})
        data=await body(request);revision=data.pop('revision',None)
        return web.json_response(public_session(store.update_session(owner(request),current['id'],revision,**data)))

    async def evolution(request):
        current=session(request);aid=owner(request);sid=current['id']
        if request.method=='GET':return web.json_response(store.evolution.listing(aid,sid))
        data=await body(request)
        return web.json_response(store.evolution.stage(aid,sid,data.get('kind'),data.get('name'),data.get('files'),data.get('summary'),data.get('base')),status=201)

    async def evolution_revision(request):
        current=session(request);aid=owner(request);sid=current['id'];rid=request.match_info['rid']
        if request.method=='GET':return web.json_response(store.evolution.get(aid,sid,rid))
        data=await body(request)
        if data.get('revision')!=rid:raise ValueError('确认必须指向具体版本。')
        if data.get('action')=='publish':return web.json_response(await store.evolution.publish(aid,sid,rid,authorize=lambda:reauthenticate(request)))
        if data.get('action')=='rollback_draft':return web.json_response(store.evolution.rollback_draft(aid,sid,rid))
        raise ValueError('未知升级操作。')

    async def mcp_bindings(request):
        current=session(request);aid=owner(request);sid=current['id']
        if request.method=='GET':return web.json_response(store.evolution.bindings(aid,sid))
        data=await body(request)
        return web.json_response(store.evolution.bind(aid,sid,data.get('names'),data.get('revision')))

    async def skills(request):
        return web.json_response({'items':store.skill_catalog()})

    async def session_skills(request):
        current=session(request);data=await body(request)
        return web.json_response(public_session(store.update_session(owner(request),current['id'],data.get('revision'),skill_names=data.get('skill_names'))))

    async def messages(request):
        current=session(request)
        if request.method=='POST':
            data=await body(request)
            job=service.enqueue_mobile(owner(request),current['id'],data.get('text'),data.get('request_id'),
                                       thinking=data.get('thinking'),agents=data.get('agents'))
            response=public_job(job)
            response['request_id']=data['request_id']
            return web.json_response(response,status=201)
        rows=add_job_timing(store,[public_job(dict(r),store) for r in store.db.execute('SELECT jobs.*, mobile_requests.request_id AS request_id FROM jobs LEFT JOIN mobile_requests ON mobile_requests.job_id=jobs.id AND mobile_requests.account_id=jobs.account_id WHERE jobs.session_id=? AND jobs.id NOT IN (SELECT job_id FROM deleted_jobs) AND jobs.created>COALESCE((SELECT reset_at FROM session_resets WHERE session_id=?),0) ORDER BY jobs.created DESC,jobs.id DESC LIMIT 1000',(current['id'],current['id']))][::-1])
        store.video_projects.decorate(rows)
        # Ephemeral, unreviewed text is never a final result, checkpoint, or memory.
        # Ownership was checked above; completed/cancelled runs cannot expose it.
        progress = getattr(getattr(service, 'pi', None), 'progress', {})
        running = [item['id'] for item in rows if item['status'] in ('running','waiting_approval') and item['kind'] == 'chat']
        todos = {}
        if running and getattr(service, 'agent', None):
            for row in store.db.execute(f"SELECT job_id,state FROM agent_turns WHERE job_id IN ({','.join('?'*len(running))})", running):
                items = json.loads(row['state']).get('todo') or []
                if items:
                    todos[row['job_id']] = [{'content': i['content'], 'status': i['status']} for i in items[:20]]
        live = getattr(service, 'live', None)
        from .agent import roster
        options = roster.options_for(store.db, [item['id'] for item in rows if item['kind'] == 'chat'])
        dramas = drama_links(owner(request), [item['id'] for item in rows if item['kind'] == 'chat'])
        sent = sent_media(store, [item['id'] for item in rows if item['kind'] == 'chat'])
        for item in rows:
            if item['id'] in sent:
                item['sent_media'] = sent[item['id']]
            if item['status'] == 'running' and item['kind'] == 'chat' and item['id'] in progress:
                item['draft'] = progress[item['id']]
            if item['id'] in todos:
                item['todo'] = todos[item['id']]
            state = live.snapshot(item['id']) if live and item['status'] in ('running', 'waiting_approval') else None
            if state:
                item['live'] = state
            if item['id'] in options:
                item['options'] = options[item['id']]
            if item['id'] in dramas:
                item['drama'] = dramas[item['id']]
        imported=[]
        for row in store.db.execute('SELECT * FROM session_imports WHERE session_id=? ORDER BY id',(current['id'],)):
            value=json.loads(row['content'])
            imported.append({'id':f"import:{row['id']}",'session_id':current['id'],'kind':'imported','status':'succeeded',
                             'prompt':value.get('prompt'),'result':value.get('result'),'created':row['created']})
        items = imported + rows
        revision = hashlib.sha256(json.dumps(items, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
        if request.query.get('revision') == revision:
            return web.json_response({'unchanged': True, 'revision': revision})
        return web.json_response({'items': items, 'revision': revision})

    def drama_links(account, job_ids):
        """The short-drama project each chat turn worked on, from its tool receipts."""
        if not job_ids:
            return {}
        found = {}
        for row in store.db.execute(f"SELECT job_id,arguments,result FROM tool_operations WHERE job_id IN ({','.join('?'*len(job_ids))}) "
                                    "AND name LIKE 'drama_%' AND status='succeeded' ORDER BY started_at", job_ids):
            pid = None
            with contextlib.suppress(ValueError, TypeError, AttributeError):
                pid = json.loads(row['arguments'] or '{}').get('project_id')
            if not pid:
                with contextlib.suppress(ValueError, TypeError, AttributeError):
                    pid = (json.loads(row['result'] or '{}').get('project') or {}).get('id')
            if pid:
                found[row['job_id']] = pid
        if not found:
            return {}
        projects = {p['id']: p for p in store.dramas.list(account)}
        return {jid: {k: projects[pid].get(k) for k in ('id', 'title', 'stage', 'shots', 'done', 'seconds')}
                for jid, pid in found.items() if pid in projects}

    async def agents(request):
        from .agent import roster
        return web.json_response({'items': roster.public_agents()})

    async def suggest_agent(request):
        from .agent import roster
        data = await body(request)
        return web.json_response({'suggestion': roster.suggest(data.get('text'), roster.validate_agents(data.get('agents')))})

    async def stream(request):
        """Server-sent events for this session's running replies: thinking, text, tool steps."""
        current = session(request)
        account = owner(request)
        live = service.live
        queue = live.subscribe(account, current['id'])
        response = web.StreamResponse(headers={'Content-Type': 'text/event-stream; charset=utf-8',
                                               'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})
        try:
            await response.prepare(request)

            async def send(event):
                await response.write(b'data: ' + json.dumps(event, ensure_ascii=False, separators=(',', ':')).encode() + b'\n\n')

            for state in live.running(current['id']):
                await send({'type': 'snapshot', 'job_id': state['job_id'], 'seq': state['seq'], 'state': state})
            await send({'type': 'ready'})
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), 15)
                except asyncio.TimeoutError:
                    reauthenticate(request)
                    await response.write(b': keep-alive\n\n')
                    continue
                await send(event)
        except (ConnectionResetError, PermissionError):
            pass
        finally:
            live.unsubscribe(account, current['id'], queue)
        return response

    async def tasks(request):
        args=[owner(request)];query='SELECT * FROM jobs WHERE account_id=? AND id NOT IN (SELECT job_id FROM deleted_jobs) AND session_id IN (SELECT id FROM sessions WHERE deleted IS NULL)'
        if 'sid' in request.match_info:
            query+=' AND session_id=?';args.append(session(request)['id'])
        else:
            # Drama renders live on the drama page; merged messages are part of another task.
            query+=' AND '+hidden_sessions_sql()+" AND COALESCE(outcome,'')!='continued'"
        return web.json_response({'items':store.video_projects.decorate(add_job_timing(store,[public_job(dict(r),store) for r in store.db.execute(query+' ORDER BY created DESC LIMIT 500',args)]))})

    async def delete_task(request):
        job=store.get_session_job(owner(request),session(request)['id'],request.match_info['jid'])
        if job['status'] in ('queued','running','waiting_approval'):
            stop_later(job['id'])
        return web.json_response({'deleted':store.delete_job(owner(request),job['session_id'],job['id'])})

    async def trash(request):
        if request.method=='DELETE':
            for item in store.deleted_sessions(owner(request)):
                store.purge_session(owner(request),item['id'])
            return web.json_response({'items':[]})
        return web.json_response({'items':store.deleted_sessions(owner(request)),'days':store.TRASH_DAYS})

    async def trash_item(request):
        sid=request.match_info['sid']
        if request.method=='DELETE':
            store.purge_session(owner(request),sid)
            return web.json_response({'purged':True,'id':sid})
        return web.json_response(public_session(store.restore_session(owner(request),sid)))

    async def runs(request):
        return web.json_response({'items':store.session_runs(owner(request),session(request)['id'],request.match_info['jid'])})

    async def voice_message(request):
        current=session(request)
        request_id=request.headers.get('Idempotency-Key')
        if not request_id or len(request_id)>128:
            raise ValueError('语音留言需要有效的请求编号。')
        if request.content_type not in ('audio/mp4','audio/x-m4a'):
            raise ValueError('语音留言需要 M4A 音频。')
        incoming=store.path.parent/'incoming'
        incoming.mkdir(parents=True,exist_ok=True,mode=0o700)
        saved=None
        try:
            with tempfile.NamedTemporaryFile(dir=incoming,suffix='.m4a',delete=False) as output:
                saved=Path(output.name)
                size=0;digest=hashlib.sha256();prefix=b''
                async for chunk in request.content.iter_chunked(65536):
                    size+=len(chunk)
                    if size>MAX_FILE_BYTES:
                        raise ValueError('语音留言不能超过20 MB。')
                    if len(prefix)<32:prefix=(prefix+chunk)[:32]
                    digest.update(chunk);output.write(chunk)
            if size<12 or prefix[4:8]!=b'ftyp':
                raise ValueError('无效的 M4A 音频。')
            # The body may take time to upload: recheck revoked credentials and
            # ownership before enqueueing, and deduplicate by content, not path.
            reauthenticate(request)
            job_id=store.enqueue(store.account(owner(request))['user_id'],None,'chat',
                '语音留言（等待转录）',audio=str(saved),session_id=current['id'],channel='app',
                request_id=request_id,audio_digest=digest.hexdigest())
            job=store.get(job_id)
            if job['audio']==str(saved):saved=None
            return web.json_response(public_job(job),status=201)
        finally:
            if saved:saved.unlink(missing_ok=True)

    async def cancel(request):
        job=store.get_session_job(owner(request),session(request)['id'],request.match_info['jid'])
        if job['status'] in ('queued','running','waiting_approval'):
            await service.cancel_job(job['id'])
        return web.json_response(public_job(store.get(job['id'])))

    async def reset(request):
        current=session(request)
        if store.db.execute("SELECT 1 FROM jobs WHERE session_id=? AND status IN ('queued','running','waiting_approval')",(current['id'],)).fetchone():
            raise RevisionConflict('请先结束此工作空间的待处理任务。')
        store.reset(store.account(owner(request))['user_id'],current['id'])
        return web.json_response({'reset':True})

    async def fork(request):
        current=session(request);data=await body(request)
        result=store.fork_session(owner(request),current['id'],data.get('name'),data.get('message_ids',[]),data.get('file_names',[]))
        register=attachment_manager(owner(request),result['id'])
        for name in data.get('file_names',[]):
            register.add(store.workspace_file(owner(request),result['id'],name),name)
        return web.json_response(public_session(result),status=201)

    def attachment_manager(aid,sid):
        account=store.account(aid)
        return Attachments(store,{'id':'','user_id':account['user_id'],'chat_id':None,'account_id':aid,'session_id':sid},store.path.parent)

    async def files(request):
        current=session(request)
        if request.method=='GET':
            items=[]
            for path in store.workspace_directory(owner(request),current['id']).iterdir():
                try:
                    path=store.workspace_file(owner(request),current['id'],path.name)
                    if path.is_file():
                        url=f"/v1/sessions/{current['id']}/files/{quote(path.name,safe='')}"
                        mime=media_type(path)
                        items.append({'name':path.name,'size':path.stat().st_size,'download_url':url,
                                      'media_type':mime,'preview_url':url+'?preview=1' if mime else None})
                except ValueError:
                    continue
            return web.json_response({'items':sorted(items,key=lambda p:p['name'])})
        reader=await request.multipart();part=await reader.next()
        if part is None or part.name!='file' or not part.filename:
            raise ValueError('请上传file字段。')
        destination=store.workspace_file(owner(request),current['id'],part.filename)
        if len(list(destination.parent.iterdir()))>=100 and not destination.exists():
            raise ValueError('工作空间最多100个文件。')
        fd,name=tempfile.mkstemp(prefix='.upload-',dir=destination.parent)
        size=0
        try:
            with os.fdopen(fd,'wb') as output:
                while chunk:=await part.read_chunk():
                    size+=len(chunk)
                    if size>MAX_FILE_BYTES:
                        raise ValueError('文件超过20MB。')
                    output.write(chunk)
            if not size:
                raise ValueError('文件不能为空。')
            reauthenticate(request)
            # Attachment parser gets an immutable owned copy before replacement.
            attachment_id=attachment_manager(owner(request),current['id']).add(name,part.filename)
            os.replace(name,destination)
            with store.db:
                store.session_event(owner(request),current['id'],'file_uploaded',{'name':destination.name,'attachment_id':attachment_id})
            return web.json_response({'name':destination.name,'size':size,'attachment_id':attachment_id},status=201)
        finally:
            Path(name).unlink(missing_ok=True)

    def media_response(request,path):
        response=web.FileResponse(path)
        response.content_type='application/octet-stream'
        response.headers['Content-Disposition']='attachment'
        if request.query.get('preview')=='1':
            mime=media_type(path)
            if not mime:
                raise web.HTTPUnsupportedMediaType(text='此文件不支持预览，请下载后查看。')
            response.content_type=mime
            response.headers['Content-Disposition']='inline'
            response.headers['Content-Security-Policy']="default-src 'none'; sandbox; frame-ancestors 'none'"
        response.headers['Cross-Origin-Resource-Policy']='same-origin'
        return response

    async def file_download(request):
        current=session(request)
        if request.method=='DELETE':
            store.delete_workspace_file(owner(request),current['id'],request.match_info['name'])
            return web.json_response({'deleted':True})
        path=store.workspace_file(owner(request),current['id'],request.match_info['name'])
        if not path.is_file():
            raise FileNotFoundError()
        return media_response(request,path)

    async def artifact(request):
        job=store.get_session_job(owner(request),session(request)['id'],request.match_info['jid'])
        value=job.get('result') if job['kind'] in ('image','video') else screen_artifact_path(store,job) or job.get('voice_result')
        if not value:
            raise FileNotFoundError()
        path=Path(value)
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError()
        if request.query.get('thumbnail') == '1':
            async with thumbnail_slots:
                data, mime = await asyncio.to_thread(image_thumbnail, path)
            # Thumbnail work crosses an await: recheck revocation before delivery.
            reauthenticate(request)
            return web.Response(body=data, content_type=mime, headers={
                'Content-Disposition': 'inline', 'Cross-Origin-Resource-Policy': 'same-origin',
                'Content-Security-Policy': "default-src 'none'; sandbox; frame-ancestors 'none'"})
        return media_response(request,path)

    async def approvals(request):
        current=session(request)
        if request.method=='GET':
            return web.json_response({'items':service.approvals.list_for_session(owner(request),current['id'])})
        data=await body(request)
        return web.json_response(service.approvals.decide(owner(request),current['id'],request.match_info['token'],data.get('decision')))

    def agent():
        if not getattr(service,'agent',None):
            raise web.HTTPNotFound(text='新版 agent 未开启。')
        return service.agent

    async def memory(request):
        from .agent.memory import Memory,LIMITS
        current=Memory(agent().memory_root,owner(request))
        if request.method=='POST':
            data=await body(request)
            current.apply(data.get('action'),data.get('target'),data.get('text'),data.get('old_text'))
        return web.json_response({'user':current.entries('user'),'memory':current.entries('memory'),'limits':LIMITS})

    async def home(request):
        from .home import summary
        agent_service=getattr(service,'agent',None)
        items=agent_service.schedules.list(owner(request)) if agent_service else ()
        return web.json_response(summary(store,owner(request),service.engine,items))

    async def schedules(request):
        return web.json_response({'items':agent().schedules.list(owner(request))})

    async def one_schedule(request):
        items=agent().schedules
        if request.method=='DELETE':
            return web.json_response(items.delete(owner(request),request.match_info['tid']))
        data=await body(request)
        if not isinstance(data.get('enabled'),bool):
            raise ValueError('enabled 须为 true 或 false。')
        return web.json_response(items.set_enabled(owner(request),request.match_info['tid'],data['enabled']))

    # -- short drama projects --------------------------------------------
    def drama_args(request):
        return owner(request),request.match_info['pid']

    async def dramas(request):
        if request.method=='GET':
            return web.json_response({'items':store.dramas.list(owner(request))})
        data=await body(request)
        origin=data.get('origin_session_id')
        if origin:
            store.require_session(owner(request),origin)
        return web.json_response(store.dramas.create(owner(request),data.get('title'),data.get('script'),
                                                     origin_session_id=origin,resolution=data.get('resolution','sd')))

    async def drama(request):
        account,pid=drama_args(request)
        if request.method=='GET':
            return web.json_response(store.dramas.view(account,pid))
        if request.method=='DELETE':
            project=store.dramas.project(account,pid)
            stop_session_jobs(project['session_id'])
            store.delete_session(account,project['session_id'])
            return web.json_response({'deleted':True,'id':pid})
        data=await body(request)
        return web.json_response(store.dramas.update_project(account,pid,data.get('revision'),data))

    async def drama_plan(request):
        account,pid=drama_args(request)
        data=await body(request)
        return web.json_response(store.dramas.request_plan(account,pid,data.get('op'),data.get('scene_ids')))

    async def drama_character(request):
        account,pid=drama_args(request)
        data=await body(request)
        return web.json_response(store.dramas.update_character(account,pid,data.get('revision'),request.match_info['cid'],data))

    async def drama_scene(request):
        account,pid=drama_args(request)
        data=await body(request)
        return web.json_response(store.dramas.update_scene(account,pid,data.get('revision'),request.match_info['scid'],data))

    async def drama_shots(request):
        account,pid=drama_args(request)
        data=await body(request)
        return web.json_response(store.dramas.add_shot(account,pid,data.get('revision'),data.get('scene_id'),data.get('after')))

    async def drama_shot(request):
        account,pid=drama_args(request)
        shid=request.match_info['shid']
        if request.method=='DELETE':
            return web.json_response(store.dramas.delete_shot(account,pid,int(request.query.get('revision','0')),shid))
        data=await body(request)
        return web.json_response(store.dramas.update_shot(account,pid,data.get('revision'),shid,data))

    async def drama_select(request):
        account,pid=drama_args(request)
        data=await body(request)
        return web.json_response(store.dramas.select_take(account,pid,data.get('revision'),request.match_info['shid'],data.get('take_id')))

    async def drama_takes(request):
        account,pid=drama_args(request)
        data=await body(request)
        return web.json_response(store.dramas.submit(account,pid,data.get('kind'),data.get('shot_ids'),data.get('confirm') is True))

    async def drama_cancel(request):
        account,pid=drama_args(request)
        for jid in store.dramas.cancel(account,pid):
            await service.cancel_job(jid)
        return web.json_response(store.dramas.view(account,pid))

    async def drama_exports(request):
        account,pid=drama_args(request)
        data=await body(request)
        return web.json_response(store.dramas.request_export(account,pid,data.get('kind'),data.get('options')))

    async def drama_take_file(request):
        account,pid=drama_args(request)
        return media_response(request,Path(store.dramas.take_file(account,pid,request.match_info['tid'])))

    async def drama_export_file(request):
        account,pid=drama_args(request)
        return media_response(request,Path(store.dramas.export_file(account,pid,request.match_info['eid'])))

    async def events(request):
        return web.json_response(store.session_events(owner(request),int(request.query.get('after','0'))))

    async def connections(request):
        return web.json_response({'items':store.connectors.list_connections(owner(request))})

    async def bindings(request):
        current=session(request)
        if request.method=='GET':
            result=store.connectors.list_bindings(owner(request),current['id'])
        else:
            data=await body(request)
            result=store.connectors.set_bindings(owner(request),current['id'],data.get('bindings'),data.get('revision'))
        return web.json_response({'items':result['bindings'],'revision':result['revision']})

    async def authorize_google(request):
        return web.json_response(store.connectors.oauth_start(owner(request)))

    async def start_picker(request):
        data=await body(request)
        return web.json_response(picker.start(owner(request),data.get('session_id'),request.match_info['cid'],data.get('revision'),request['device']['id']))

    async def picker_page(request):
        result=await picker.page(request.query.get('ticket'))
        return web.Response(text=result['html'],content_type='text/html',headers=result['headers'])

    async def picker_selection(request):
        data=await body(request)
        result=await picker.select(data.get('ticket'),data.get('ids'))
        return web.json_response({'items':result['bindings'],'revision':result['revision']})

    async def oauth_callback(request):
        await store.connectors.oauth_exchange(request.query.get('state'),request.query.get('code'))
        return web.Response(text='Google 已连接。请返回 App，刷新并为工作空间选择可访问的文件。',content_type='text/plain')

    async def disconnect(request):
        store.connectors.disconnect(owner(request),request.match_info['cid'])
        return web.json_response({'disconnected':True})

    async def host_status(request):
        return web.json_response(store.maintenance.status(owner(request),session(request)['id']))

    async def create_voice(request):
        current=session(request)
        description=await voice.create(owner(request),current['id'])
        try:
            reauthenticate(request)
        except PermissionError:
            await voice.end(owner(request), description['id'])
            raise
        voice_devices.clear()
        voice_devices[description['id']]=request['device']['id']
        return web.json_response(description,status=201)

    async def voice_stream(request):
        vid=request.match_info['vid']
        if voice_devices.get(vid)!=request['device']['id']:
            raise PermissionError('通话不属于此设备。')
        request['voice_authorize'] = lambda: reauthenticate(request)
        try:
            return await voice.stream(owner(request),vid,request)
        finally:
            voice_devices.pop(vid,None)

    async def end_voice(request):
        vid=request.match_info['vid']
        if voice_devices.get(vid)!=request['device']['id']:
            raise PermissionError('通话不属于此设备。')
        try:
            await voice.end(owner(request),vid)
        finally:
            voice_devices.pop(vid,None)
        return web.json_response({'ended':True})

    async def devices(request):
        return web.json_response({'items':auth.devices(owner(request))})

    async def revoke_device(request):
        did=request.match_info['did'];auth.revoke(owner(request),did)
        # Foreign IDs must never terminate another account's live call.
        row=store.db.execute('SELECT account_id,revoked FROM mobile_devices WHERE id=?',(did,)).fetchone()
        if row and row['account_id'] == owner(request) and row['revoked']:
            await close_device_voice(did)
        return web.json_response({'revoked':True})

    async def renew(request):
        if request.get('browser_token'):
            raise PermissionError('网页登录到期后请重新验证通行密钥。')
        return web.json_response(auth.renew(owner(request),request['device']['id']))

    async def notification_status(request):
        device = request['device']
        configured = all(os.environ.get(key) for key in ('APNS_TEAM_ID', 'APNS_KEY_ID', 'APNS_KEY_FILE', 'APNS_BUNDLE_ID'))
        return web.json_response({'server_configured': configured,
                                  'device_registered': bool(device.get('push_token'))})

    async def push(request):
        data=await body(request)
        auth.register_push(owner(request),request.match_info['did'],data.get('token'),data.get('environment'))
        return web.json_response({'registered':True})

    if browser:
        pwa = PWARoutes(browser, body, close_device_voice)
        pwa.install(app)
        if native_enabled:
            from .native_auth import NativeAuthRoutes
            native_routes = NativeAuthRoutes(store, browser, pwa, body)
            native_routes.install(app)

    app.add_routes([
        web.get('/healthz',health),web.get('/v1/home',home),web.post('/v1/pair',pair),
        web.get('/v1/sessions',sessions),web.post('/v1/sessions',sessions),
        web.get('/v1/sessions/{sid}',one_session),web.patch('/v1/sessions/{sid}',one_session),web.delete('/v1/sessions/{sid}',one_session),
        web.get('/v1/trash',trash),web.delete('/v1/trash',trash),web.post('/v1/trash/{sid}/restore',trash_item),web.delete('/v1/trash/{sid}',trash_item),
        web.delete('/v1/sessions/{sid}/tasks/{jid}',delete_task),web.delete('/v1/sessions/{sid}/files/{name}',file_download),
        web.get('/v1/sessions/{sid}/evolution',evolution),web.post('/v1/sessions/{sid}/evolution',evolution),
        web.get('/v1/sessions/{sid}/evolution/{rid}',evolution_revision),web.post('/v1/sessions/{sid}/evolution/{rid}',evolution_revision),
        web.get('/v1/sessions/{sid}/mcp',mcp_bindings),web.put('/v1/sessions/{sid}/mcp',mcp_bindings),
        web.get('/v1/skills',skills),web.put('/v1/sessions/{sid}/skills',session_skills),
        web.get('/v1/sessions/{sid}/messages',messages),web.post('/v1/sessions/{sid}/messages',messages),
        web.get('/v1/sessions/{sid}/stream',stream),web.get('/v1/agents',agents),web.post('/v1/agents/suggest',suggest_agent),
        web.post('/v1/sessions/{sid}/voice-messages',voice_message),
        web.get('/v1/sessions/{sid}/tasks/{jid}/runs',runs),
        web.get('/v1/tasks',tasks),web.get('/v1/sessions/{sid}/tasks',tasks),
        web.post('/v1/sessions/{sid}/tasks/{jid}/cancel',cancel),web.post('/v1/sessions/{sid}/reset',reset),
        web.post('/v1/sessions/{sid}/fork',fork),web.get('/v1/sessions/{sid}/files',files),web.post('/v1/sessions/{sid}/files',files),
        web.get('/v1/sessions/{sid}/files/{name}',file_download),web.get('/v1/sessions/{sid}/tasks/{jid}/artifact',artifact),
        web.get('/v1/sessions/{sid}/approvals',approvals),web.post('/v1/sessions/{sid}/approvals/{token}',approvals),
        web.get('/v1/events',events),web.get('/v1/memory',memory),web.post('/v1/memory',memory),
        web.get('/v1/dramas',dramas),web.post('/v1/dramas',dramas),
        web.get('/v1/dramas/{pid}',drama),web.patch('/v1/dramas/{pid}',drama),web.delete('/v1/dramas/{pid}',drama),
        web.post('/v1/dramas/{pid}/plan',drama_plan),web.put('/v1/dramas/{pid}/characters/{cid}',drama_character),
        web.put('/v1/dramas/{pid}/scenes/{scid}',drama_scene),web.post('/v1/dramas/{pid}/shots',drama_shots),
        web.patch('/v1/dramas/{pid}/shots/{shid}',drama_shot),web.delete('/v1/dramas/{pid}/shots/{shid}',drama_shot),
        web.put('/v1/dramas/{pid}/shots/{shid}/selected',drama_select),web.post('/v1/dramas/{pid}/takes',drama_takes),
        web.post('/v1/dramas/{pid}/cancel',drama_cancel),web.post('/v1/dramas/{pid}/exports',drama_exports),
        web.get('/v1/dramas/{pid}/takes/{tid}/file',drama_take_file),web.get('/v1/dramas/{pid}/exports/{eid}/file',drama_export_file),
        web.get('/v1/schedules',schedules),web.patch('/v1/schedules/{tid}',one_schedule),web.delete('/v1/schedules/{tid}',one_schedule),web.get('/v1/connectors',connections),
        web.get('/v1/sessions/{sid}/connectors',bindings),web.put('/v1/sessions/{sid}/connectors',bindings),
        web.post('/v1/connectors/google/authorize',authorize_google),web.get('/v1/oauth/google/callback',oauth_callback),
        web.post('/v1/connectors/{cid}/picker',start_picker),web.get('/v1/google/picker',picker_page),web.post('/v1/google/picker/selection',picker_selection),
        web.delete('/v1/connectors/{cid}',disconnect),web.get('/v1/sessions/{sid}/host/status',host_status),
        web.post('/v1/sessions/{sid}/voice',create_voice),web.get('/v1/voice/{vid}/stream',voice_stream),web.delete('/v1/voice/{vid}',end_voice),
        web.get('/v1/notifications/status',notification_status),web.get('/v1/devices',devices),web.delete('/v1/devices/{did}',revoke_device),web.post('/v1/devices/{did}/push',push),web.post('/v1/auth/renew',renew),
    ])

    async def background(app):
        from .notifications import APNsNotifier
        notifier=APNsNotifier(store)
        task=asyncio.create_task(notifier.run())
        async def check_voice_device():
            while True:
                await asyncio.sleep(2)
                if not voice.active:
                    voice_devices.clear()
                for vid,did in list(voice_devices.items()):
                    device=store.db.execute('SELECT d.*,a.enabled FROM mobile_devices d JOIN accounts a ON a.id=d.account_id WHERE d.id=?',(did,)).fetchone()
                    if not device or device['revoked'] or not device['enabled'] or device['expires']<=time.time() or (browser and not browser.device_active(did)):
                        with contextlib.suppress(VoiceError):
                            if device:
                                await voice.end(device['account_id'],vid)
                        voice_devices.pop(vid,None)
        reaper=asyncio.create_task(check_voice_device())
        try:
            yield
        finally:
            task.cancel()
            reaper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            with contextlib.suppress(asyncio.CancelledError):
                await reaper
            await voice.close()
    app.cleanup_ctx.append(background)
    return app
