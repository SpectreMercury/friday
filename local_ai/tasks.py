"""User requests are immutable; execution attempts and revisable plans are separate."""
import json
import hashlib
import re
import time


def _message_text(message):
    content = message.get('content', '')
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(part['text'] for part in content
                         if isinstance(part, dict) and part.get('type') == 'text' and isinstance(part.get('text'), str))
    return ''


class Tasks:
    def init_tasks(self):
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, user_id INTEGER,
                chat_id INTEGER, created REAL, outcome TEXT DEFAULT 'pending');
            CREATE TABLE IF NOT EXISTS task_inputs(job_id TEXT PRIMARY KEY, task_id TEXT,
                text TEXT NOT NULL, created REAL, consumed INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS plans(id INTEGER PRIMARY KEY, task_id TEXT, job_id TEXT,
                content TEXT NOT NULL, created REAL);
            CREATE TABLE IF NOT EXISTS inbound_updates(update_id INTEGER PRIMARY KEY,job_id TEXT,
                chat_id INTEGER,message_id INTEGER);
        ''')
        columns = {r[1] for r in self.db.execute('PRAGMA table_info(jobs)')}
        if 'task_id' not in columns:
            self.db.execute('ALTER TABLE jobs ADD COLUMN task_id TEXT')
        self.db.execute('UPDATE jobs SET task_id=id WHERE task_id IS NULL')
        self.db.execute('INSERT OR IGNORE INTO tasks(id,user_id,chat_id,created) SELECT id,user_id,chat_id,created FROM jobs')
        self.db.execute("INSERT OR IGNORE INTO task_inputs(job_id,task_id,text,created,consumed) SELECT id,task_id,prompt,created,1 FROM jobs")
        self.db.commit()

    def task_facts(self, job):
        current = self.get(job['id'])
        if not current or (current['account_id'], current['session_id']) != (job['account_id'], job['session_id']):
            raise PermissionError('不能读取其他用户或会话的任务事实。')
        task_id = current.get('task_id') or job['id']
        inputs = [dict(r) for r in self.db.execute('''SELECT i.job_id,i.text,i.created,j.status,j.outcome
            FROM task_inputs i JOIN jobs j ON j.id=i.job_id
            WHERE i.task_id=? AND j.task_id=? AND j.account_id=? AND j.session_id=? ORDER BY i.created''',
            (task_id,task_id,current['account_id'],current['session_id']))]
        plans = [json.loads(r[0]) for r in self.db.execute('''SELECT p.content FROM plans p
            JOIN jobs j ON j.id=p.job_id WHERE p.task_id=? AND j.task_id=?
            AND j.account_id=? AND j.session_id=? ORDER BY p.id DESC LIMIT 3''',
            (task_id,task_id,current['account_id'],current['session_id']))]
        from .execution import plan_progress
        return {'task_id': task_id, 'user_requests': inputs, 'plans': list(reversed(plans)),
                **({'plan_progress': plan_progress(plans[0])} if plans and 'work_items' in plans[0] else {})}

    def link_task(self, job, previous_id):
        current = self.get(job['id'])
        previous = self.get(previous_id)
        if (not current or not previous or
                (current['account_id'], current['session_id']) != (job['account_id'], job['session_id']) or
                (previous['account_id'], previous['session_id']) != (current['account_id'], current['session_id'])):
            raise PermissionError('不能续接其他用户或会话的任务。')
        reset = self.db.execute('SELECT reset_at FROM session_resets WHERE session_id=?',(current['session_id'],)).fetchone()
        if previous['created'] > current['created'] or (reset and previous['created'] <= reset[0]):
            raise ValueError('只能续接当前上下文中已存在的请求。')
        old = current['task_id']
        target = previous['task_id']
        with self.db:
            # Snapshot owned job IDs before updating any task association. A
            # malformed shared task_id must never move another tenant's rows.
            owned = [row['id'] for row in self.db.execute('''SELECT id FROM jobs
                WHERE task_id=? AND account_id=? AND session_id=?''',
                (old,current['account_id'],current['session_id']))]
            self.db.executemany('UPDATE task_inputs SET task_id=? WHERE job_id=? AND task_id=?',
                                [(target,jid,old) for jid in owned])
            self.db.executemany('UPDATE plans SET task_id=? WHERE job_id=? AND task_id=?',
                                [(target,jid,old) for jid in owned])
            self.db.executemany('''UPDATE task_runs SET task_id=? WHERE job_id=? AND task_id=?
                AND account_id=? AND session_id=?''',
                [(target,jid,old,current['account_id'],current['session_id']) for jid in owned])
            self.db.executemany('''UPDATE jobs SET task_id=? WHERE id=? AND task_id=?
                AND account_id=? AND session_id=?''',
                [(target,jid,old,current['account_id'],current['session_id']) for jid in owned])
        return self.task_facts(job)

    def revise_plan(self, job, plan):
        facts = self.task_facts(job)
        from .execution import validate_plan, validate_plan_evidence, plan_progress
        # Old internal snapshots can predate the model-facing mode/steps schema.
        # Validate every new structured plan while preserving those records.
        if 'work_items' in plan:
            plan = validate_plan(plan)
        validate_plan_evidence(self, job, facts, plan)
        with self.db:
            self.db.execute('INSERT INTO plans(task_id,job_id,content,created) VALUES (?,?,?,?)',
                (facts['task_id'],job['id'],json.dumps(plan,ensure_ascii=False),time.time()))
        return {**plan,'revision':self.db.execute('SELECT count(*) FROM plans WHERE task_id=?',(facts['task_id'],)).fetchone()[0],
                'user_requests':facts['user_requests'],
                **({'progress': plan_progress(plan)} if 'work_items' in plan else {})}

    def accepted_answer(self, source):
        """Return a persisted conversational answer, never a host failure notice.

        Both checkpoint projection and legacy history fallback use this rule.
        Successful legacy jobs without a review remain readable; reviewed jobs
        require acceptance, including valid partial and needs-input answers.
        """
        result = source.get('result')
        if (source.get('status') != 'succeeded' or source.get('outcome') == 'continued'
                or not isinstance(result, str) or not result.strip()):
            return None
        review = self.db.execute("SELECT result FROM pi_tools WHERE job_id=? AND name='final_check' ORDER BY id DESC LIMIT 1",
                                 (source['id'],)).fetchone()
        if review:
            try:
                verdict = json.loads(review[0] or 'null')
            except (ValueError, TypeError):
                return None
            if not isinstance(verdict, dict) or verdict.get('accepted') is not True:
                return None
            # New reviews bind acceptance to an exact candidate. A later host
            # notice must not inherit an earlier candidate's acceptance.
            checked = verdict.get('answer')
            if isinstance(checked, str) and checked.strip() != result.strip():
                return None
        return result

    def conversation(self, job):
        """Project prior dialogue and evidence, never replay prior harness control.

        Same-job recovery deliberately uses checkpoint() instead. Checkpoints
        contain draft answers and host prompts as well as actual conversation.
        Only persisted inbound messages and delivered answers establish their
        conversational roles when a different job imports that transcript.
        """
        rows = self.db.execute('''SELECT c.messages,j.id FROM agent_checkpoints c JOIN jobs j ON j.id=c.job_id
            WHERE j.account_id=? AND j.session_id=? AND j.created<?
            AND j.created>COALESCE((SELECT reset_at FROM session_resets WHERE session_id=?),0)
            AND j.status IN ('succeeded','failed') AND j.id NOT IN (SELECT job_id FROM deleted_jobs) ORDER BY j.created DESC LIMIT 1''',
            (job['account_id'],job['session_id'],job['created'],job['session_id'])).fetchone()
        if not rows:
            return []
        messages = json.loads(rows[0])
        previous_job = rows[1]
        sources = {row['id']: dict(row) for row in self.db.execute('''
            SELECT j.*,i.text AS input_text FROM jobs j LEFT JOIN task_inputs i ON i.job_id=j.id
            WHERE j.account_id=? AND j.session_id=? AND j.created<?
            AND j.kind='chat' AND j.parent_job_id IS NULL AND j.id NOT IN (SELECT job_id FROM deleted_jobs)
            AND j.created>COALESCE((SELECT reset_at FROM session_resets WHERE session_id=?),0)
            ORDER BY j.created''', (job['account_id'], job['session_id'], job['created'], job['session_id']))}
        used_inputs, delivered = set(), set()

        def user_source(message):
            if message.get('source') in ('harness_control', 'reference'):
                return None
            text = _message_text(message)
            source_id = message.get('source_job_id')
            candidates = [sources[source_id]] if source_id in sources else []
            if source_id and not candidates:
                return None
            if not candidates:
                candidates = sources.values()
            for source in candidates:
                if source['id'] not in used_inputs and text in (source['prompt'], source['input_text']):
                    return source
            # Legacy steering added a host label and attachment metadata around
            # a real task_input. Validate both against the same session's rows.
            match = re.match(r'^\[用户补充 ([^\]]+)\] ', text)
            source = sources.get(match[1]) if match else None
            if not source or source['id'] in used_inputs or (source_id and source_id != source['id']):
                return None
            body = text[match.end():]
            expected = source['input_text']
            if body == expected:
                return source
            prefix = (expected or '') + '\n新附件（元数据）：'
            if body.startswith(prefix):
                try:
                    supplied = json.loads(body[len(prefix):])
                    actual = [dict(row) for row in self.db.execute(
                        'SELECT id,name,kind FROM attachments WHERE job_id=? AND session_id=?',
                        (source['id'], job['session_id']))]
                    if supplied == actual:
                        return source
                except (ValueError, TypeError):
                    pass
            return None

        accepted_answers = {}
        for source_id, source in sources.items():
            result = self.accepted_answer(source)
            if result is not None:
                accepted_answers[source_id] = result

        def answer_source(message):
            text = _message_text(message).strip()
            source_id = message.get('source_job_id')
            if source_id in accepted_answers and text == accepted_answers[source_id].strip():
                return source_id
            return next((sid for sid, answer in accepted_answers.items()
                         if sid not in delivered and text == answer.strip()), None)

        def delivered_message(source_id, template=None):
            answer = {**(template or {}), 'role': 'assistant',
                      'content': [{'type': 'text', 'text': accepted_answers[source_id]}],
                      'source': 'delivered_answer', 'source_job_id': source_id,
                      'stopReason': 'stop', 'timestamp': int(time.time()*1000)}
            answer.pop('errorMessage', None)
            return answer

        results = {message['toolCallId']: message for message in messages
                   if message.get('role') == 'toolResult' and message.get('toolCallId')}
        settled = []
        for message in messages:
            if message.get('role') == 'user':
                source = user_source(message)
                if source:
                    settled.append({**message, 'source': 'user', 'source_job_id': source['id']})
                    used_inputs.add(source['id'])
                continue
            if message.get('role') != 'assistant':
                continue
            parts = message.get('content', [])
            calls = [part for part in parts if isinstance(part, dict) and part.get('type') == 'toolCall'] if isinstance(parts, list) else []
            if not calls:
                source_id = answer_source(message)
                if source_id and source_id not in delivered:
                    settled.append(delivered_message(source_id, message))
                    delivered.add(source_id)
                continue
            source_id = message.get('source_job_id', previous_job)
            if source_id not in sources:
                continue
            settled.append({**message, 'content': calls, 'source': 'historical_evidence', 'source_job_id': source_id})
            for part in calls:
                source_call = part.setdefault('source_call_id', part['id'])
                result = results.get(part['id'])
                if result:
                    result = {**result}
                else:
                    # Interrupted old calls are settled from the old journal;
                    # they must never be executed under the new job's authority.
                    operation = self.operation(source_id, source_call)
                    known = operation and operation['status'] in ('succeeded', 'failed')
                    payload = json.loads(operation['result']) if known and operation['status'] == 'succeeded' else {
                        'previous_job_id': source_id, 'status': operation['status'] if operation else 'not_started',
                        'error': json.loads(operation['failure']) if known and operation['failure'] else '此前执行中断；这是历史记录，不能按新任务重放旧动作。'}
                    result = {'role': 'toolResult', 'toolCallId': part['id'], 'toolName': part['name'],
                              'content': [{'type': 'text', 'text': json.dumps(payload, ensure_ascii=False)}],
                              'isError': not known or operation['status'] != 'succeeded', 'timestamp': int(time.time()*1000)}
                if part.get('name') in ('read_skill', 'list_skills'):
                    result['content'] = [{'type': 'text', 'text': '历史技能正文已省略；请读取当前任务启用的技能版本。'}]
                settled.append({**result, 'source': 'historical_evidence', 'source_job_id': source_id,
                                'source_call_id': source_call})
        if previous_job in accepted_answers and previous_job not in delivered:
            template = next((m for m in reversed(messages) if m.get('role') == 'assistant'), None)
            settled.append(delivered_message(previous_job, template))
        def history_id(value):
            return 'history_'+hashlib.sha256((previous_job+':'+value).encode()).hexdigest()[:32]
        for message in settled:
            if message.get('role')=='toolResult':message['toolCallId']=history_id(message['toolCallId'])
            if message.get('role')=='assistant':
                for part in message.get('content',[]):
                    if part.get('type')=='toolCall':part['id']=history_id(part['id'])
        messages=settled
        # Bound old evidence previews, not the identity or content of user
        # messages. A long single turn must not erase the entire conversation.
        # The newest receipt and all full source checkpoints/journals remain.
        receipts = [m for m in messages if m['role'] == 'toolResult']
        for receipt in receipts[:-1]:
            if len(json.dumps(messages, ensure_ascii=False)) <= 28000:
                break
            text = _message_text(receipt)
            if len(text) <= 1400:
                continue
            receipt['content'] = [{'type': 'text', 'text': json.dumps({
                'historical_preview': True, 'source_job_id': receipt['source_job_id'],
                'source_call_id': receipt['source_call_id'], 'total_characters': len(text),
                'preview': text[:1000]}, ensure_ascii=False)}]
        return messages

    def absorb_input(self, active, incoming):
        """A classified correction joins the active task, without losing its source job."""
        if (active['account_id'],active['session_id']) != (incoming['account_id'],incoming['session_id']):
            raise PermissionError('不能跨工作空间关联输入。')
        with self.db:
            task_id = self.get(active['id'])['task_id']
            self.db.execute('UPDATE task_inputs SET task_id=?,consumed=0 WHERE job_id=?',(task_id,incoming['id']))
            self.db.execute("UPDATE jobs SET task_id=?,status='succeeded',outcome='continued',delivered=1,delivery_state='suppressed' WHERE id=? AND status='queued'",
                            (task_id,incoming['id']))
            self.db.execute('UPDATE task_runs SET task_id=? WHERE job_id=? AND account_id=? AND session_id=?',
                            (task_id,incoming['id'],active['account_id'],active['session_id']))

    def pending_inputs(self, job):
        current = self.get(job['id'])
        if not current or (current['account_id'], current['session_id']) != (job['account_id'], job['session_id']):
            raise PermissionError('不能读取其他用户或会话的输入。')
        return [dict(r) for r in self.db.execute('''SELECT i.job_id,i.text FROM task_inputs i
            JOIN jobs j ON j.id=i.job_id WHERE i.task_id=? AND j.task_id=?
            AND j.account_id=? AND j.session_id=? AND i.consumed=0 ORDER BY i.created''',
            (current['task_id'],current['task_id'],current['account_id'],current['session_id']))]

    def release_pending_inputs(self, job):
        """Return unconsumed absorbed inputs to the worker without changing them.

        A stopped old run cannot acknowledge a correction it never incorporated.
        The released job owns its original input on its next run, so it must no
        longer appear as unread steering for the old run of the same task.
        """
        current = self.get(job['id'])
        if not current or (current['account_id'], current['session_id']) != (job['account_id'], job['session_id']):
            raise PermissionError('不能释放其他用户或会话的输入。')
        released = []
        with self.db:
            rows = self.db.execute('''SELECT j.id FROM jobs j JOIN task_inputs i ON i.job_id=j.id
                WHERE j.account_id=? AND j.session_id=? AND j.task_id=? AND i.task_id=?
                AND j.id!=? AND i.consumed=0 AND j.status='succeeded'
                AND j.outcome='continued' AND j.delivery_state='suppressed' ORDER BY i.created''',
                (current['account_id'], current['session_id'], current['task_id'], current['task_id'], current['id'])).fetchall()
            for row in rows:
                updated = self.db.execute('''UPDATE jobs SET status='queued',outcome=NULL,result=NULL,
                    delivered=0,delivery_state='pending',error=NULL
                    WHERE id=? AND account_id=? AND session_id=? AND task_id=?
                    AND status='succeeded' AND outcome='continued' AND delivery_state='suppressed'
                    AND EXISTS(SELECT 1 FROM task_inputs i WHERE i.job_id=jobs.id AND i.task_id=? AND i.consumed=0)''',
                    (row['id'], current['account_id'], current['session_id'], current['task_id'], current['task_id']))
                if updated.rowcount:
                    self.db.execute('UPDATE task_inputs SET consumed=1 WHERE job_id=? AND task_id=?',
                                    (row['id'], current['task_id']))
                    released.append(row['id'])
        return released

    def consume_inputs(self, job, ids):
        current = self.get(job['id'])
        if not current or (current['account_id'], current['session_id']) != (job['account_id'], job['session_id']):
            raise PermissionError('不能确认其他用户或会话的输入。')
        with self.db:
            for jid in ids:
                self.db.execute('''UPDATE task_inputs SET consumed=1 WHERE job_id=? AND task_id=?
                    AND EXISTS(SELECT 1 FROM jobs j WHERE j.id=task_inputs.job_id AND j.task_id=?
                               AND j.account_id=? AND j.session_id=?)''',
                    (jid,current['task_id'],current['task_id'],current['account_id'],current['session_id']))
