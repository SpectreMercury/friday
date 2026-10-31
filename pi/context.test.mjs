import assert from 'node:assert/strict';
import test from 'node:test';
import {compactContext} from './context.mjs';

const request = {role: 'user', content: 'Read the complete evidence for this task.'};
function evidencePage(index, {legacy = false} = {}) {
  const id = `evidence-${index}`;
  const payload = {job_id: 'job-fixture', offset: index * 12000,
    next_offset: index === 4 ? null : (index + 1) * 12000,
    total_characters: 60000, text: 'evidence ' + 'x'.repeat(11970) + ' PAGE END', untrusted_content: true};
  return [
    {role: 'assistant', content: [{type: 'toolCall', id, name: 'task_evidence',
      arguments: {job_id: payload.job_id, offset: payload.offset}}]},
    {role: 'toolResult', toolCallId: id, ...(legacy ? {} : {toolName: 'task_evidence'}),
      content: [{type: 'text', text: JSON.stringify(payload)}]},
  ];
}

test('small contexts remain unchanged', () => {
  const original = [request, ...evidencePage(0)];
  assert.equal(compactContext(original), original);
});

for (const legacy of [false, true]) {
  test(`large contexts preserve complete evidence pages and cursors (legacy=${legacy})`, () => {
    const original = [request, ...Array.from({length: 5}, (_, i) => evidencePage(i, {legacy})).flat()];
    const snapshot = JSON.stringify(original);
    assert.ok(snapshot.length > 42000);
    const compact = compactContext(original);
    assert.ok(JSON.stringify(compact).length <= 42000);
    assert.equal(JSON.stringify(original), snapshot, 'the durable transcript must not be mutated');
    assert.equal(compact[0], request);
    assert.equal(compact.at(-1), original.at(-1), 'the newest page must remain complete');
    assert.equal(JSON.parse(compact.at(-1).content[0].text).next_offset, null);
    const calls = new Map(compact.filter(message => message.role === 'assistant')
      .flatMap(message => message.content).map(part => [part.id, part]));
    const results = compact.filter(message => message.role === 'toolResult');
    assert.equal(calls.size, results.length);
    for (const result of results) {
      const call = calls.get(result.toolCallId);
      assert.ok(call, 'retain the call paired with every retained result');
      const page = JSON.parse(result.content[0].text);
      assert.equal(page.job_id, call.arguments.job_id);
      assert.equal(page.offset, call.arguments.offset);
      assert.equal(page.total_characters, 60000);
      assert.ok(page.text.endsWith('PAGE END'));
      assert.deepEqual(result, original.find(message => message.role === 'toolResult' && message.toolCallId === result.toolCallId));
    }
  });
}

test('other large results retain the existing compaction behavior', () => {
  const original = [request,
    {role: 'assistant', content: [{type: 'toolCall', id: 'read', name: 'web_read', arguments: {url: 'https://example.test'}}]},
    {role: 'toolResult', toolCallId: 'read', toolName: 'web_read', content: [{type: 'text', text: 'y'.repeat(50000)}]},
    ...evidencePage(0)];
  const compact = compactContext(original);
  const read = compact.find(message => message.toolCallId === 'read');
  assert.equal(read.content[0].text.slice(0, 1400), 'y'.repeat(1400));
  assert.match(read.content[0].text, /模型视图截断/);
  assert.equal(compact.at(-1).content[0].text, original.at(-1).content[0].text);
});

test('a user-only history is bounded while the newest actual instruction stays complete', () => {
  const original = Array.from({length: 12}, (_, i) => ({role: 'user', source: 'user', source_job_id: 'input-' + i,
    content: 'Historical request ' + i + ': ' + '界'.repeat(5000)}));
  original.push({role: 'user', source: 'user', source_job_id: 'current', content: 'Use the corrected number 17, and explain why.'});
  original.push({role: 'user', source: 'harness_control', source_job_id: 'current', control_kind: 'review', content: 'Check the latest answer.'});
  const snapshot = JSON.stringify(original), compact = compactContext(original);
  assert.ok(JSON.stringify(compact).length <= 42000);
  assert.equal(JSON.stringify(original), snapshot);
  assert.equal(compact.find(m => m.source_job_id === 'current' && m.source === 'user'), original.at(-2));
  assert.equal(compact.at(-1), original.at(-1));
  const previews = compact.filter(m => m.source === 'historical_input_preview');
  assert.ok(previews.length > 0);
  assert.ok(previews.every(m => m.view_truncated === true && m.source_job_id && m.content.includes('task_evidence')));
});

