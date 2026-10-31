import assert from 'node:assert/strict';
import test from 'node:test';
import {mkdtemp, readFile, writeFile, copyFile, symlink, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {createProgressGuard, restoreProgress} from './progress.mjs';

test('three identical state reads stop; object property ordering does not evade the bound', () => {
  const guard = createProgressGuard();
  assert.equal(guard.observe('task_status', {job_id: 'fixture', offset: 0}, {status: 'running', count: 2}), false);
  assert.equal(guard.observe('task_status', {offset: 0, job_id: 'fixture'}, {count: 2, status: 'running'}), false);
  assert.equal(guard.observe('task_status', {job_id: 'fixture', offset: 0}, {status: 'running', count: 2}), true);
});

test('repeated task association cannot consume the whole tool budget', () => {
  const guard=createProgressGuard();
  const args={job_id:'earlier'};
  const receipt={task_id:'same-task',already_linked:true};
  assert.equal(guard.observe('continue_task',args,receipt),false);
  assert.equal(guard.observe('continue_task',args,receipt),false);
  assert.equal(guard.observe('continue_task',args,receipt),true);
});

test('changed state, parameters, or tools reset the consecutive repetition count', () => {
  const guard = createProgressGuard();
  guard.observe('task_status', {}, {status: 'running'});
  guard.observe('task_status', {}, {status: 'running'});
  assert.equal(guard.observe('task_status', {}, {status: 'succeeded'}), false);
  assert.equal(guard.observe('task_status', {job_id: 'other'}, {status: 'succeeded'}), false);
  assert.equal(guard.observe('capabilities', {}, {status: 'succeeded'}), false);
  for (let offset = 0; offset < 60000; offset += 12000) {
    assert.equal(guard.observe('task_evidence', {job_id: 'fixture', offset}, {text: 'a repeated paragraph'}), false);
  }
});

test('failed reads remain retryable and mutation receipts never trigger the guard', () => {
  const guard = createProgressGuard();
  guard.observe('task_status', {}, {status: 'running'});
  guard.observe('task_status', {}, {status: 'running'});
  assert.equal(guard.observe('task_status', {}, {error: 'temporary'}, true), false);
  assert.equal(guard.observe('task_status', {}, {status: 'running'}), false);
  assert.equal(guard.observe('task_status', {}, {status: 'running'}), false);
  for (let attempt = 0; attempt < 5; attempt++) {
    assert.equal(guard.observe('write_file', {name: 'fixture.txt', content: 'same'}, {saved: 'fixture.txt'}), false);
  }
  guard.reset();
  assert.equal(guard.stopped, false);
});

function receipt(index, {name = 'task_status', args = {}, result = {status: 'running'}, isError = false} = {}) {
  return [
    {role: 'assistant', content: [{type: 'toolCall', id: `call-${index}`, name, arguments: args}]},
    {role: 'toolResult', toolCallId: `call-${index}`, toolName: name, isError,
      content: [{type: 'text', text: JSON.stringify(result)}]},
  ];
}

test('restoring a polling checkpoint stops before making three more requests', () => {
  const checkpoint = [{role: 'user', content: 'A fact correction'},
    ...receipt(1), ...receipt(2), ...receipt(3)];
  const guard = createProgressGuard();
  assert.equal(restoreProgress(guard, checkpoint), true);
  assert.equal(guard.stopped, true);
});

test('real input and changed observations reset restored progress', () => {
  const repeated = [...receipt(1), ...receipt(2), ...receipt(3)];
  for (const tail of [
    [{role: 'user', content: 'A new instruction'}, ...receipt(4)],
    receipt(4, {result: {status: 'succeeded'}}),
    receipt(4, {result: {error: 'temporary'}, isError: true}),
  ]) assert.equal(restoreProgress(createProgressGuard(), [...repeated, ...tail]), false);
});

test('alternating state reads and volatile status timestamps cannot evade the bound', () => {
  const guard = createProgressGuard();
  for (let i = 0; i < 4; i++) assert.equal(guard.observe(i % 2 ? 'system_status' : 'task_status', {},
    {status: 'running', timestamp: i, elapsed_ms: i * 200}), false);
  assert.equal(guard.observe('task_status', {}, {status: 'running', timestamp: 100, elapsed_ms: 900}), true);
});

test('self-status is not awaitable and identical failures have a bounded retry allowance', () => {
  const guard = createProgressGuard();
  assert.equal(guard.observe('task_status', {}, {awaitable: false, status: 'running'}), false);
  assert.equal(guard.observe('task_status', {}, {awaitable: false, status: 'running'}), true);
  guard.reset();
  for (let i = 0; i < 2; i++) assert.equal(guard.observe('web_read', {url: 'https://example.test'}, {code: 'unavailable'}, true), false);
  assert.equal(guard.observe('web_read', {url: 'https://example.test'}, {code: 'unavailable'}, true), true);
});

test('control prompts and draft answers do not erase repeated-failure history', () => {
  const error = {name: 'web_read', args: {url: 'https://example.test'}, result: {code: 'unavailable'}, isError: true};
  const messages = [...receipt(1, error), ...receipt(2, error),
    {role: 'user', source: 'harness_control', content: 'Please repair the result.'},
    {role: 'assistant', content: [{type: 'text', text: 'Draft explanation'}]}, ...receipt(3, error)];
  assert.equal(restoreProgress(createProgressGuard(), messages), true);
});

test('unrelated success and changed observations cannot erase an exhausted failed path', () => {
  const guard=createProgressGuard();
  const args={url:'https://example.test/unavailable'};
  for(let i=0;i<3;i++)guard.observe('web_read',args,{code:'unavailable'},true);
  assert.equal(guard.blocked('web_read',args),true);
  guard.observe('calculate',{expression:'1+1'},{value:'2'});
  guard.observe('task_status',{}, {status:'running'});
  guard.observe('task_status',{}, {status:'succeeded'});
  assert.equal(guard.blocked('web_read',args),true);
  assert.equal(guard.blocked('web_read',{url:'https://example.test/alternative'}),false);
  guard.reset();
  assert.equal(guard.blocked('web_read',args),false,'Only a genuine new input clears failed-path exclusions');
});

for (const [restoredReads, steering] of [[0, false], [2, false], [3, false], [0, true], [3, true]]) {
  test(`real runner stops repeated reads and accepts new input (${restoredReads} restored, steering=${steering})`, {timeout: 15000}, async () => {
    const directory = await mkdtemp(join(tmpdir(), 'pi-progress-'));
    let child;
    try {
      await symlink(fileURLToPath(new URL('./node_modules', import.meta.url)), join(directory, 'node_modules'));
      for (const name of ['context.mjs', 'progress.mjs', 'tool-context.mjs']) {
        await copyFile(new URL(name, import.meta.url), join(directory, name));
      }
      const original = await readFile(new URL('./runner-v2.mjs', import.meta.url), 'utf8');
      // Replace only the provider transport. The actual Agent loop and RPC host
      // protocol run unchanged, without a network listener or a local model.
      const fakeProvider = `
        let fakeTurns=0;
        function streamSimple(model,context,options){
          const payload=options.onPayload({tools:[{name:'task_status'}]});
          const steered=context.messages.some(message=>message.role==='user'&&JSON.stringify(message.content).includes('[用户补充'));
          const polling=payload.tools.length>0&&!steered;
          const message={role:'assistant',api:model.api,provider:model.provider,model:model.id,
            timestamp:Date.now(),stopReason:polling?'toolUse':'stop',
            usage:{input:0,output:0,cacheRead:0,cacheWrite:0,totalTokens:0,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}},
            content:polling?[{type:'toolCall',id:'new-'+(++fakeTurns),name:'task_status',arguments:{}}]:[{type:'text',text:steered?'I handled the new input.':'I understand the corrected fact.'}]};
          return {async *[Symbol.asyncIterator](){yield {type:'done',message};},result:async()=>message};
        }
      `;
      const runner = original.replace("import {streamSimple} from '@earendil-works/pi-ai/api/openai-completions';", fakeProvider);
      assert.notEqual(runner, original);
      await writeFile(join(directory, 'runner.mjs'), runner);
      const checkpoint = [{role: 'user', content: 'Please acknowledge the corrected fact.'},
        ...Array.from({length: restoredReads}, (_, index) => receipt(index)).flat()];
      let toolCalls = 0, modelRequests = 0, reviews = 0, completed, stderr = '', steered = false;
      child = spawn(process.execPath, [join(directory, 'runner.mjs')], {stdio: ['pipe', 'pipe', 'pipe']});
      child.stderr.on('data', chunk => { stderr += chunk; });
      const lines = createInterface({input: child.stdout});
      lines.on('line', line => {
        const event = JSON.parse(line);
        if (event.type === 'result') { completed = event; return; }
        if (event.type === 'error') return;
        let result = {};
        if (event.type === 'sync_inputs') {
          if (steering && !steered && toolCalls + restoredReads >= 3) {
            steered = true;
            result = {inputs: [{job_id: 'new-input', text: 'Please handle my new correction.'}]};
          } else result = {inputs: []};
        }
        if (event.type === 'model_acquire') modelRequests++;
        if (event.type === 'tool_call') { toolCalls++; result = {status: 'running'}; }
        if (event.type === 'final_check') {
          reviews++;
          result = {accepted: steering, repair: !steering, feedback: 'Please query again.', outcome: steering ? 'completed' : 'partial'};
        }
        child.stdin.write(JSON.stringify({id: event.id, result}) + '\n');
      });
      const exited = new Promise((resolve, reject) => {
        child.on('error', reject);
        child.on('close', code => resolve(code));
      });
      child.stdin.write(JSON.stringify({type: 'init', prompt: checkpoint[0].content,
        checkpoint: restoredReads ? checkpoint : undefined, local_time: 'fixture',
        tools: [{name: 'task_status', description: 'Read state', parameters: {type: 'object', properties: {}, additionalProperties: false}}]}) + '\n');
      assert.equal(await exited, 0, stderr);
      assert.ok(completed, stderr);
      assert.equal(toolCalls, 3 - restoredReads);
      assert.equal(modelRequests, 4 - restoredReads, 'exactly one final answer after polling stops');
      assert.equal(reviews, 1, 'review must not reopen the stopped loop');
      assert.equal(completed.answer, steering ? 'I handled the new input.' : 'I understand the corrected fact.');
      assert.equal(completed.limited, !steering);
    } finally {
      child?.kill();
      await rm(directory, {recursive: true, force: true});
    }
  });
}

async function runFixture({mode = 'answer', checkpoint, review, updates, onTool, modelAcquireDelay = 0, toolGroups, task} = {}) {
  const directory = await mkdtemp(join(tmpdir(), 'pi-runtime-'));
  let child;
  try {
    await symlink(fileURLToPath(new URL('./node_modules', import.meta.url)), join(directory, 'node_modules'));
    for (const name of ['context.mjs', 'progress.mjs', 'tool-context.mjs']) await copyFile(new URL(name, import.meta.url), join(directory, name));
    const original = await readFile(new URL('./runner-v2.mjs', import.meta.url), 'utf8');
    const provider = `
      let fixtureTurn = 0;
      function streamSimple(model, context, options) {
        fixtureTurn++;
        const selected = new Set();
        for (const message of context.messages) if (message.role === 'system') {
          for (const tool of message.toolsRemoved || []) selected.delete(tool.name);
          for (const tool of message.toolsAdded || []) selected.add(tool.name);
        }
        send({type:'fixture_model',names:[...selected]});
        const payload = options.onPayload({tools: [{}]});
        const steered = context.messages.some(m => m.role === 'user' && m.source === 'user' && m.source_job_id === 'correction');
        const recovering = context.messages.some(m => m.control_kind === 'recover');
        const recovered = context.messages.some(m => m.role === 'toolResult' && m.toolName === 'calculate' && !m.isError);
        const linked = context.messages.some(m => m.role === 'toolResult' && m.toolName === 'continue_task' && !m.isError);
        const plans = context.messages.filter(m => m.role === 'toolResult' && m.toolName === 'plan_task' && !m.isError).length;
        const mode = ${JSON.stringify(mode)};
        const polling = (['alternating','errors','self','recover_repeat'].includes(mode) || mode === 'planned_poll' && plans<2 || mode === 'recover' && !recovered || mode === 'linked' && !linked || mode === 'batch' && !steered) && payload.tools.length > 0;
        const name = mode === 'planned_poll' ? !plans||recovered ? 'plan_task' : recovering ? 'calculate' : 'task_status'
          : mode === 'recover_repeat' ? !recovered&&recovering ? 'calculate' : 'web_read'
          : mode === 'recover' ? recovering ? 'calculate' : 'web_read' : mode === 'linked' ? 'continue_task'
          : mode === 'errors' ? 'web_read' : mode === 'alternating' && fixtureTurn % 2 === 0 ? 'system_status' : 'task_status';
        const message = {role: 'assistant', api: model.api, provider: model.provider, model: model.id,
          timestamp: Date.now(), stopReason: polling ? 'toolUse' : 'stop',
          usage: {input:0,output:0,cacheRead:0,cacheWrite:0,totalTokens:0,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}},
          content: polling ? mode === 'batch'
            ? ['task_status','write_file','write_file'].map((tool,index)=>({type:'toolCall',id:'batch-'+index,name:tool,arguments:{}}))
            : [{type:'toolCall',id:'fixture-'+fixtureTurn,name,arguments:{}}]
            : [{type:'text',text:steered?'The correction is included.':'A useful answer.'}]};
        return {async *[Symbol.asyncIterator](){yield {type:'done',message};},result:async()=>message};
      }
    `;
    await writeFile(join(directory, 'runner.mjs'), original.replace(
      "import {streamSimple} from '@earendil-works/pi-ai/api/openai-completions';", provider));
    const state = {tools: 0, toolNames: [], selectedTools: [], models: 0, reviews: 0, syncs: 0, checkpoints: [], result: null, stderr: ''};
    child = spawn(process.execPath, [join(directory, 'runner.mjs')], {stdio: ['pipe', 'pipe', 'pipe']});
    child.stderr.on('data', chunk => { state.stderr += chunk; });
    const lines = createInterface({input: child.stdout});
    lines.on('line', line => {
      const event = JSON.parse(line);
      if (event.type === 'fixture_model') { state.selectedTools.push(event.names); return; }
      if (event.type === 'result') { state.result = event; return; }
      if (event.type === 'error') { state.error = event; return; }
      let result = {}, error;
      if (event.type === 'checkpoint') state.checkpoints.push(event);
      if (event.type === 'model_acquire') state.models++;
      if (event.type === 'sync_inputs') { state.syncs++; result = {inputs: updates?.(state) || []}; }
      if (event.type === 'tool_call') {
        state.tools++;
        state.toolNames.push(event.name);
        if (mode === 'errors' || ['recover','recover_repeat'].includes(mode) && event.name === 'web_read') error = {code: 'service_unavailable', message: 'Fixture service unavailable'};
        else result = onTool?.(state, event) || {status: 'running', timestamp: state.tools,
          ...(mode === 'self' ? {awaitable: false} : {})};
      }
      if (event.type === 'final_check') {
        state.reviews++;
        result = review?.(state, event) || {accepted: true, outcome: 'completed', answer: event.answer};
      }
      if (event.type === 'model_acquire' && modelAcquireDelay) {
        setTimeout(() => child.stdin.write(JSON.stringify({id: event.id, result}) + '\n'), modelAcquireDelay);
        return;
      }
      child.stdin.write(JSON.stringify({id: event.id, result, ...(error ? {error} : {})}) + '\n');
    });
    const exited = new Promise((resolve, reject) => { child.on('error', reject); child.on('close', resolve); });
    child.stdin.write(JSON.stringify({type: 'init', job_id: 'fixture', checkpoint, tool_groups: toolGroups, task,
      prompt: 'Explain the supplied fact.', local_time: 'fixture',
      tools: ['task_status', 'system_status', 'web_read', 'write_file', 'calculate', 'continue_task', 'capabilities', 'plan_task', 'computer_write'].map(name => ({name, description: name,
        parameters: {type: 'object', properties: {}, additionalProperties: false}}))}) + '\n');
    assert.equal(await exited, 0, state.stderr);
    assert.ok(state.result, JSON.stringify(state.error) + state.stderr);
    return state;
  } finally {
    child?.kill();
    await rm(directory, {recursive: true, force: true});
  }
}

test('waiting for the GPU does not spend the model work budget', {timeout: 15000}, async () => {
  const state=await runFixture({modelAcquireDelay:300});
  assert.equal(state.result.limited,false);
  assert.ok(state.checkpoints.at(-1).messages.at(-1).harness_state.elapsed_ms<200);
});

for (const [mode, count] of [['alternating', 5], ['errors', 3], ['self', 2]]) {
  test(`real runner bounds ${mode} and emits source-tagged checkpoints`, {timeout: 15000}, async () => {
    const state = await runFixture({mode});
    assert.equal(state.tools, count);
    assert.equal(state.models, count + 1 + (mode === 'errors' ? 2 : 0));
    assert.equal(state.result.limited, true);
    const messages = state.checkpoints.at(-1).messages;
    assert.equal(messages[0].source, 'user');
    assert.equal(messages[0].source_job_id, 'fixture');
    assert.ok(messages.some(m => m.source === 'harness_control' && m.control_kind === 'finalize'));
    assert.ok(messages.every(m => m.source_job_id === 'fixture'));
    assert.equal(messages.at(-1).harness_state.calls, count);
  });
}

test('same review feedback without new work receives at most one repair', {timeout: 15000}, async () => {
  const state = await runFixture({review: () => ({accepted: false, repair: true, outcome: 'partial', feedback: 'The same gap.'})});
  assert.equal(state.reviews, 2);
  assert.equal(state.models, 2);
  const controls = state.checkpoints.at(-1).messages.filter(m => m.source === 'harness_control');
  assert.equal(controls.length, 1);
  assert.equal(controls[0].control_kind, 'review');
});

test('an exhausted failed path replans and completes through a different available tool', {timeout: 15000}, async () => {
  const state = await runFixture({mode: 'recover'});
  assert.deepEqual(state.toolNames, ['web_read', 'web_read', 'web_read', 'calculate']);
  assert.equal(state.result.limited, false);
  assert.equal(state.result.outcome, 'completed');
  const saved = state.checkpoints.at(-1).messages.at(-1).harness_state;
  assert.equal(saved.recoveryAttempts, 1);
  assert.equal(saved.exhaustedFailures.length, 1, 'Success on another step does not reopen the exhausted failed path');
});

test('successful alternative work cannot reopen the failed tool path in the same run', {timeout:15000}, async()=>{
  const state=await runFixture({mode:'recover_repeat'});
  assert.deepEqual(state.toolNames,['web_read','web_read','web_read','calculate']);
  assert.equal(state.checkpoints.at(-1).messages.at(-1).harness_state.exhaustedFailures.length,1);
  assert.equal(state.result.limited,true,'A stubborn model is still bounded without resubmitting the failed operation');
});

test('stale state reads do not abandon outstanding planned work', {timeout:15000}, async()=>{
  const state=await runFixture({mode:'planned_poll',onTool:(state,event)=>event.name==='plan_task'
    ? {work_items:[{id:'calculate',description:'Calculate the pending result',status:state.toolNames.includes('calculate')?'completed':'pending'}]}
    : {status:'running'}});
  assert.deepEqual(state.toolNames,['plan_task','task_status','task_status','task_status','calculate','plan_task']);
  assert.equal(state.result.limited,false);
  const saved=state.checkpoints.at(-1).messages.at(-1).harness_state;
  assert.equal(saved.recoveryAttempts,1);
  assert.equal(saved.pendingPlanWork,false);
  assert.ok(state.checkpoints.at(-1).messages.some(message=>message.control_kind==='recover'));
});

test('failed path recovery is durable and cannot reset its budget on restart', {timeout: 15000}, async () => {
  const first = await runFixture({mode: 'errors'});
  const checkpoint = first.checkpoints.at(-1).messages;
  const saved = checkpoint.at(-1).harness_state;
  assert.equal(saved.recoveryAttempts, 2);
  assert.equal(saved.exhaustedFailures.length, 1);
  assert.equal(first.tools, 3, 'Recovery cannot retry an identical exhausted host operation');
  const resumed = await runFixture({mode: 'errors', checkpoint});
  assert.equal(resumed.tools, 0);
  assert.equal(resumed.models, 0);
  assert.equal(resumed.checkpoints.at(-1).messages.at(-1).harness_state.recoveryAttempts, 2);
});

test('review repair keeps discovery and one-time task association restrictions', {timeout: 15000}, async () => {
  const state = await runFixture({mode: 'linked', toolGroups: {computer: ['computer_write'], notes: ['write_file']},
    review: (state,event) => state.reviews === 1 ? {accepted:false, repair:true, feedback:'Include the remaining result.', outcome:'partial'}
      : {accepted:true, outcome:'completed', answer:event.answer},
  });
  assert.equal(state.reviews, 2);
  assert.deepEqual(state.toolNames, ['continue_task']);
  assert.ok(state.selectedTools[0].includes('plan_task'), 'Planning is available before discovery');
  assert.ok(state.selectedTools.every(names => !names.includes('computer_write') && !names.includes('write_file')));
  assert.ok(state.selectedTools.slice(1).every(names => !names.includes('continue_task')));
});

test('host-associated tasks never reopen the task-link tool', {timeout: 15000}, async () => {
  const state = await runFixture({toolGroups: {}, task: {already_linked: true},
    review: (state,event) => state.reviews === 1 ? {accepted:false, repair:true, feedback:'Include one more fact.', outcome:'partial'}
      : {accepted:true, outcome:'completed', answer:event.answer},
  });
  assert.equal(state.reviews, 2);
  assert.ok(state.selectedTools.every(names => !names.includes('continue_task')));
});

test('same-job restart preserves exhausted budget and does not repeat finalization', {timeout: 15000}, async () => {
  const checkpoint = [{role: 'user', source: 'user', source_job_id: 'fixture', content: 'Explain the supplied fact.',
    harness_state: {version: 1, job_id: 'fixture', calls: 48, turns: 24, elapsed_ms: 500,
      limited: true, budgetLimited: true, finalized: false, finalizing: false, reviewAttempts: 0}}];
  const first = await runFixture({checkpoint});
  assert.equal(first.models, 1);
  assert.equal(first.tools, 0);
  assert.equal(first.result.calls, 48);
  assert.equal(first.result.budget_limited, true);
  const resumed = await runFixture({checkpoint: first.checkpoints.at(-1).messages});
  assert.equal(resumed.models, 0);
  assert.equal(resumed.tools, 0);
  assert.equal(resumed.result.calls, 48);
  assert.equal(resumed.result.turns, first.result.turns);
});

test('resuming a partial run adds to saved operation and model counters', {timeout: 15000}, async () => {
  const checkpoint = [{role: 'user', source: 'user', source_job_id: 'fixture', content: 'Explain the supplied fact.'},
    ...receipt(1), ...receipt(2)];
  checkpoint.at(-1).harness_state = {version: 1, job_id: 'fixture', calls: 7, turns: 8, elapsed_ms: 100};
  const state = await runFixture({mode: 'self', checkpoint});
  // The current self-status differs from the older generic running observation,
  // so it gets its own two-read allowance, without resetting total accounting.
  assert.equal(state.tools, 2);
  assert.equal(state.result.calls, 9);
  assert.equal(state.result.turns, 11);
  assert.ok(state.checkpoints.at(-1).messages.at(-1).harness_state.elapsed_ms >= 100);
});

test('a crash inside reserved finalization does not obtain another model budget on restart', {timeout: 15000}, async () => {
  const checkpoint = [{role: 'user', source: 'user', source_job_id: 'fixture', content: 'Original request'},
    {role: 'assistant', source_job_id: 'fixture', content: [{type: 'text', text: 'A preserved draft.'}]},
    {role: 'user', source: 'harness_control', source_job_id: 'fixture', control_kind: 'finalize', content: 'Finalize now.',
      harness_state: {version: 1, job_id: 'fixture', calls: 48, turns: 25, elapsed_ms: 100,
        limited: true, budgetLimited: true, finalizing: true, finalized: false, finalizationAttempts: 1}}];
  const state = await runFixture({checkpoint});
  assert.equal(state.models, 0);
  assert.equal(state.tools, 0);
  assert.equal(state.result.turns, 25);
  assert.equal(state.result.budget_limited, true);
});

test('exhausted budget leaves a new correction unconsumed for the host', {timeout: 15000}, async () => {
  const state = await runFixture({checkpoint: [{role: 'user', source: 'user', source_job_id: 'fixture', content: 'Original request',
    harness_state: {version: 1, job_id: 'fixture', calls: 48, turns: 24, elapsed_ms: 50, budgetLimited: true, limited: true}}],
    updates: () => [{job_id: 'correction', text: 'A new fact.'}]});
  assert.equal(state.result.budget_limited, true);
  assert.equal(state.result.deferred_inputs, true);
  assert.ok(state.checkpoints.every(event => !event.input_ids.includes('correction')));
  assert.ok(state.checkpoints.at(-1).messages.every(m => m.source_job_id !== 'correction'));
});

test('a changed-input verdict syncs and answers real input before reviewing again', {timeout: 15000}, async () => {
  const state = await runFixture({
    review: (state, event) => state.reviews === 1 ? {accepted: false, repair: true, inputs_changed: true}
      : {accepted: true, outcome: 'completed', answer: event.answer},
    updates: state => state.reviews ? [{job_id: 'correction', text: 'Use the new fact.'}] : [],
  });
  assert.equal(state.result.answer, 'The correction is included.');
  assert.equal(state.reviews, 2);
  assert.equal(state.models, 2);
  assert.deepEqual(state.checkpoints.at(-1).input_ids, ['correction']);
  const correction = state.checkpoints.at(-1).messages.find(m => m.source_job_id === 'correction');
  assert.equal(correction.source, 'user');
  assert.ok(state.checkpoints.at(-1).messages.every(m => m.control_kind !== 'review'));
});

test('a correction after the first tool blocks every remaining old-plan mutation in the batch', {timeout: 15000}, async () => {
  const state = await runFixture({mode: 'batch',
    updates: state => state.tools ? [{job_id: 'correction', text: 'Stop both writes and explain the new fact.'}] : [],
  });
  assert.deepEqual(state.toolNames, ['task_status']);
  assert.equal(state.models, 2);
  assert.equal(state.result.answer, 'The correction is included.');
  const results = state.checkpoints.at(-1).messages.filter(m => m.role === 'toolResult');
  assert.deepEqual(results.map(m => [m.toolCallId, !!m.isError]), [['batch-0', false], ['batch-1', true], ['batch-2', true]]);
});
