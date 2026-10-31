import assert from 'node:assert/strict';
import test from 'node:test';
import {toolContext} from './tool-context.mjs';
const definitions=['capabilities','calculate','terminal','write_file'].map(name=>({name}));
test('rare schemas load only after host discovery; hallucinations cannot add tools',()=>{
  const view=toolContext(definitions,{computer:['terminal'],notes:['write_file'],secret:['stolen']});
  assert.deepEqual(view.filter(definitions).map(t=>t.name),['capabilities','calculate']);
  assert.equal(view.catalog,'computer: terminal\nnotes: write_file');
  view.discover({capabilities:[{name:'terminal',available:true},{name:'write_file',available:false},{name:'stolen',available:true}]});
  assert.deepEqual(view.filter(definitions).map(t=>t.name),['capabilities','calculate','terminal']);
});
test('resume restores discovery and used tools only within the current host registry',()=>{
  const checkpoint=[{role:'assistant',content:[{type:'toolCall',name:'write_file'}]},
    {role:'toolResult',toolName:'capabilities',content:[{type:'text',text:JSON.stringify({capabilities:[{name:'terminal',available:true},{name:'stolen',available:true}]})}]}];
  const view=toolContext(definitions,{},checkpoint);
  assert.deepEqual(view.filter(definitions),definitions);
  assert.deepEqual(toolContext(definitions.slice(0,2),{},checkpoint).filter(definitions),definitions.slice(0,2));
});

import {mediaAcknowledgement} from './tool-context.mjs';
const submitted=()=>({message:{content:[{type:'toolCall',id:'call',name:'generate_image'}]},toolResults:[{toolCallId:'call',content:[{type:'text',text:JSON.stringify({job_id:'child',parent_job_id:'parent',status:'queued',artifact_exists:false})}]}]});
test('media handoff requires an exact pending receipt, never an error or composite tool batch',()=>{
  assert.match(mediaAcknowledgement(submitted(),'parent'),/生成队列/);
  assert.equal(mediaAcknowledgement(submitted(),'another-parent'),null);
  const error=submitted();error.toolResults[0].isError=true;assert.equal(mediaAcknowledgement(error,'parent'),null);
  const multi=submitted();multi.message.content.push({type:'toolCall',id:'read',name:'web_read'});assert.equal(mediaAcknowledgement(multi,'parent'),null);
  const done=submitted();done.toolResults[0].content[0].text=JSON.stringify({job_id:'child',parent_job_id:'parent',status:'succeeded',artifact_exists:true});assert.equal(mediaAcknowledgement(done,'parent'),null);
});
test('video submission describes the required review before rendering',()=>{
  for(const name of ['generate_video','generate_video_project']){
    const turn=submitted();turn.message.content[0].name=name;
    assert.match(mediaAcknowledgement(turn,'parent'),/确认前不会开始渲染/);
  }
});
