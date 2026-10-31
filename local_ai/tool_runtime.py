"""Admit, journal, execute and settle one tool call under host authority."""
import asyncio
import json
import time

from .capabilities import validate
from .tool_errors import ToolFailure, classify_error


class ToolRuntime:
    def __init__(self, store, job, registry, allowed):
        self.store, self.job, self.registry, self.allowed = store, job, registry, allowed

    async def call(self, call_id, name, arguments):
        if not self.allowed(self.job['user_id']):
            raise PermissionError('用户授权已撤销。')
        if self.store.get(self.job['id'])['status'] == 'cancelled':
            raise asyncio.CancelledError()
        spec = self.registry.spec(name)
        validate(arguments, spec.parameters)
        old=self.store.operation(self.job['id'],call_id)
        reconcile=self.registry.reconcilers.get(name)
        if old and reconcile and old['status'] in ('started','uncertain') and old['name']==name and json.loads(old['arguments'])==arguments:
            verified=await reconcile(name,arguments)
            if verified is not None:self.store.settle_verified_operation(self.job['id'],call_id,verified)
        decision = self.store.begin_operation(self.job['id'], call_id, spec, arguments)
        if decision['cached']:
            return decision
        result, failure = None, None
        try:
            result = await self.registry.execute(name, arguments)
        except asyncio.CancelledError:
            # Leave started in the journal: restarting can replay reads, while a
            # write needs reconciliation. Do not call cancellation a clean failure.
            raise
        except Exception as error:
            failure = classify_error(error).payload()
            if spec.replay != 'safe' and failure['retryable']:
                failure.update(code='effect_uncertain', retryable=False, uncertain=True,
                               message='操作连接中断，结果尚不确定。先检查真实结果，不能直接重复。')
        self.store.finish_operation(self.job['id'], call_id, result=result, failure=failure)
        # Compatibility projection for existing task_status/media evidence. The
        # operation journal, not this presentation log, is the execution authority.
        if name not in {'generate_image', 'generate_video'} or failure:
            self.store.db.execute('INSERT INTO pi_tools(job_id,user_id,name,args,result,error,created) VALUES (?,?,?,?,?,?,?)',
                                  (self.job['id'], self.job['user_id'], name, json.dumps(arguments, ensure_ascii=False),
                                   json.dumps(result, ensure_ascii=False), failure['message'] if failure else None, time.time()))
            self.store.db.commit()
        return {'cached': False, 'result': result, 'failure': failure}

    async def repair_checkpoint(self, messages):
        """Settle only tool calls missing their paired results after a crash.

        Calls not yet admitted can execute. Previously successful calls use the
        receipt. Unknown mutation outcomes become explicit errors, never repeats.
        """
        paired = {m.get('toolCallId') for m in messages if m.get('role') == 'toolResult'}
        repaired = []
        for message in messages:
            repaired.append(message)
            if message.get('role') != 'assistant':
                continue
            for part in message.get('content', []):
                if part.get('type') != 'toolCall' or part['id'] in paired:
                    continue
                try:
                    old=self.store.operation(self.job['id'],part['id'])
                    if self.store.pending_inputs(self.job) and (not old or old['status']=='waiting_approval' or (old['status']=='started' and old['replay']=='safe')):
                        raise ToolFailure('task_updated','用户已有补充，先处理新输入再决定此项操作。')
                    outcome = await self.call(part['id'], part['name'], part.get('arguments', {}))
                    failure = outcome['failure']
                    content = {'error': failure} if failure else outcome['result']
                except Exception as exc:
                    failure = classify_error(exc).payload()
                    content = {'error': failure}
                repaired.append({'role': 'toolResult', 'toolCallId': part['id'], 'toolName': part['name'],
                                 'content': [{'type': 'text', 'text': json.dumps(content, ensure_ascii=False)}],
                                 'isError': bool(failure), 'timestamp': int(time.time()*1000)})
                paired.add(part['id'])
        return repaired
