// Keep common actions immediately available; load uncommon schemas on discovery.
// The host still owns the complete registry and checks every execution.
const core = new Set(['capabilities','continue_task','plan_task','list_skills','read_skill',
  'calculate','web_search','web_read','generate_image','generate_video','generate_video_project','video_capacity',
  'list_attachments','read_attachment']);

export function mediaAcknowledgement(turn, jobId) {
  const calls=(turn.message?.content || []).filter(part=>part.type==='toolCall');
  if(calls.length!==1 || !['generate_image','generate_video','generate_video_project'].includes(calls[0].name))return null;
  const results=turn.toolResults || [];
  if(results.length!==1 || results[0].isError || results[0].toolCallId!==calls[0].id)return null;
  let receipt;
  try { receipt=JSON.parse(results[0].content.filter(p=>p.type==='text').map(p=>p.text).join('')); }
  catch { return null; }
  if(receipt?.status!=='queued' || receipt.parent_job_id!==jobId || !receipt.job_id || receipt.artifact_exists!==false)return null;
  if(calls[0].name==='generate_video_project')return '视频项目已加入分镜队列。分镜方案完成后会请你核对；确认前不会开始渲染。';
  if(calls[0].name==='generate_video')return '视频方案已加入队列。提示词和参考图设置完成后会请你核对；确认前不会开始渲染。';
  return '图片已加入生成队列，完成后会回到这个会话。';
}

export function toolContext(definitions, groups = {}, checkpoint = []) {
  const allowed = new Set(definitions.map(t => t.name));
  const selected = new Set([...core].filter(name => allowed.has(name)));
  const add = name => { if (allowed.has(name)) selected.add(name); };
  const discover = result => {
    for (const item of result?.capabilities || []) if (item.available) add(item.name);
  };
  for (const message of checkpoint || []) {
    if (message.role === 'assistant') for (const part of message.content || [])
      if (part.type === 'toolCall') add(part.name);
    if (message.role === 'toolResult' && message.toolName === 'capabilities' && !message.isError)
      for (const part of message.content || []) if (part.type === 'text') {
        try { discover(JSON.parse(part.text)); } catch { /* malformed history cannot grant tools */ }
      }
  }
  const catalog = Object.entries(groups).map(([group, names]) =>
    `${group}: ${names.filter(name => allowed.has(name) && !core.has(name)).join(', ')}`)
    .filter(line => !line.endsWith(': ')).join('\n');
  return {catalog, discover, filter: tools => tools.filter(tool => selected.has(tool.name))};
}