test('many historical inputs cannot leave unbounded preview overhead', () => {
  const original = Array.from({length: 200}, (_, i) => ({role: 'user', source: 'user', source_job_id: 'history-' + i, content: 'x'.repeat(900)}));
  original.push({role: 'user', source: 'user', source_job_id: 'current', content: 'This current instruction must remain intact.'});
  const compact = compactContext(original);
  assert.ok(JSON.stringify(compact).length <= 42000);
  assert.equal(compact.at(-1), original.at(-1));
  assert.ok(compact.some(m => m.control_kind === 'context_summary' && m.content.includes('task_evidence')));
});

test('one oversized current input is explicitly marked as a partial view with source identity', () => {
  const original = [{role: 'user', source: 'user', source_job_id: 'large-current',
    content: 'REQUIRED BEGIN ' + '界'.repeat(30000) + ' REQUIRED END'}];
  const before = JSON.stringify(original), compact = compactContext(original);
  assert.ok(JSON.stringify(compact).length <= 42000);
  assert.equal(JSON.stringify(original), before);
  assert.equal(compact.length, 1);
  assert.equal(compact[0].source, 'user');
  assert.equal(compact[0].source_job_id, 'large-current');
  assert.equal(compact[0].view_truncated, true);
  assert.match(compact[0].content, /REQUIRED BEGIN/);
  assert.match(compact[0].content, /REQUIRED END/);
  assert.match(compact[0].content, /task_evidence/);
});

test('oversized mixed context never leaves an orphan tool-result message', () => {
  const original = [{role: 'user', source: 'user', source_job_id: 'old', content: 'Old facts '.repeat(9000)},
    {role: 'user', source: 'user', source_job_id: 'current', content: 'Current instruction'},
    {role: 'assistant', content: [{type: 'toolCall', id: 'large-call', name: 'write_file', arguments: {content: 'a'.repeat(50000)}}]},
    {role: 'toolResult', toolCallId: 'large-call', content: [{type: 'text', text: 'written'}]}];
  const compact = compactContext(original);
  assert.ok(JSON.stringify(compact).length <= 42000);
  const calls = new Set(compact.filter(m => m.role === 'assistant').flatMap(m => m.content).filter(p => p.type === 'toolCall').map(p => p.id));
  assert.ok(compact.filter(m => m.role === 'toolResult').every(m => calls.has(m.toolCallId)));
  assert.ok(compact.some(m => m.content === 'Current instruction'));
});

test('a legitimate 12k CJK request remains complete alongside bounded system metadata', () => {
  const current = {role: 'user', source: 'user', source_job_id: 'current', content: '界'.repeat(12000)};
  const original = [{role: 'system', content: 'System and tool metadata '.repeat(330)}, current];
  assert.ok(Buffer.byteLength(JSON.stringify(original), 'utf8') > 42000);
  const compact = compactContext(original);
  assert.equal(compact.at(-1), current);
  assert.equal(current.view_truncated, undefined);
});

test('the latest complete 12k CJK evidence page and its cursor remain readable', () => {
  const pages = Array.from({length: 5}, (_, i) => {
    const pair = evidencePage(i), data = JSON.parse(pair[1].content[0].text);
    data.text = '界'.repeat(12000);
    pair[1].content[0].text = JSON.stringify(data);
    return pair;
  }).flat();
  const original = [{role: 'system', content: 'System and tool metadata '.repeat(330)}, request, ...pages];
  const compact = compactContext(original);
  assert.equal(compact.at(-1), original.at(-1));
  const latest = JSON.parse(compact.at(-1).content[0].text);
  assert.equal(latest.text.length, 12000);
  assert.equal(latest.next_offset, null);
  assert.ok(compact.some(m => m.role === 'assistant' && m.content.some(p => p.id === compact.at(-1).toolCallId)));
});

test('imported reference data cannot displace the latest real user instruction', () => {
  const current = {role: 'user', source: 'user', source_job_id: 'current', content: 'Follow this real request.'};
  const original = [current, {role: 'user', source: 'reference', content: 'Reference document '.repeat(5000)}];
  const compact = compactContext(original);
  assert.ok(compact.includes(current));
  assert.equal(current.view_truncated, undefined);
  assert.ok(JSON.stringify(compact).length <= 42000);
});
