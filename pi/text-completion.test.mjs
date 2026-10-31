import assert from 'node:assert/strict';
import test from 'node:test';
import {mkdtemp, readFile, writeFile, copyFile, symlink, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';

// Run the shipped agent loop and IPC protocol; only local inference is replaced.
// These neutral fixtures never call an external service or execute a real tool.
async function runFixture({segments, checkpoint, interruptAt, correction=false, reject=false}={}) {
  const directory=await mkdtemp(join(tmpdir(),'pi-completion-'));
  let child;
  try {
    await symlink(fileURLToPath(new URL('./node_modules',import.meta.url)),join(directory,'node_modules'));
    for(const name of ['context.mjs','progress.mjs','tool-context.mjs'])await copyFile(new URL(name,import.meta.url),join(directory,name));
    const provider=`
      const fixtureSegments=${JSON.stringify(segments||[{text:'A complete response.',stop:'stop'}])};
      let fixtureIndex=0;
      function streamSimple(model,context,options){
        const segment=fixtureSegments[fixtureIndex++];
        if(!segment)throw new Error('Unexpected additional generation');
        const payload=options.onPayload({tools:context.tools});
        send({type:'fixture_model',payload,controls:context.messages.filter(m=>m.role==='user').map(m=>m.control_kind),
          hasHarnessState:context.messages.some(m=>Object.hasOwn(m,'harness_state')),
          hasContinuationAnchor:context.messages.some(m=>m.role==='user'&&JSON.stringify(m.content).includes('CONTINUATION_ANCHOR'))});
        const message={role:'assistant',api:model.api,provider:model.provider,model:model.id,timestamp:Date.now(),
          stopReason:segment.stop,errorMessage:['aborted','error'].includes(segment.stop)?'Fixture interruption':undefined,
          content:[{type:'text',text:segment.text}],
          usage:{input:0,output:segment.stop==='length'?3072:20,cacheRead:0,cacheWrite:0,totalTokens:20,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}}};
        return {async *[Symbol.asyncIterator](){yield {type:'start',partial:{...message,content:[]}};
          yield {type:'text_delta',partial:message,delta:segment.text,contentIndex:0};yield {type:'done',message};},result:async()=>message};
      }
    `;
    const source=(await readFile(new URL('./runner-v2.mjs',import.meta.url),'utf8')).replace(
      "import {streamSimple} from '@earendil-works/pi-ai/api/openai-completions';",provider);
    await writeFile(join(directory,'runner.mjs'),source);
    child=spawn(process.execPath,[join(directory,'runner.mjs')],{stdio:['pipe','pipe','pipe']});
    const state={models:[],checks:[],checkpoints:[],drafts:[],stderr:'',result:null,interrupted:false};
    let corrected=false;
    child.stderr.on('data',chunk=>{state.stderr+=chunk;});
    const lines=createInterface({input:child.stdout});
    lines.on('line',line=>{
      const event=JSON.parse(line);
      if(event.type==='fixture_model'){state.models.push(event);return;}
      if(event.type==='draft'){state.drafts.push(event.text);return;}
      if(event.type==='result'){state.result=event;return;}
      if(event.type==='error'){state.error=event;return;}
      let result={};
      if(event.type==='checkpoint'){
        state.checkpoints.push(event.messages);
        const completion=event.messages.at(-1)?.harness_state?.textCompletion;
        if(!state.interrupted&&interruptAt==='segment'&&completion?.reason==='length'&&!completion.continuing||
           !state.interrupted&&interruptAt==='reserved'&&completion?.continuing){
          state.interrupted=true;child.kill('SIGTERM');return;
        }
      }
      if(state.interrupted)return;
      if(event.type==='sync_inputs'){
        const completion=state.checkpoints.at(-1)?.at(-1)?.harness_state?.textCompletion;
        const updated=correction&&!corrected&&completion?.reason==='length';
        if(updated)corrected=true;
        result={inputs:updated?[{job_id:'correction',text:'Replace the previous request with a short greeting.'}]:[]};
      }
      if(event.type==='final_check'){
        state.checks.push(event);
        result=reject?{accepted:false,repair:true,outcome:'partial',answer:event.answer,feedback:'Fixture incomplete output.'}:
          {accepted:true,repair:false,outcome:'completed',answer:event.answer};
      }
      child.stdin.write(JSON.stringify({id:event.id,result})+'\n');
    });
    const exited=new Promise((resolve,reject)=>{child.on('error',reject);child.on('close',resolve);});
    child.stdin.write(JSON.stringify({type:'init',job_id:'fixture',checkpoint,prompt:'Write a short neutral explanation.',
      local_time:'fixture',tools:[{name:'calculate',description:'Calculate',parameters:{type:'object',properties:{}}}]})+'\n');
    const code=await exited;
    if(!state.interrupted){assert.equal(code,0,state.stderr);assert.ok(state.result,JSON.stringify(state.error)+state.stderr);}
    return state;
  } finally {
    child?.kill();
    await rm(directory,{recursive:true,force:true});
  }
}

test('length then stop delivers concatenated text and keeps the draft prefix visible',{timeout:15000},async()=>{
  const state=await runFixture({segments:[{text:'A detailed explanation begins ',stop:'length'},{text:'and reaches its conclusion.',stop:'stop'}]});
  assert.equal(state.models.length,2);
  assert.deepEqual(state.models[1].payload.tools,[]);
  assert.equal(state.models[1].payload.tool_choice,'none');
  assert.equal(state.result.answer,'A detailed explanation begins and reaches its conclusion.');
  assert.equal(state.checks.length,1);
  assert.equal(state.checks[0].answer,state.result.answer);
  assert.deepEqual(state.result.generation,{complete:true,stop_reason:'stop',reason:''});
  assert.deepEqual(state.checks[0].generation,state.result.generation);
  const first=state.drafts.indexOf('A detailed explanation begins ');
  assert.ok(first>=0);
  assert.ok(state.drafts.slice(first).every(draft=>draft.startsWith('A detailed explanation begins ')));
});

test('repeated token limits have a bounded continuation budget and preserve all partial text',{timeout:15000},async()=>{
  const state=await runFixture({segments:[{text:'One. ',stop:'length'},{text:'Two. ',stop:'length'},{text:'Three.',stop:'length'}],reject:true});
  assert.equal(state.models.length,3);
  assert.equal(state.result.answer,'One. Two. Three.');
  assert.equal(state.checks.length,1,'Do not rewrite a partial draft during review repair');
  assert.deepEqual(state.result.generation,{complete:false,stop_reason:'length',reason:'length'});
  assert.ok(!state.models.some(model=>model.controls.includes('finalize')));
});

test('exhausted work budget delivers saved partial text without a summary generation',{timeout:15000},async()=>{
  const first=await runFixture({segments:[{text:'The preserved opening ',stop:'length'}],interruptAt:'segment'});
  const checkpoint=first.checkpoints.at(-1);
  checkpoint.at(-1).harness_state.elapsed_ms=900001;
  const state=await runFixture({checkpoint,segments:[],reject:true});
  assert.equal(state.models.length,0);
  assert.equal(state.result.answer,'The preserved opening');
  assert.equal(state.result.budget_limited,true);
  assert.deepEqual(state.result.generation,{complete:false,stop_reason:'length',reason:'length'});
});

for(const interruptAt of ['segment','reserved'])test(`restart after ${interruptAt} resumes text without repeating its prefix`,{timeout:15000},async()=>{
  const first=await runFixture({segments:[{text:'The saved opening ',stop:'length'}],interruptAt});
  assert.equal(first.interrupted,true);
  const state=await runFixture({checkpoint:first.checkpoints.at(-1),segments:[{text:'has a completed ending.',stop:'stop'}]});
  assert.equal(state.models.length,1);
  assert.equal(state.result.answer,'The saved opening has a completed ending.');
  assert.equal(state.result.generation.complete,true);
  assert.equal(state.checkpoints.at(-1).at(-1).harness_state.textCompletion.continuations,1);
});

for(const stop of ['aborted','error','stop'])test(`a ${stop==='stop'?'blank':stop} continuation preserves its prefix and remains incomplete`,{timeout:15000},async()=>{
  const state=await runFixture({segments:[{text:'Already generated text.',stop:'length'},{text:'',stop}],reject:true});
  assert.equal(state.models.length,2);
  assert.equal(state.result.answer,'Already generated text.');
  assert.deepEqual(state.result.generation,{complete:false,stop_reason:stop,reason:stop==='stop'?'empty':stop});
});

test('real user correction resets the old continuation and answers the new request',{timeout:15000},async()=>{
  const state=await runFixture({segments:[{text:'An obsolete opening ',stop:'length'},{text:'Hello.',stop:'stop'}],correction:true});
  assert.equal(state.models.length,2);
  assert.equal(state.result.answer,'Hello.');
  assert.equal(state.result.generation.complete,true);
  assert.equal(state.checkpoints.at(-1).at(-1).harness_state.textCompletion.truncated,false);
  assert.ok(!state.models.some(model=>model.controls.includes('continue_text')));
});

test('long-context continuation keeps the saved text and its anchor without exposing durable state',{timeout:15000},async()=>{
  const prefix='Neutral context. '.repeat(1200)+'CONTINUATION_ANCHOR';
  const first=await runFixture({segments:[{text:prefix,stop:'length'}],interruptAt:'segment'});
  const checkpoint=first.checkpoints.at(-1);
  checkpoint.find(message=>message.role==='user').content='Provided background. '.repeat(1800);
  const state=await runFixture({checkpoint,segments:[{text:' Ending.',stop:'stop'}]});
  assert.equal(state.result.answer,prefix+' Ending.');
  assert.equal(state.models.length,1);
  assert.equal(state.models[0].hasHarnessState,false);
  assert.equal(state.models[0].hasContinuationAnchor,true);
  assert.equal(state.checkpoints.at(-1).at(-1).harness_state.textCompletion.text,prefix+' Ending.');
});
