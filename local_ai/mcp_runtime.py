"""Real stdio MCP, pinned per task; only owner-approved local code is executable."""
import asyncio
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

from jsonschema import Draft202012Validator
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .tool_errors import ToolFailure


class MCPRuntime:
    def __init__(self,store):
        self.store=store
        self.evolution=store.evolution

    async def exchange(self,item,workspace,tool=None,arguments=None):
        directory=self.evolution.materialize(item)
        workspace.mkdir(parents=True,exist_ok=True)
        # Never inherit the bot token, connector keys, proxy credentials or .env.
        params=StdioServerParameters(command=sys.executable,args=['-I',str(directory/'server.py')],
            cwd=str(workspace),env={'PATH':'/usr/bin:/bin','PYTHONNOUSERSITE':'1',
                                   'FRIDAY_WORKSPACE':str(workspace)})
        try:
            async with asyncio.timeout(25):
                with open(os.devnull,'w') as errors:
                    async with stdio_client(params,errlog=errors) as (read,write):
                        async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=20)) as client:
                            await client.initialize()
                            tools=[];cursor=None
                            for _ in range(5):
                                page=await client.list_tools(cursor=cursor)
                                tools.extend(page.tools)
                                cursor=page.nextCursor
                                if not cursor:break
                            if cursor or len(tools)>100:raise ValueError('MCP 工具数量超出限制。')
                            definitions=[]
                            for entry in tools:
                                schema=entry.inputSchema
                                Draft202012Validator.check_schema(schema)
                                # No implicit remote JSON-schema fetches while validating arguments.
                                def check_refs(node):
                                    if isinstance(node,dict):
                                        for key,value in node.items():
                                            if key in ('$ref','$dynamicRef') and (not isinstance(value,str) or not value.startswith('#')):raise ValueError('工具 schema 只允许本地引用。')
                                            check_refs(value)
                                    elif isinstance(node,list):
                                        for value in node:check_refs(value)
                                check_refs(schema)
                                definitions.append({'name':entry.name,'description':entry.description or '', 'inputSchema':schema})
                            if not tool:
                                if len(json.dumps(definitions))>48000:raise ValueError('MCP 工具定义过大。')
                                return definitions
                            spec=next((d for d in definitions if d['name']==tool),None)
                            if not spec:raise ValueError('该 MCP 没有此工具。')
                            if not isinstance(arguments,dict):raise ValueError('工具参数必须是对象。')
                            Draft202012Validator(spec['inputSchema']).validate(arguments)
                            result=await client.call_tool(tool,arguments)
                            if result.isError:raise ToolFailure('mcp_tool_error','MCP 工具报告失败；请检查实现，不能宣称成功。')
                            value=result.model_dump(mode='json',exclude_none=True,by_alias=True)
                            if len(json.dumps(value,ensure_ascii=False))>24000:raise ToolFailure('mcp_result_too_large','MCP 返回过大，请让工具分页或返回工作空间文件。')
                            return {'result':value,'untrusted_content':True,'version':item['id']}
        except (asyncio.CancelledError,):raise
        except Exception as exc:
            # ExceptionGroups and stderr can contain credentials emitted by external code.
            raise ToolFailure('mcp_failed','MCP 连接或调用失败（'+type(exc).__name__+'）；没有自动重试。',retryable=False,uncertain=True) from None

    async def probe(self,item):
        self.evolution.allow(item['account_id'],item['session_id'])
        return await self.exchange(item,self.evolution.root/'validation'/item['id'])

    async def for_job(self,job,name,tool=None,arguments=None):
        aid,sid=job['account_id'],job['session_id']
        self.evolution.allow(aid,sid)
        if self.evolution.draining():raise ValueError('正在准备升级，请稍后再调用 MCP。')
        binding=self.evolution.bindings(aid,sid)
        if name not in binding['names']:raise PermissionError('当前会话未启用此 MCP，或已撤销。')
        version=self.evolution.capture(job['id']).get(name)
        if not version:raise PermissionError('此任务未包含该 MCP，请新建任务使用新能力。')
        row=self.store.db.execute("SELECT * FROM evolution_revisions WHERE id=? AND kind='mcp' AND state='published'",(version,)).fetchone()
        if not row:raise PermissionError('MCP 版本不可用。')
        item=dict(row);item['files']=json.loads(item['files'])
        workspace=self.store.workspace_directory(aid,sid)
        result=await self.exchange(item,workspace,tool,arguments)
        self.evolution.allow(aid,sid)
        if name not in self.evolution.bindings(aid,sid)['names']:raise PermissionError('MCP 授权已撤销。')
        return result
