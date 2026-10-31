// A small text formatter, not an HTML or full Markdown renderer. User/model
// content reaches the DOM only through text nodes; links never embed resources.
const MAX_FORMAT_LENGTH = 200000;
const MAX_FORMAT_LINES = 5000;
const MAX_NODES = 4000;
const FORMAT_LIMIT = Symbol('format limit');

function externalURL(value) {
  if (!/^https?:\/\//i.test(value) || /[\s\\\u0000-\u001f\u007f]/.test(value)) return null;
  const authority = value.slice(value.indexOf('://') + 3).split(/[/?#]/, 1)[0];
  if (authority.includes('@')) return null;
  try {
    const url = new URL(value);
    if (!['https:', 'http:'].includes(url.protocol) || !url.hostname || url.username || url.password) return null;
    return url.href;
  } catch { return null; }
}

export function renderMessage(value, document = globalThis.document) {
  const text = String(value ?? '');
  const container = document.createElement('div'); container.className = 'assistant-message';
  const plain = () => { container.className = 'assistant-message message-plain'; container.textContent = text; return container; };
  if (!text) return container;
  if (!text.trim()) return plain();
  if (text.length > MAX_FORMAT_LENGTH) return plain();
  const lines = text.split('\n');
  if (lines.length > MAX_FORMAT_LINES) return plain();
  let budget = MAX_NODES;
  const element = tag => { if (--budget < 0) throw FORMAT_LIMIT; return document.createElement(tag); };
  const literal = (parent, content) => {
    if (!content) return;
    if (--budget < 0) throw FORMAT_LIMIT;
    parent.append(document.createTextNode(content));
  };

  function inline(parent, source) {
    let index = 0, plainStart = 0, noLinkDelimiter = false;
    const flush = end => literal(parent, source.slice(plainStart, end));
    while (index < source.length) {
      if (source[index] === '`') {
        const end = source.indexOf('`', index + 1);
        if (end < 0) break;
        if (end > index + 1) {
          flush(index); const node = element('code'); node.textContent = source.slice(index + 1, end); parent.append(node);
          index = end + 1; plainStart = index; continue;
        }
        index = end + 1; continue;
      }
      if (source.startsWith('**', index)) {
        const end = source.indexOf('**', index + 2);
        if (end < 0) break;
        if (end > index + 2) {
          flush(index); const node = element('strong'); node.textContent = source.slice(index + 2, end); parent.append(node);
          index = end + 2; plainStart = index; continue;
        }
        index = end + 2; continue;
      }
      const image = source[index] === '!' && source[index + 1] === '[';
      if (!noLinkDelimiter && (source[index] === '[' || image)) {
        const start = index + (image ? 1 : 0), delimiter = source.indexOf('](', start + 1);
        if (delimiter < 0) { noLinkDelimiter = true; index++; continue; }
        // Consume a candidate once, including balanced URL parentheses, so
        // repeated unmatched '[' cannot trigger repeated suffix scans.
        let end = delimiter + 2, depth = 1;
        for (; end < source.length; end++) {
          if (source[end] === '(') depth++;
          else if (source[end] === ')' && --depth === 0) break;
        }
        if (depth !== 0) break;
        const href = image ? null : externalURL(source.slice(delimiter + 2, end));
        flush(index);
        if (href) {
          const link = element('a'); link.textContent = source.slice(start + 1, delimiter);
          link.href = href; link.target = '_blank'; link.rel = 'noopener noreferrer'; parent.append(link);
        } else literal(parent, source.slice(index, end + 1));
        index = end + 1; plainStart = index; continue;
      }
      index++;
    }
    literal(parent, source.slice(plainStart));
  }

  try {
    let paragraph = null, list = null, listKind = null;
    for (let index = 0; index < lines.length; index++) {
      const line = lines[index].replace(/\r$/, '');
      if (!line.trim()) { paragraph = null; list = null; listKind = null; continue; }
      const fence = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line);
      if (fence && !(fence[1][0] === '`' && fence[2].includes('`'))) {
        paragraph = null; list = null; listKind = null;
        const closing = new RegExp('^ {0,3}' + fence[1][0] + '{' + fence[1].length + ',}[ \\t]*$');
        const content = []; let closed = false;
        while (++index < lines.length) {
          const codeLine = lines[index].replace(/\r$/, '');
          if (closing.test(codeLine)) { closed = true; break; }
          content.push(codeLine);
        }
        const pre = element('pre'), code = element('code');
        code.textContent = content.join('\n') + (closed && content.length ? '\n' : ''); pre.append(code); container.append(pre); continue;
      }
      const heading = /^ {0,3}(#{1,6})[ \t]+(.+)$/.exec(line);
      if (heading) {
        paragraph = null; list = null; listKind = null;
        let title = heading[2].trimEnd(), end = title.length;
        while (end > 0 && title[end - 1] === '#') end--;
        if (end < title.length && end > 0 && /[ \t]/.test(title[end - 1])) title = title.slice(0, end).trimEnd();
        const node = element(heading[1].length <= 2 ? 'h3' : 'h4'); inline(node, title); container.append(node); continue;
      }
      const item = /^ {0,3}(?:([-+*])|(\d{1,9})[.)])[ \t]+(.*)$/.exec(line);
      if (item) {
        paragraph = null;
        const kind = item[1] ? 'ul' : 'ol';
        if (!list || listKind !== kind) {
          list = element(kind); listKind = kind;
          if (kind === 'ol') list.start = Number(item[2]);
          container.append(list);
        }
        const node = element('li'); inline(node, item[3]); list.append(node); continue;
      }
      list = null; listKind = null;
      if (!paragraph) { paragraph = element('p'); container.append(paragraph); }
      else paragraph.append(element('br'));
      inline(paragraph, line);
    }
  } catch (error) {
    if (error !== FORMAT_LIMIT) throw error;
    // Preserve the complete answer if formatting would allocate excessive DOM.
    plain();
  }
  return container;
}
