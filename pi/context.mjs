// Compact only the model view. This is a conservative heuristic, not an exact
// tokenizer: bound characters as well as an ASCII/CJK weighted token estimate.
// The 24k estimate leaves headroom in the model's 32k window for the response.
// Full source messages remain in the host checkpoint.
const MAX_VIEW_CHARS = 42000, MAX_ESTIMATED_TOKENS = 24000;
const size = messages => {
  const text = JSON.stringify(messages);
  let ascii = 0;
  for (let i = 0; i < text.length; i++) if (text.charCodeAt(i) < 128) ascii++;
  const estimated = ascii / 3 + (text.length - ascii) * 1.5;
  return Math.max(text.length, estimated * MAX_VIEW_CHARS / MAX_ESTIMATED_TOKENS);
};
const textOf = message => typeof message.content === 'string' ? message.content
  : (message.content || []).map(part => part.type === 'text' ? part.text : '[附件内容，完整记录保存在宿主]').join('\n');
const realUser = message => message.role === 'user' && !['harness_control', 'reference', 'historical_input_preview'].includes(message.source);

function preview(message, limit, current = false) {
  const text = textOf(message), id = message.source_job_id || '当前逻辑任务ID';
  const tail = Math.floor(limit * .2);
  const excerpt = text.length <= limit ? text : text.slice(0, Math.ceil(limit * .8)) +
    '\n[模型视图中段省略；完整原文仍已保存]\n' + (tail ? text.slice(-tail) : '');
  return {...message, view_truncated: true,
    ...(current ? {} : {source: 'historical_input_preview'}),
    content: `[${current ? message.source === 'harness_control' ? '当前执行控制信息的模型视图' : '当前用户消息的模型视图' : '历史用户消息预览'}；view_truncated=true；source_job_id=${id}。` +
      '此处不是完整原文，不能据此断言用户未说明；用 task_evidence 按任务ID分页读取完整请求。]\n' + excerpt};
}

export function compactContext(original) {
  if (size(original) <= MAX_VIEW_CHARS) return original;
  const evidenceCalls = new Set(original.flatMap(message => message.role === 'assistant'
    ? message.content.filter(part => part.type === 'toolCall' && part.name === 'task_evidence').map(part => part.id)
    : []));
  let compact = original.map(message => {
    // Evidence is already paginated. Truncating its JSON hides next_offset and
    // invites rereading page zero; retaining just the cursor would skip content.
    if (message.role !== 'toolResult' || message.toolName === 'task_evidence' || evidenceCalls.has(message.toolCallId)) return message;
    return {...message, content: message.content.map(part => part.type === 'text' && part.text.length > 1400
      ? {...part, text: part.text.slice(0, 1400) + ' [模型视图截断；用 task_evidence 按任务ID读取完整证据]'}
      : part)};
  });
  while (size(compact) > MAX_VIEW_CHARS) {
    const firstAssistant = compact.findIndex(message => message.role === 'assistant');
    if (firstAssistant < 0) break;
    let end = firstAssistant + 1;
    while (end < compact.length && compact[end].role === 'toolResult') end++;
    if (end === compact.length) break;
    compact = [...compact.slice(0, firstAssistant), ...compact.slice(end)];
  }
  if (size(compact) <= MAX_VIEW_CHARS) return compact;

  // A conversation made entirely of user messages used to bypass the loop
  // above. Preserve the newest actual instruction before summarizing history.
  const current = compact.findLast(realUser);
  const latestControl = compact.at(-1)?.source === 'harness_control' ? compact.at(-1) : null;
  compact = compact.map(message => realUser(message) && message !== current ? preview(message, 600) : message);
  if (size(compact) <= MAX_VIEW_CHARS) return compact;
  const newestGroup = new Set();
  const newestAssistant = compact.findLastIndex(message => message.role === 'assistant');
  if (newestAssistant > compact.indexOf(current)) {
    newestGroup.add(compact[newestAssistant]);
    for (let i = newestAssistant + 1; compact[i]?.role === 'toolResult'; i++) newestGroup.add(compact[i]);
  }
  let omitted = 0;
  while (size(compact) > MAX_VIEW_CHARS - 512) {
    const index = compact.findIndex(message => message.role !== 'system' && message !== current && message !== latestControl && !newestGroup.has(message));
    if (index < 0) break;
    let end = index + 1;
    if (compact[index].role === 'assistant') while (end < compact.length && compact[end].role === 'toolResult') end++;
    omitted += end - index;
    compact = [...compact.slice(0, index), ...compact.slice(end)];
  }
  // Prefer the newest complete evidence page over a duplicate long user view.
  // If even that tool group alone is oversized, remove the entire paired group.
  const minimum = compact.map(message => message === current || message === latestControl ? preview(message, 0, true) : message);
  if (size(minimum) > MAX_VIEW_CHARS - 512 && newestGroup.size) {
    compact = compact.filter(message => !newestGroup.has(message));
    omitted += newestGroup.size;
  }
  if (omitted) compact.unshift({role: 'user', source: 'harness_control', control_kind: 'context_summary',
    source_job_id: current?.source_job_id, view_truncated: true,
    content: `模型视图已省略 ${omitted} 条较早历史消息。原始请求与完整工具记录仍保留，可用 task_evidence 分页读取；省略不表示历史丢失或旧动作未完成。`});

  // A single oversized current input must be explicitly marked as a partial
  // view. Keep its beginning and end, and retain its durable source identity.
  for (const message of [latestControl, current].filter(Boolean)) {
    if (size(compact) <= MAX_VIEW_CHARS) break;
    const index = compact.indexOf(message);
    if (index < 0) continue;
    let low = 0, high = textOf(message).length, best = preview(message, 0, true);
    while (low <= high) {
      const middle = Math.floor((low + high) / 2), candidate = preview(message, middle, true);
      const trial = compact.map((item, i) => i === index ? candidate : item);
      if (size(trial) <= MAX_VIEW_CHARS) { best = candidate; low = middle + 1; }
      else high = middle - 1;
    }
    compact[index] = best;
  }
  if (size(compact) > MAX_VIEW_CHARS) throw new Error('系统指令或保留元数据超过模型视图预算；完整会话仍由宿主保存。');
  return compact;
}
