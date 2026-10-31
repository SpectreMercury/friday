// These tools observe state. Never infer progress of a mutation from its receipt.
const stateReads = new Set(['continue_task', 'task_status', 'task_evidence', 'list_media', 'capabilities',
  'system_status', 'list_attachments', 'list_files', 'list_skills']);

const volatileStatusFields = new Set(['timestamp', 'checked_at', 'queried_at', 'current_time',
  'local_time', 'elapsed', 'elapsed_ms', 'uptime', 'uptime_seconds']);

function canonical(value, omitVolatile = false) {
  if (Array.isArray(value)) return value.map(item => canonical(item, omitVolatile));
  if (value && typeof value === 'object') return Object.fromEntries(
    Object.keys(value).filter(key => !omitVolatile || !volatileStatusFields.has(key))
      .sort().map(key => [key, canonical(value[key], omitVolatile)]));
  return value;
}

export function createProgressGuard(savedFailures = [], savedReads = []) {
  let recent = [], stopped = false, failureStopped = false;
  const latest = new Map();
  const exhaustedFailures = new Set(savedFailures.filter(key => typeof key === 'string'));
  const exhaustedReads = new Set(savedReads.filter(key => typeof key === 'string'));
  const callKey = (name, args) => JSON.stringify(canonical({name, args}));
  const resetObservations = () => { recent = []; latest.clear(); exhaustedReads.clear(); stopped = false; failureStopped = false; };
  // Unrelated successful work can change observations, but cannot justify
  // repeating an exhausted failed operation. Only real user steering resets it.
  const reset = () => { resetObservations(); exhaustedFailures.clear(); };
  return {
    reset,
    resetObservations,
    blocked: (name, args) => exhaustedFailures.has(callKey(name, args)) || exhaustedReads.has(callKey(name, args)),
    failedPath: (name, args) => exhaustedFailures.has(callKey(name, args)),
    snapshot: () => [...exhaustedFailures],
    readSnapshot: () => [...exhaustedReads],
    restoreFailures: keys => { for (const key of keys || []) if (typeof key === 'string') exhaustedFailures.add(key); },
    restoreReads: keys => { for (const key of keys || []) if (typeof key === 'string') exhaustedReads.add(key); },
    get stopped() { return stopped; },
    get failureStopped() { return failureStopped; },
    observe(name, args, result, isError = false) {
      // Repeated failures also consume the bounded retry allowance. Successful
      // mutations are never treated as polling, even when their receipts match.
      if (!isError && !stateReads.has(name)) { resetObservations(); return false; }
      const key = callKey(name, args);
      const value = JSON.stringify(canonical({result, isError}, true));
      if (latest.has(key) && latest.get(key) !== value) resetObservations();
      latest.set(key, value);
      const fingerprint = key + '\n' + value;
      recent.push(fingerprint);
      if (recent.length > 12) recent.shift();
      const repeats = recent.filter(item => item === fingerprint).length;
      // A self-status read can never become awaitable by querying it again.
      const bound = name === 'task_status' && result?.awaitable === false ? 2 : 3;
      stopped = repeats >= bound;
      failureStopped = stopped && isError;
      if (failureStopped) exhaustedFailures.add(key);
      else if (stopped) exhaustedReads.add(key);
      return stopped;
    },
  };
}

export function restoreProgress(guard, messages) {
  const calls = new Map();
  for (const message of messages) {
    if (message.role === 'user' && message.source !== 'harness_control') guard.reset();
    if (message.role === 'assistant') {
      const parts = Array.isArray(message.content) ? message.content : [];
      const toolCalls = parts.filter(part => part.type === 'toolCall');
      for (const call of toolCalls) calls.set(call.id, call);
    }
    if (message.role !== 'toolResult') continue;
    const call = calls.get(message.toolCallId);
    if (!call) { guard.resetObservations(); continue; }
    const parts = message.content;
    try {
      if (parts?.length !== 1 || parts[0].type !== 'text') { guard.resetObservations(); continue; }
      let result;
      try { result = JSON.parse(parts[0].text); } catch { result = parts[0].text; }
      guard.observe(call.name, call.arguments, result, !!message.isError);
    } catch { guard.resetObservations(); }
  }
  return guard.stopped;
}
