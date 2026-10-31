import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { renderMessage } from '../local_ai/pwa/message-format.js';

class SafeNode {
  constructor(type, name = '', content = '') {
    this.nodeType = type; this.tagName = name.toUpperCase(); this.children = []; this._text = content;
  }
  append(...nodes) { this.children.push(...nodes); }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(node => node.textContent).join(''); }
  set innerHTML(_) { throw new Error('HTML parsing is forbidden'); }
  set outerHTML(_) { throw new Error('HTML parsing is forbidden'); }
  insertAdjacentHTML() { throw new Error('HTML parsing is forbidden'); }
}
const document = {
  createElement: name => new SafeNode(1, name),
  createTextNode: text => new SafeNode(3, '', String(text)),
};
const render = text => renderMessage(text, document);
const descendants = node => node.children.flatMap(child => [child, ...descendants(child)]);
const tags = (node, tag) => descendants(node).filter(child => child.tagName === tag.toUpperCase());

test('plain answers and multiline paragraphs remain text with explicit line breaks', () => {
  const result = render('第一行\n第二行\n\n下一段');
  assert.equal(result.className, 'assistant-message');
  assert.deepEqual(result.children.map(node => node.tagName), ['P', 'P']);
  assert.equal(tags(result, 'br').length, 1);
  assert.equal(result.children[0].children[0].textContent, '第一行');
  assert.equal(result.children[0].children[2].textContent, '第二行');
  assert.equal(result.children[1].textContent, '下一段');
  assert.equal(render('').textContent, '');
  assert.equal(render(null).textContent, '');
});

test('renders modest headings, ordered and bullet lists, bold, and inline code', () => {
  const result = render('# Main title\n## Another title ##\n### Smaller title\n\n- **First** item\n- Use `literal()`\n\n3. Third\n4) Fourth');
  assert.deepEqual(result.children.map(node => node.tagName), ['H3', 'H3', 'H4', 'UL', 'OL']);
  assert.deepEqual(tags(result, 'h3').map(node => node.textContent), ['Main title', 'Another title']);
  assert.equal(tags(result, 'strong')[0].textContent, 'First');
  assert.equal(tags(result, 'code')[0].textContent, 'literal()');
  assert.deepEqual(tags(result, 'li').map(node => node.textContent), ['First item', 'Use literal()', 'Third', 'Fourth']);
  assert.equal(tags(result, 'ol')[0].start, 3);
});

test('code fences preserve code and never interpret markup or URLs inside them', () => {
  const payload = '<script>alert(1)</script>\n**not bold** [not linked](https://example.test)';
  const result = render('```html\n' + payload + '\n```\nAfter\n\n~~~\nunterminated <img src=x>');
  assert.equal(tags(result, 'pre').length, 2);
  assert.equal(tags(result, 'code')[0].textContent, payload + '\n');
  assert.equal(tags(result, 'code')[1].textContent, 'unterminated <img src=x>');
  assert.equal(tags(result, 'a').length, 0); assert.equal(tags(result, 'strong').length, 0);
  assert.equal(tags(result, 'script').length, 0); assert.equal(tags(result, 'img').length, 0);
});

test('only explicit HTTP(S) markdown links create protected external anchors', () => {
  const result = render('[Secure](https://example.test/path?q=1#part) and [HTTP](http://example.test/a) [Nested](https://example.test/wiki/Thing_(type))');
  const links = tags(result, 'a');
  assert.deepEqual(links.map(node => node.textContent), ['Secure', 'HTTP', 'Nested']);
  assert.deepEqual(links.map(node => node.href), ['https://example.test/path?q=1#part', 'http://example.test/a', 'https://example.test/wiki/Thing_(type)']);
  for (const link of links) { assert.equal(link.target, '_blank'); assert.equal(link.rel, 'noopener noreferrer'); }
  assert.equal(tags(render('https://example.test/plain'), 'a').length, 0, 'Plain URLs are not auto-embedded');
});

test('unsafe protocols, protocol-relative URLs, credentials and deceptive separators remain literal', () => {
  const bad = [
    'javascript:alert(1)', 'JaVaScRiPt:alert(1)', 'data:text/html,<script>alert(1)</script>', 'file:///private/data',
    '//example.test/steal', '/relative', 'https://user:password@example.test', 'https://user@example.test',
    'https://@example.test', 'https://example.test\\@attacker.test', 'https://example.test/ space',
    'https://example.test/\tpath', 'https://example.test/\u0000path', 'mailto:user@example.test',
  ];
  for (const url of bad) {
    const source = `[open](${url})`, result = render(source);
    assert.equal(tags(result, 'a').length, 0, url);
    assert.equal(result.textContent, source, url);
  }
});

test('raw HTML, image syntax and hostile labels cannot create executable or network-loading elements', () => {
  const html = '<img src="https://attacker.test/pixel" onerror="alert(1)"><script>alert(2)</script><svg onload="alert(3)">';
  const image = '![private image](https://attacker.test/pixel)';
  const result = render(html + '\n' + image + '\n[<img src=x onerror=alert(4)>](https://example.test)');
  for (const tag of ['img', 'script', 'svg', 'iframe', 'object', 'style']) assert.equal(tags(result, tag).length, 0);
  assert.ok(result.textContent.includes(html)); assert.ok(result.textContent.includes(image));
  assert.equal(tags(result, 'a')[0].textContent, '<img src=x onerror=alert(4)>');
  assert.equal(tags(result, 'a').length, 1, 'Image syntax never becomes an image or anchor');
});

test('malformed markup stays literal and excessive output falls back without losing content', () => {
  for (const text of ['**unfinished', '`unfinished', '[unfinished](https://example.test', '![unfinished', '*****', 'normal [broken']) {
    assert.equal(render(text).textContent, text);
  }
  const large = '**huge**' + 'x'.repeat(200001), manyNodes = '- item\n'.repeat(2200), adversarial = '['.repeat(90000);
  for (const text of [large, manyNodes, adversarial]) {
    const result = render(text);
    assert.equal(result.textContent, text);
    assert.ok(descendants(result).length <= 4000);
  }
  assert.match(render(manyNodes).className, /\bmessage-plain\b/, 'Plain fallback retains multiline presentation');
  // A run of spaces followed by a non-closing heading suffix must not cause
  // repeated regex scans over the same suffix.
  const heading = '# title' + ' '.repeat(60000) + 'last';
  assert.equal(render(heading).textContent, heading.slice(2));
});

test('formatter source has no HTML interpretation, resource embedding or external dependencies', async () => {
  const source = await readFile(new URL('../local_ai/pwa/message-format.js', import.meta.url), 'utf8');
  assert.doesNotMatch(source, /\b(?:innerHTML|outerHTML|insertAdjacentHTML|eval)\b|document\.write\s*\(/);
  assert.doesNotMatch(source, /\bimport\s|\bfetch\s*\(|createElement\(['"](?:img|iframe|script|object)['"]\)/);
});
