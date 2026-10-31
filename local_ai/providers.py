"""Typed read-only API adapters; host credentials never enter model context."""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp

from .capabilities import Capability, string
from .tool_errors import ToolFailure
from .web_tools import PublicResolver, public_url


async def fetch_json(url, *, params=None, headers=None):
    public_url(url)
    connector = aiohttp.TCPConnector(resolver=PublicResolver(),use_dns_cache=False)
    async with aiohttp.ClientSession(connector=connector,trust_env=False,
            timeout=aiohttp.ClientTimeout(total=30)) as client:
        # No automatic redirects: particularly important for authenticated providers.
        async with client.get(url,params=params,headers=headers,allow_redirects=False) as response:
            if response.status!=200:
                raise ToolFailure(f'http_{response.status}',f'API 返回 HTTP {response.status}。',
                    retryable=response.status==429 or response.status>=500,requires_user=response.status in (401,403))
            data=bytearray()
            async for block in response.content.iter_chunked(65536):
                data.extend(block)
                if len(data)>1_000_000:
                    raise ToolFailure('response_too_large','API 返回超过1MB，请缩小查询或使用接口分页。')
            try:
                result=json.loads(data)
            except (ValueError,UnicodeError):
                raise ToolFailure('invalid_provider_response','接口未返回有效 JSON。') from None
            return {'data':result,'source':str(response.url) if not headers else url,
                    'retrieved_at':datetime.now(timezone.utc).isoformat(),'untrusted_content':True}


def project_result(result, pointer='', offset=0):
    data=result['data']
    if pointer:
        if not pointer.startswith('/'):
            raise ValueError('JSON pointer 必须以 / 开始。')
        try:
            for token in pointer[1:].split('/'):
                token=token.replace('~1','/').replace('~0','~')
                data=data[int(token)] if isinstance(data,list) else data[token]
        except (KeyError,ValueError,TypeError,IndexError):
            raise ToolFailure('field_not_found','API 数据中不存在指定字段。') from None
    if isinstance(data,list):
        page=data[offset:offset+30]
        if len(json.dumps(page,ensure_ascii=False))>24000:
            raise ToolFailure('response_too_large','此页数据过大，请选择更具体的 JSON pointer。')
        return {**result,'data':page,'total':len(data),'next_offset':offset+30 if offset+30<len(data) else None}
    encoded=json.dumps(data,ensure_ascii=False)
    if len(encoded)>24000:
        return {**result,'data':None,'truncated':True,'keys':list(data)[:100] if isinstance(data,dict) else [],
                'hint':'返回对象过大。用 JSON pointer 选择具体字段；不能把字段名当作字段值。'}
    return {**result,'data':data,'truncated':False}


class Providers:
    def __init__(self, config):
        self.entries={}
        if not config.exists():return
        if config.is_symlink() or config.stat().st_mode & 0o022:
            raise PermissionError('API 配置必须由本机账号独占写入。')
        for item in json.loads(config.read_text()):
            name=item['name']
            if item.get('access','user') not in ('user','admin') or not isinstance(item.get('description'),str) or not item['description'].strip():
                raise ValueError('Invalid provider metadata or access')
            if not re.fullmatch(r'api_[a-z][a-z0-9_]{1,50}',name) or item.get('method','GET')!='GET':
                raise ValueError('Provider 必须是具名只读 GET 适配器。')
            public_url(item['url'])
            if item.get('secret_env') and not re.fullmatch(r'PROVIDER_[A-Z0-9_]+',item['secret_env']):
                raise ValueError('Provider 只能引用专用 PROVIDER_ 环境变量。')
            self.entries[name]=item

    def register(self, registry):
        for name,item in self.entries.items():
            access=item.get('access','admin' if item.get('secret_env') else 'user')
            if access not in ('user','admin'):raise ValueError('Invalid provider access')
            spec=Capability(name,item['description'],item.get('properties',{}),tuple(item.get('required',[])),access=access,group='api')
            def available(item=item):
                ready=not item.get('secret_env') or bool(os.getenv(item['secret_env']))
                return ready,None if ready else '尚未配置此API专用凭据'
            registry.register(spec,self.execute,available)

    async def execute(self,name,args):
        item=self.entries[name]
        headers={'Authorization':'Bearer '+os.environ[item['secret_env']]} if item.get('secret_env') else None
        result=await fetch_json(item['url'],params={**item.get('defaults',{}),**args},headers=headers)
        if item.get('secret_env'):
            result=json.loads(json.dumps(result,ensure_ascii=False).replace(os.environ[item['secret_env']],'[REDACTED]'))
        return project_result(result)


class Skills:
    def __init__(self, root):self.root=root.resolve()

    @staticmethod
    def description(text):
        if text.startswith('---\n'):
            front=text.split('---',2)[1]
            found=re.search(r'^description:\s*(.+)$',front,re.M)
            if found and found[1].strip() not in ('>','|'):
                return found[1].strip().strip('"\'')[:250]
        return text.split('\n',1)[0][:250]

    def list(self):
        if not self.root.exists():return []
        result=[]
        for p in sorted(self.root.glob('*/SKILL.md')):
            if p.is_symlink() or not p.resolve().is_relative_to(self.root):continue
            text=p.read_text()[:1000]
            result.append({'name':p.parent.name,'description':self.description(text)})
        return result

    def read(self,name):
        if not re.fullmatch(r'[a-z0-9_-]{1,80}',name):raise ValueError('无效技能名称。')
        path=self.root/name/'SKILL.md'
        if not path.resolve().is_relative_to(self.root) or path.is_symlink():raise PermissionError('技能路径越界。')
        text=path.read_text()
        if len(text)>16000:raise ValueError('技能文档超过16000字符。')
        return {'name':name,'text':text,'authority':'guidance_only','permissions':'unchanged'}
