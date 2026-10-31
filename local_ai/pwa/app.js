import { API, ApiError, Drafts, Selection, sessionPath, statusText, canCancel, resultText, byteSize, safeExternalURL, trackAppViewport } from "/pwa/core.js";
import { VoiceController, VoiceMessageRecorder } from "/pwa/voice.js";
import { renderMessage } from "/pwa/message-format.js";
import { mediaDescriptor, conversationEntries, MediaPreviews } from "/pwa/media.js";
import { createDramas } from "/pwa/drama.js";

const stopViewportTracking = trackAppViewport();
const main = document.querySelector("#main"), sheet = document.querySelector("#sheet");
const sheetContent = sheet.querySelector(".sheet-content"), banner = document.querySelector("#voice-banner");
const drafts = new Drafts(), selection = new Selection();
const state = { auth: null, sessions: [], jobs: new Map(), approvals: new Map(), tab: "sessions", taskFilter: "all", cursor: 0, polling: false, closed: false, showArchived: false };
const mediaPreviews = new MediaPreviews({ active: () => !state.closed });
const downloadURLs = new Set();
let toastTimer, pollTimer, recordingView = null, voiceState = {}, memoState = {}, pageGeneration = 0;
const api = new API({ onUnauthorized: expire });
const request = api.request.bind(api);
const voice = new VoiceController({ api: request, onChange: updateVoice, onEvent: voiceEvent });
const recorder = new VoiceMessageRecorder({ api: request, onChange: updateMemo });
voiceState = voice.snapshot; memoState = recorder.snapshot;

// Untrusted content is rendered with DOM text nodes, including formatted replies.
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}
function button(text, handler, className = "secondary") {
  const node = el("button", className, text); node.type = "button";
  node.addEventListener("click", event => {
    try { Promise.resolve(handler(event)).catch(report); } catch (error) { report(error); }
  });
  return node;
}
function section(title, ...children) { const node = el("section"); node.append(el("h3", "", title), ...children); return node; }
function row(...children) { const node = el("div", "row"); node.append(...children); return node; }
function hint(text) { return el("p", "hint", text); }
function disclosure(title, ...content) {
  const node = el("details", "disclosure"); node.append(el("summary", "", title), ...content); return node;
}
function emptyState(title, copy = "", showAvatar = false) {
  const node = el("div", "empty");
  if (showAvatar) {
    const avatar = el("img", "friday-avatar"); avatar.src = "/pwa/friday-icon-192.png"; avatar.alt = "Friday"; avatar.width = 48; avatar.height = 48;
    node.append(avatar);
  }
  node.append(el("p", "empty-title", title));
  if (copy) node.append(el("p", "empty-copy", copy)); return node;
}
function icon(name) {
  const paths = { plus: "M12 5v14M5 12h14", back: "m14 6-6 6 6 6", arrow: "m10 6 6 6-6 6", up: "M12 19V5m-6 6 6-6 6 6", file: "M8 3h7l4 4v14H5V3h3m6 0v5h5M8 12h8M8 16h6", mic: "M9 5a3 3 0 0 1 6 0v7a3 3 0 0 1-6 0V5m-3 6v1a6 6 0 0 0 12 0v-1M12 18v4m-3 0h6", settings: "M5 7h14M5 17h14M9 4v6m6 4v6", more: "M5 12h.01M12 12h.01M19 12h.01", refresh: "M20 7v5h-5M4 17v-5h5M6.1 7a7 7 0 0 1 11.5-1L20 9M4 15l2.4 3A7 7 0 0 0 17.9 17", compose: "M12 4H6a3 3 0 0 0-3 3v11a3 3 0 0 0 3 3h11a3 3 0 0 0 3-3v-6M15 4l4 4M9 14l-1 4 4-1 9-9a2.8 2.8 0 0 0-4-4l-8 10Z", copy: "M9 8V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-3M5 8h9a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-9a2 2 0 0 1 2-2Z" };
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  for (const [key, value] of Object.entries({ viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": "1.6", "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", class: "ui-icon" })) svg.setAttribute(key, value);
  if (name === "more") {
    for (const x of [5, 12, 19]) {
      const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      for (const [key, value] of Object.entries({ cx: x, cy: 12, r: 1.65, fill: "currentColor", stroke: "none" })) dot.setAttribute(key, value);
      svg.append(dot);
    }
    return svg;
  }
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path"); path.setAttribute("d", paths[name]); svg.append(path); return svg;
}
function iconButton(label, name, handler, className = "quiet icon-button", text = false) {
  const node = button("", handler, className); node.setAttribute("aria-label", label); node.title = label; node.append(icon(name));
  if (text) node.append(el("span", "", label)); return node;
}
function field(label, input) {
  const node = el("label", "field"); node.append(el("span", "", label), input); return node;
}
function input(value = "", placeholder = "") { const node = el("input"); node.value = value; node.placeholder = placeholder; node.maxLength = 100; return node; }
function checkbox(title, checked = false, description = "") {
  const control = el("input"); control.type = "checkbox"; control.checked = checked;
  const label = el("label", "check-row"), text = el("span", "grow", title);
  if (description) text.append(el("span", "hint", description));
  label.append(control, text); return { label, control };
}
function timeText(value) { return value ? new Date(value * 1000).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : ""; }
function currentSession() { return state.sessions.find(session => session.id === selection.id); }
function sessionName(id) { return state.sessions.find(session => session.id === id)?.name || "原会话"; }
function skillLabel(name) { return ({ research: "资料研究", documents: "文档整理", "image-prompting": "图像创作", "h3-prompt-writing": "视频创作" })[name] || name; }
function toast(text, error = false) {
  if (state.closed) return;
  const node = document.querySelector("#toast"); node.textContent = text; node.hidden = false; node.classList.toggle("error", error);
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { node.hidden = true; }, error ? 6500 : 3500);
}
function report(error) {
  if (state.closed || error?.name === "AbortError" || error?.status === 401) return;
  if (sheet.open) sheetMessage(error?.message || "暂时无法完成，请重试。");
  else toast(error?.message || "暂时无法完成，请重试。", true);
}
function updateConnection(online) {
  const node = document.querySelector("#connection-state"); node.textContent = online ? "已连接" : "等待网络"; node.classList.toggle("offline", !online);
  const workspace = document.querySelector(".workspace-network"); if (workspace) workspace.hidden = online;
}
function closeSheet() { mediaPreviews.releaseWithin(sheetContent); sheet.close(); sheetContent.replaceChildren(); recordingView = null; }
function openSheet(title) {
  recordingView = null; mediaPreviews.releaseWithin(sheetContent); sheetContent.replaceChildren();
  const close = button("×", closeSheet, "close"); close.setAttribute("aria-label", "关闭");
  const titleNode = el("h2", "grow", title); titleNode.id = "sheet-title"; sheet.setAttribute("aria-labelledby", titleNode.id);
  const heading = row(titleNode, close); heading.className = "sheet-heading";
  sheetContent.append(heading); if (!sheet.open) sheet.showModal();
}
sheet.addEventListener("cancel", () => { mediaPreviews.releaseWithin(sheetContent); sheetContent.replaceChildren(); recordingView = null; });
function sheetMessage(message, isError = true) {
  let node = sheetContent.querySelector(".sheet-message");
  if (!node) { node = el("p", "notice sheet-message"); sheetContent.append(node); }
  node.classList.toggle("error", isError); node.textContent = message;
}
async function withButton(node, task) {
  node.disabled = true;
  try { return await task(); } finally { if (!state.closed && !node.dataset.conflict) node.disabled = false; }
}

// Confirmation is a separate native <dialog>, so a file/settings sheet keeps
// its form and focus. Escape, dismissal and sign-out always resolve as cancel.
let pendingConfirmation = null;
const confirmation = el("dialog", "sheet confirmation-sheet"); confirmation.id = "confirmation";
document.body.append(confirmation);
function confirmAction({ title, detail, action, danger = false }) {
  pendingConfirmation?.(false);
  if (state.closed) return Promise.resolve(false);
  return new Promise(resolve => {
    let settled = false;
    const finish = accepted => {
      if (settled) return; settled = true; pendingConfirmation = null;
      confirmation.close(); confirmation.replaceChildren(); resolve(accepted && !state.closed);
    };
    pendingConfirmation = finish;
    const content = el("div", "sheet-content"), heading = el("h2", "", title); heading.id = "confirmation-title";
    const detailNode = el("p", "confirm-copy", detail); detailNode.id = "confirmation-detail";
    confirmation.setAttribute("aria-labelledby", heading.id); confirmation.setAttribute("aria-describedby", detailNode.id);
    const actions = el("div", "confirm-actions");
    const cancel = button("取消", () => finish(false), "outline");
    actions.append(cancel, button(action, () => finish(true), danger ? "danger" : "primary"));
    content.append(heading, detailNode, actions); confirmation.replaceChildren(content); confirmation.showModal(); cancel.focus();
  });
}
confirmation.addEventListener("cancel", event => { event.preventDefault(); pendingConfirmation?.(false); });
function replaceSession(updated) {
  state.sessions = state.sessions.filter(item => item.id !== updated.id); state.sessions.unshift(updated);
}
async function refreshSessions() { const data = await request("/v1/sessions"); state.sessions = data.items; }
async function refreshWorkspace(ticket = selection.ticket(), { render = true } = {}) {
  if (!ticket.id) return;
  const [messages, approvals] = await Promise.all([
    request(sessionPath(ticket.id, "/messages")), request(sessionPath(ticket.id, "/approvals")),
  ]);
  state.jobs.set(ticket.id, messages.items); state.approvals.set(ticket.id, approvals.items);
  if (render && selection.current(ticket) && state.tab === "sessions") renderMessages();
}

const dramas = createDramas({ el, button, hint, heading: (...args) => heading(...args), field, request, report, toast, confirmAction, openSheet, closeSheet, sheetContent,
  withButton, emptyState, timeText, renderPage: () => renderPage(), generation: () => pageGeneration });
function renderPage() {
  if (state.closed || !state.auth) return;
  pageGeneration++;
  const shell = document.querySelector("#app"); shell.dataset.view = state.tab;
  shell.classList.toggle("in-workspace", state.tab === "sessions" && !!selection.id);
  for (const tab of document.querySelectorAll("[data-tab]")) {
    if (tab.dataset.tab === state.tab) tab.setAttribute("aria-current", "page"); else tab.removeAttribute("aria-current");
  }
  mediaPreviews.releaseWithin(main); main.replaceChildren();
  if (state.tab === "sessions") selection.id && currentSession() ? renderWorkspace() : renderSessions();
  else if (state.tab === "tasks") renderTasks();
  else if (state.tab === "dramas") { const generation = pageGeneration; dramas.render(main, generation).catch(report); }
  else renderSettings();
}
function heading(title, subtitle, action) {
  const text = el("div", "grow"); text.append(el("h1", "", title)); if (subtitle) text.append(hint(subtitle));
  const node = el("div", "page-heading"); node.append(text); if (action) node.append(action); return node;
}
function renderSessions() {
  main.setAttribute("aria-label", "会话");
  const active = state.sessions.filter(session => !session.archived);
  const grid = el("div", "session-grid");
  for (const session of active) grid.append(sessionCard(session));
  main.append(grid);
  if (!active.length) main.append(emptyState("还没有会话", "点右上角，开始一段对话。"));
  const archived = state.sessions.filter(session => session.archived);
  if (archived.length) {
    const show = button(`${state.showArchived ? "收起" : "查看"}已归档会话 · ${archived.length}`, () => { state.showArchived = !state.showArchived; renderPage(); }, "quiet small");
    main.append(el("div", "section-caption", "归档"), show);
    if (state.showArchived) {
      const old = el("div", "session-grid"); for (const session of archived) old.append(sessionCard(session)); main.append(old);
    }
  }
}
function sessionCard(session) {
  const node = button("", () => selectSession(session.id), `session-card${session.archived ? " archived" : ""}`);
  const body = el("div", "session-card-body"), title = el("h3", "session-title", session.name); title.title = session.name;
  const loaded = state.jobs.get(session.id), last = session.last || loaded?.at(-1);
  const top = el("div", "session-card-top"); top.append(title, el("time", "", timeText(last?.created || session.created))); body.append(top);
  const summary = last?.title || last?.prompt || (session.skill_names.length ? session.skill_names.map(skillLabel).join(" · ") : "新的对话");
  body.append(el("p", "session-meta", summary));
  const foot = el("div", "session-card-foot");
  if (session.waiting) foot.append(el("span", "attention", `${session.waiting} 项待确认`));
  if (session.active) foot.append(el("span", "", `${session.active} 项进行中`));
  else if (last && !session.waiting) foot.append(el("span", "", statusText(last)));
  if (session.archived) foot.append(el("span", "", "已归档"));
  if (session.management) foot.append(el("span", "", "Mac 管理"));
  if (foot.childElementCount) body.append(foot);
  node.append(body); return node;
}
async function selectSession(id) {
  if (state.closed) return;
  state.tab = "sessions"; const ticket = selection.select(id); renderPage();
  try { await refreshWorkspace(ticket); } catch (error) { if (selection.current(ticket)) report(error); }
}
async function createSession() {
  openSheet("新建会话"); const name = input("", "给这段对话起个名字");
  const submit = button("创建会话", () => withButton(submit, async () => {
    if (!name.value.trim()) { name.focus(); return; }
    const session = await request("/v1/sessions", { method: "POST", body: { name: name.value.trim() } });
    replaceSession(session); closeSheet(); await selectSession(session.id);
  }), "primary");
  sheetContent.append(field("会话名称", name), submit,
    disclosure("关于新会话", hint("对话、文件与权限各自独立。创建后可在会话设置中添加技能和资源。"))); name.focus();
}

function renderWorkspace() {
  const session = currentSession(), container = el("div", "workspace-container");
  main.setAttribute("aria-label", session.name);
  const back = iconButton("返回会话列表", "back", () => { selection.select(null); renderPage(); }, "back icon-button");
  const settings = iconButton("会话设置", "more", () => workspaceSettings(session.id), "quiet icon-button");
  const title = el("h1", "workspace-title", session.name); title.title = session.name;
  const heading = el("div", "workspace-heading grow");
  const subtitle = session.management ? "Friday · Mac 管理" : session.skill_names.length ? `Friday · ${session.skill_names.length} 项技能` : "Friday · 本地助手";
  heading.append(title, el("p", "workspace-subtitle", subtitle));
  const actions = el("div", "workspace-actions"); actions.append(iconButton("新建会话", "compose", createSession, "quiet icon-button"), settings);
  const top = el("div", "workspace-top"); top.append(back, heading, actions);
  const context = el("div", "workspace-context");
  if (session.archived) context.append(el("span", "badge", "已归档"));
  const network = el("span", "workspace-network badge warn", "等待网络"); network.hidden = !document.querySelector("#connection-state").classList.contains("offline");
  context.append(network);
  const messages = el("div", "messages"); messages.id = "messages"; messages.setAttribute("aria-label", "对话记录");
  const approvals = el("div", "stack"); approvals.id = "approvals";
  const stream = el("div", "conversation-content"); stream.tabIndex = 0; stream.setAttribute("role", "region"); stream.setAttribute("aria-label", "对话与待确认操作");
  stream.append(messages, approvals); container.append(top, context, stream, composer(session)); main.append(container); renderMessages();
}
function renderMessages() {
  const list = document.querySelector("#messages"), approvals = document.querySelector("#approvals");
  if (!list || !approvals) return;
  const stream = list.parentElement;
  const follow = !list.dataset.stamp || list.dataset.stamp === "null" || stream.scrollHeight - stream.scrollTop - stream.clientHeight < 80;
  const items = state.jobs.get(selection.id);
  const stamp = JSON.stringify(items || null);
  if (list.dataset.stamp !== stamp) {
    list.dataset.stamp = stamp;
    const entries = conversationEntries(items, selection.id);
    if (!items || !entries.length) {
      mediaPreviews.releaseWithin(list);
      list.replaceChildren(!items ? el("p", "empty", "正在读取此会话…") : emptyState("想从哪里开始？", "", true));
    } else {
      const previous = new Map([...list.children].map(node => [node.dataset.key, node]));
      const nodes = entries.map(entry => {
        const entryStamp = JSON.stringify(entry), old = previous.get(String(entry.key));
        if (old?.dataset.stamp === entryStamp) return old;
        const node = messageCard(entry); node.dataset.key = entry.key; node.dataset.stamp = entryStamp; return node;
      });
      // Keep unchanged nodes in place so a later task update does not reset a
      // playing video, its controls, or its current download position.
      for (const child of [...list.children]) if (!nodes.includes(child)) child.remove();
      nodes.forEach((node, index) => { if (list.children[index] !== node) list.insertBefore(node, list.children[index] || null); });
      mediaPreviews.prune();
    }
  }
  const pending = state.approvals.get(selection.id) || [], approvalStamp = JSON.stringify(pending);
  if (approvals.dataset.stamp !== approvalStamp) {
    approvals.dataset.stamp = approvalStamp; approvals.replaceChildren();
    for (const item of pending) approvals.append(approvalCard(selection.id, item));
  }
  if (follow) stream.scrollTop = stream.scrollHeight;
}
function messageCard({ job, artifacts }) {
  const node = el("article", "message");
  if (!job) {
    node.append(el("p", "imported-label", "生成的内容"));
    for (const artifact of artifacts) node.append(artifactCard(artifact));
    return node;
  }
  if (job.kind === "imported") node.append(el("p", "imported-label", "导入的资料"));
  if (job.prompt) node.append(el("div", "user-message", job.prompt));
  if (job.outcome === "continued") { node.append(hint("已并入上一条正在进行的任务。")); return node; }
  if (job.todo?.length && canCancel(job)) {
    const list = el("ul", "todo-list");
    for (const item of job.todo) list.append(el("li", `todo-${item.status}`, item.content));
    node.append(list);
  }
  const reply = resultText(job.result);
  if (reply) node.append(renderMessage(reply));
  if (job.error) node.append(el("p", "text-block error-text", job.error));
  const footer = el("div", "message-footer");
  if (reply) footer.append(iconButton("复制回复", "copy", async () => {
    try { await navigator.clipboard.writeText(reply); toast("已复制"); }
    catch { toast("无法自动复制，请长按正文选择文字。", true); }
  }, "quiet icon-button"));
  if (!["succeeded", "completed", "done"].includes(job.status) || ["needs_input", "blocked", "partial", "paused"].includes(job.outcome)) footer.append(el("span", `message-status${job.status === "failed" ? " error-text" : ""}`, statusText(job)));
  footer.append(el("time", "", timeText(job.created)));
  if (canCancel(job)) footer.append(button("取消任务", () => cancelJob(job), "small quiet"));
  if (job.outcome === "paused") footer.append(button("继续", () => {
    const textarea = document.querySelector("#message-input"); if (textarea) { textarea.value = "继续"; textarea.dispatchEvent(new Event("input")); textarea.focus(); }
  }, "small quiet"));
  if (["needs_input", "blocked"].includes(job.outcome)) footer.append(button("补充要求", () => {
    const textarea = document.querySelector("#message-input"); if (textarea) { textarea.focus(); textarea.scrollIntoView({ block: "center" }); }
  }, "small quiet"));
  for (const artifact of artifacts) node.append(artifactCard(artifact));
  node.append(footer); return node;
}
function artifactCard(job) {
  const descriptor = mediaDescriptor(job, selection.id);
  if (!descriptor) return iconButton(job.artifact_filename || "下载结果", "file", () => downloadArtifact(job), "artifact-link", true);
  const card = el("div", "media-card"); card.append(mediaPreviews.get("conversation", descriptor));
  const caption = el("div", "media-caption"); caption.append(el("span", "media-name", descriptor.name), button("下载原文件", () => downloadArtifact(job), "small quiet"));
  card.append(caption); return card;
}
function attachmentSheet(id) {
  openSheet("添加到会话");
  const list = el("div", "attachment-options");
  list.append(iconButton("文件", "file", () => fileSheet(id), "attachment-option", true),
    iconButton("技能与资源", "settings", () => workspaceSettings(id), "attachment-option", true),
    iconButton("语音", "mic", () => voiceSheet(id), "attachment-option", true));
  sheetContent.append(list);
}
function composer(session) {
  const draft = drafts.get(session.id), form = el("form", "composer");
  const textarea = el("textarea"); textarea.id = "message-input"; textarea.placeholder = "发送消息";
  textarea.value = draft.text; textarea.maxLength = 30000; textarea.rows = 1; textarea.setAttribute("aria-label", `发给 ${session.name} 的消息`);
  textarea.disabled = draft.sending || draft.uncertain;
  const fitDraft = () => {
    if (!textarea.isConnected) return;
    textarea.rows = 1;
    while (textarea.rows < 5 && textarea.scrollHeight > textarea.clientHeight + 1) textarea.rows++;
  };
  const line = el("div", "composer-line");
  line.append(iconButton("添加附件与工具", "plus", () => attachmentSheet(session.id), "quiet icon-button"), textarea, iconButton("语音", "mic", () => voiceSheet(session.id), "quiet icon-button"));
  const submit = el("button", "primary send", draft.sending ? "发送中" : draft.uncertain ? "重试" : ""); submit.type = "submit"; submit.disabled = draft.sending || !draft.text.trim();
  submit.setAttribute("aria-label", draft.uncertain ? "重试发送" : "发送"); if (!draft.sending && !draft.uncertain) submit.append(icon("up"));
  const updateDraft = () => { submit.hidden = !draft.sending && !textarea.value.trim(); submit.disabled = draft.sending || !textarea.value.trim(); fitDraft(); };
  textarea.addEventListener("input", () => { drafts.set(session.id, textarea.value); updateDraft(); });
  submit.hidden = !draft.sending && !draft.text.trim();
  requestAnimationFrame(updateDraft);
  line.append(submit); form.append(line);
  if (draft.uncertain) form.append(el("p", "composer-hint", "尚未确认是否发送成功。重试不会重复提交。"));
  form.addEventListener("submit", event => { event.preventDefault(); sendMessage(session.id).catch(report); });
  return form;
}
async function sendMessage(id) {
  const pending = drafts.begin(id); if (!pending) return;
  updateComposer(id);
  try {
    const job = await request(sessionPath(pending.sessionID, "/messages"), { method: "POST", body: { text: pending.text, request_id: pending.request_id } });
    drafts.complete(id, pending.request_id);
    const items = state.jobs.get(id) || []; state.jobs.set(id, [...items.filter(item => item.id !== job.id), job]);
    if (selection.id === id && state.tab === "sessions") renderMessages(); else toast(`已发送到「${sessionName(id)}」`);
  } catch (error) {
    drafts.failed(id, pending.request_id, !(error instanceof ApiError) || error.status >= 500 || error.status === 0); throw error;
  } finally { if (!state.closed) updateComposer(id); }
}
function updateComposer(id) {
  if (selection.id !== id || state.tab !== "sessions") return;
  const old = main.querySelector(".composer"), session = currentSession(); if (old && session) old.replaceWith(composer(session));
}
async function cancelJob(job) {
  if (!await confirmAction({ title: "停止这项任务？", detail: `「${sessionName(job.session_id)}」中的这项任务将停止执行。已完成的内容会保留。`, action: "停止任务", danger: true })) return;
  await request(sessionPath(job.session_id, `/tasks/${encodeURIComponent(job.id)}/cancel`), { method: "POST", body: {} });
  toast("已请求停止任务。");
  await refreshWorkspace({ id: job.session_id, generation: selection.generation }); if (state.tab === "tasks") renderPage();
}
function approvalCard(id, approval) {
  const node = el("div", "approval-card");
  node.append(el("h3", "", "请确认这项操作"), el("p", "prewrap", approval.description), hint(`授权仅限「${sessionName(id)}」的本次请求。`));
  const approve = button("允许一次", () => decide("approved"), "primary small"), deny = button("拒绝", () => decide("denied"), "outline small");
  async function decide(decision) {
    approve.disabled = true; deny.disabled = true;
    try {
      await request(sessionPath(id, `/approvals/${encodeURIComponent(approval.token)}`), { method: "POST", body: { decision } });
      await refreshWorkspace({ id, generation: selection.generation });
    } finally { approve.disabled = false; deny.disabled = false; }
  }
  node.append(row(approve, deny)); return node;
}
async function renderTasks() {
  const generation = pageGeneration;
  main.setAttribute("aria-label", "任务");
  main.append(heading("任务", "", iconButton("刷新任务", "refresh", () => renderPage(), "quiet icon-button")));
  const filters = el("div", "task-filters"); filters.setAttribute("aria-label", "筛选任务");
  for (const [key, label] of [["all", "全部"], ["active", "进行中"], ["attention", "待处理"], ["done", "已完成"]]) {
    const control = button(label, () => { state.taskFilter = key; renderPage(); }, "filter-button");
    control.setAttribute("aria-pressed", String(state.taskFilter === key)); filters.append(control);
  }
  main.append(filters);
  const list = el("div", "task-list"); list.append(el("p", "empty", "正在读取任务…")); main.append(list);
  try {
    const data = await request("/v1/tasks"); if (generation !== pageGeneration || state.closed) return;
    const items = data.items.filter(job => state.taskFilter === "all" || (state.taskFilter === "active" ? ["queued", "pending", "running"].includes(job.status) : state.taskFilter === "attention" ? ["waiting", "waiting_approval", "failed"].includes(job.status) || ["needs_input", "blocked", "partial"].includes(job.outcome) : ["succeeded", "completed", "done"].includes(job.status) && !["needs_input", "blocked", "partial"].includes(job.outcome)));
    list.replaceChildren(); if (!items.length) list.append(emptyState("暂无任务", state.taskFilter === "all" ? "在会话中开始，进展会显示在这里。" : "这个分类中还没有任务。"));
    for (const job of items) {
      const card = el("article", "task-card"), top = row(el("span", "task-session grow", sessionName(job.session_id)), el("span", "badge", statusText(job)));
      card.append(top, el("p", "task-title", job.title || job.prompt || "任务"));
      if (job.result || job.error) card.append(el("p", "task-preview", resultText(job.result) || job.error));
      const footer = row(hint(timeText(job.created)), button("查看会话", () => selectSession(job.session_id), "small quiet")); footer.classList.add("task-footer");
      if (canCancel(job)) footer.append(button("取消", () => cancelJob(job), "small outline"));
      card.append(footer); list.append(card);
    }
  } catch (error) { if (generation === pageGeneration) list.replaceChildren(el("p", "empty", "暂时无法读取任务，请刷新重试。")); report(error); }
}

async function workspaceSettings(id) {
  openSheet(`会话设置 · ${sessionName(id)}`); const loading = el("p", "hint", "正在读取…"); sheetContent.append(loading);
  try {
    const [session, catalog] = await Promise.all([request(sessionPath(id)), request("/v1/skills")]);
    if (!sheet.open || !loading.isConnected) return;
    loading.remove(); replaceSession(session);
    const name = input(session.name), rename = button("保存名称", () => mutate(rename, { name: name.value.trim() }), "outline small");
    sheetContent.append(field("名称", name), rename);
    let revision = session.revision;
    const skillSection = section("技能", hint("为这个会话选择需要的能力。"));
    const skillControls = catalog.items.map(skill => {
      const item = checkbox(skillLabel(skill.name), session.skill_names.includes(skill.name), skill.description || ""); skillSection.append(item.label); return { name: skill.name, ...item };
    });
    if (!skillControls.length) skillSection.append(hint("Mac 的技能库还没有可用技能。"));
    const saveSkills = button("保存技能选择", () => withButton(saveSkills, async () => {
      try {
        const updated = await request(sessionPath(id, "/skills"), { method: "PUT", body: { revision, skill_names: skillControls.filter(item => item.control.checked).map(item => item.name) } });
        revision = updated.revision; replaceSession(updated); toast("此会话的技能已更新。"); if (selection.id === id) renderPage();
      } catch (error) { handleConflict(error); }
    }), "secondary small"); skillSection.append(saveSkills, disclosure("技能与任务版本", hint("修改只影响新任务。执行中与恢复的任务保留原版本；技能本身不授予文件或设备权限。"))); sheetContent.append(skillSection);
    const resources = section("资料与连接", hint("外部文档的修改会直接写入原件。"));
    resources.append(button("连接的账号与文件", () => connectorSheet(id), "outline"), button("会话文件", () => fileSheet(id), "outline")); sheetContent.append(resources);
    if (state.auth.account.is_admin) {
      const management = checkbox("允许管理 Mac", session.management, "仅限诊断与预设恢复操作。需要批准的操作会单独确认。");
      const saveManagement = button("保存管理权限", () => mutate(saveManagement, { management: management.control.checked }), "outline small");
      const controls = section("Mac 管理", management.label, saveManagement);
      if (session.management) controls.append(button("查看本机状态", () => hostStatus(id), "quiet small")); sheetContent.append(controls);
    }
    const archive = button(session.archived ? "恢复会话" : "归档会话", () => mutate(archive, { archived: !session.archived }, true), "outline");
    const clear = button("清除对话上下文", async () => {
      if (!await confirmAction({ title: "清除对话上下文？", detail: `「${session.name}」后续不再引用之前的对话。会话文件会保留，其他会话不受影响。请先结束当前任务。`, action: "清除上下文", danger: true })) return;
      await withButton(clear, async () => {
        await request(sessionPath(id, "/reset"), { method: "POST", body: {} });
        drafts.items.delete(id); closeSheet(); await refreshWorkspace({ id, generation: selection.generation }); updateComposer(id); toast("对话上下文已清除。");
      });
    }, "danger");
    sheetContent.append(section("整理会话", button("复制为新会话", () => forkSheet(id), "secondary"), archive, hint("归档后仍可恢复，任务会继续执行。"), clear));
    function handleConflict(error) {
      if (error.status === 409) {
        sheetMessage("设置已在其他设备更新。请刷新后重新选择。", false);
        const refresh = button("刷新会话设置", () => workspaceSettings(id), "secondary"); sheetContent.append(refresh);
        for (const node of sheetContent.querySelectorAll("button")) if (node !== refresh && !node.classList.contains("close")) { node.disabled = true; node.dataset.conflict = "true"; }
      } else sheetMessage(error.message);
    }
    async function mutate(control, changes, close = false) {
      await withButton(control, async () => {
        try {
          const updated = await request(sessionPath(id), { method: "PATCH", body: { revision, ...changes } });
          revision = updated.revision; replaceSession(updated); toast("会话设置已保存。");
          if (close) closeSheet(); else if ("management" in changes) await workspaceSettings(id);
          renderPage();
        } catch (error) { handleConflict(error); }
      });
    }
  } catch (error) { if (loading.isConnected) { loading.remove(); sheetMessage(error.message); } report(error); }
}

async function forkSheet(id) {
  openSheet("复制为新会话");
  const loading = hint("正在读取可导入的材料…"); sheetContent.append(loading);
  const [messages, files] = await Promise.all([request(sessionPath(id, "/messages")), request(sessionPath(id, "/files"))]);
  if (!sheet.open || !loading.isConnected) return;
  loading.remove(); const name = input("", "新会话名称"), chosenMessages = [], chosenFiles = [];
  sheetContent.append(field("会话名称", name), hint("带上需要的片段，另起一段思路。"), disclosure("会复制哪些内容", hint("仅复制所选对话、文件和技能选择。任务、审批、连接器授权、Mac 权限和通话均不带入。")));
  const messageList = el("div", "fork-options");
  for (const job of messages.items.filter(item => item.kind !== "imported")) {
    const entry = checkbox((job.title || job.prompt || "任务结果").slice(0, 140), false, statusText(job)); chosenMessages.push({ id: job.id, control: entry.control }); messageList.append(entry.label);
  }
  if (!chosenMessages.length) messageList.append(hint("没有可选的对话。"));
  const fileList = el("div", "fork-options");
  for (const file of files.items) { const entry = checkbox(file.name, false, byteSize(file.size)); chosenFiles.push({ name: file.name, control: entry.control }); fileList.append(entry.label); }
  if (!chosenFiles.length) fileList.append(hint("没有可选的文件。"));
  sheetContent.append(section("选择对话", messageList), section("选择文件", fileList));
  const submit = button("创建独立会话", () => withButton(submit, async () => {
    if (!name.value.trim()) { name.focus(); return; }
    const session = await request(sessionPath(id, "/fork"), { method: "POST", body: {
      name: name.value.trim(), message_ids: chosenMessages.filter(item => item.control.checked).map(item => item.id), file_names: chosenFiles.filter(item => item.control.checked).map(item => item.name),
    } });
    replaceSession(session); closeSheet(); await selectSession(session.id);
  }), "primary"); sheetContent.append(submit);
}

async function downloadFile(id, name) { await download(sessionPath(id, `/files/${encodeURIComponent(name)}`), name); }
async function downloadArtifact(job) { await download(sessionPath(job.session_id, `/tasks/${encodeURIComponent(job.id)}/artifact`), job.artifact_filename || "result"); }
async function download(path, name) {
  const generation = pageGeneration;
  const blob = await request(path, { responseType: "blob" }); if (state.closed || generation !== pageGeneration) return;
  // Blob URLs do not inherit the download response's CSP or attachment header.
  // Files and task artifacts therefore always use an inert download MIME.
  const url = URL.createObjectURL(blob.slice(0, blob.size, "application/octet-stream")), link = el("a"); link.href = url; link.download = name; link.rel = "noopener";
  downloadURLs.add(url);
  document.body.append(link); link.click(); link.remove(); setTimeout(() => { URL.revokeObjectURL(url); downloadURLs.delete(url); }, 30000);
}
async function fileSheet(id) {
  openSheet(`文件 · ${sessionName(id)}`); const loading = hint("正在读取文件…"); sheetContent.append(loading);
  const data = await request(sessionPath(id, "/files")); if (!sheet.open || !loading.isConnected) return;
  loading.remove(); sheetContent.append(hint("留给这段对话的参考资料。单个文件最多 20 MB。"));
  const upload = el("input", "file-input"); upload.type = "file"; upload.setAttribute("aria-label", "选择上传到此会话的文件");
  const submit = button("上传文件", () => withButton(submit, async () => {
    const file = upload.files?.[0]; if (!file) { toast("请先选择文件。"); return; }
    if (file.size > 20 * 1024 * 1024 || file.size === 0) throw new Error("文件不能为空或超过 20 MB。");
    if (data.items.some(item => item.name === file.name) && !await confirmAction({ title: "替换同名文件？", detail: `「${sessionName(id)}」中的 ${file.name} 将被新文件替换。`, action: "替换文件", danger: true })) return;
    const form = new FormData(); form.append("file", file, file.name);
    await request(sessionPath(id, "/files"), { method: "POST", body: form }); toast(`已上传至「${sessionName(id)}」`);
    if (upload.isConnected) await fileSheet(id);
  }), "primary");
  sheetContent.append(upload, submit, section("已添加", ...data.items.map(file => {
    const text = el("div", "grow"); text.append(el("p", "file-name", file.name), hint(byteSize(file.size)));
    const item = row(text, button("下载", () => downloadFile(id, file.name), "small outline")); item.classList.add("item-row");
    const descriptor = mediaDescriptor(file, id, { file: true });
    if (!descriptor) return item;
    const card = el("div", "media-file"); card.append(mediaPreviews.get("files", descriptor), item); return card;
  })));
  if (!data.items.length) sheetContent.append(hint("还没有文件。"));
}

async function connectorSheet(id) {
  openSheet(`可访问资源 · ${sessionName(id)}`); const loading = hint("正在读取授权范围…"); sheetContent.append(loading);
  const [response, connections] = await Promise.all([request(sessionPath(id, "/connectors")), request("/v1/connectors")]);
  if (!sheet.open || !loading.isConnected) return;
  loading.remove(); sheetContent.append(hint("选择这段对话可以使用的账号与文件。"), disclosure("关于文件权限", hint("只有选定的文件可被访问。多个会话选择同一文档时，修改会写入同一份原件。")));
  let revision = response.revision; const controls = [];
  for (const connection of connections.items.filter(item => item.status === "connected")) {
    const existing = response.items.find(item => item.connection_id === connection.id);
    const enabled = checkbox(connection.display_name || connection.provider, !!existing, "允许此会话使用这个账号");
    const create = checkbox("允许创建新文件", existing?.can_create || false, "新建的文件会加入此会话的访问范围。");
    const scope = el("textarea", "resource-editor"); scope.rows = 3; scope.maxLength = 20000; scope.value = (existing?.resource_ids || []).join("\n"); scope.placeholder = "每行一个文件 ID；留空表示未授权现有文件";
    const group = el("div", "settings-card"); group.append(enabled.label, create.label, hint(`已授权 ${existing?.resource_ids?.length || 0} 个文件`));
    if (connection.provider === "google") group.append(button("从 Google 选择文件 ↗", async () => {
      await openRemote(async () => request(`/v1/connectors/${encodeURIComponent(connection.id)}/picker`, { method: "POST", body: { session_id: id, revision } }), "url");
      toast("选好文件后，返回这里刷新。");
    }, "outline small"));
    group.append(disclosure("手动管理文件范围", field("文件 ID", scope)));
    controls.push({ connection, enabled: enabled.control, create: create.control, scope }); sheetContent.append(group);
  }
  if (!controls.length) sheetContent.append(hint("还没有已连接的账号。请先在设置中连接 Google。"));
  const save = button("保存访问范围", () => withButton(save, async () => {
    const bindings = controls.filter(item => item.enabled.checked).map(item => ({ connection_id: item.connection.id, can_create: item.create.checked, resource_ids: [...new Set(item.scope.value.split(/\s+/).filter(Boolean))] }));
    try {
      const result = await request(sessionPath(id, "/connectors"), { method: "PUT", body: { revision, bindings } }); revision = result.revision;
      await refreshSessions(); toast("此会话的资源范围已保存。"); if (selection.id === id) renderPage();
    } catch (error) {
      if (error.status === 409) { sheetMessage("访问范围已更新。请刷新并核对后再保存。", false); save.disabled = true; save.dataset.conflict = "true"; }
      else throw error;
    }
  }), "primary");
  const refresh = button("刷新授权范围", () => connectorSheet(id), "outline"); sheetContent.append(save, refresh);
}
async function openRemote(loader, fieldName, google = false) {
  const popup = window.open("about:blank", "_blank"); if (popup) popup.opener = null;
  try {
    const result = await loader(), url = safeExternalURL(result[fieldName], { origin: location.origin, google });
    if (popup) popup.location.replace(url);
    else {
      const link = el("a", "secondary", "点击打开授权页面 ↗"); link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer";
      if (sheet.open) sheetContent.append(link); else main.append(link);
      toast("浏览器拦截了新窗口，请点击页面上的授权链接。");
    }
  } catch (error) { popup?.close(); throw error; }
}
async function hostStatus(id) {
  openSheet(`Mac 状态 · ${sessionName(id)}`); const loading = hint("正在诊断…"); sheetContent.append(loading);
  const result = await request(sessionPath(id, "/host/status")); if (!sheet.open || !loading.isConnected) return;
  loading.remove();
  const memory = el("div", "settings-card"); memory.append(el("h3", "", "内存"), el("p", "metric-value", `${byteSize(result.memory_available_bytes)} 可用`), hint(`共 ${byteSize(result.memory_total_bytes)}`));
  sheetContent.append(memory, section("维护记录", ...(result.requests || []).map(item => {
    const content = el("div", "item-row"); content.append(el("p", "grow", item.service === "harness" ? "助手服务" : item.service === "tunnel" ? "远程连接" : item.service), el("span", "badge", statusText({ status: item.state }))); return content;
  })), disclosure("诊断详情", el("pre", "text-block", JSON.stringify(result, null, 2))), hint("需要维护时，在会话中描述问题即可。"));
  if (!result.requests?.length) sheetContent.querySelector("section").append(hint("暂无维护记录。"));
}

async function renderSettings() {
  const generation = pageGeneration;
  main.setAttribute("aria-label", "设置");
  main.append(heading("设置", ""));
  const grid = el("div", "settings-grid"); main.append(grid);
  const account = el("section", "settings-card");
  account.append(el("p", "settings-section-label", "账号与登录"), el("h2", "", state.auth.account.display_name || "我的账号"), hint("通行密钥已验证"),
    disclosure("登录与隐私", hint("新增或撤销通行密钥需在 Mac 上操作。密钥可能经 iCloud 等服务同步，并非只绑定一台手机。"),
      hint("页面与对话不作离线缓存，刷新会清除草稿。关闭页面后，已提交的任务仍会继续。"), hint("长期连接器凭据保留在 Mac。网页脚本无法读取登录 Cookie。")), button("退出登录", logout, "outline"));
  account.classList.add("full"); grid.append(account);
  const connectors = el("section", "settings-card full"); connectors.append(el("h2", "", "连接的账号"), hint("文件范围在各个会话中单独选择。"),
    button("连接 Google Drive / Docs", () => openRemote(() => request("/v1/connectors/google/authorize", { method: "POST", body: {} }), "authorization_url", true), "secondary"));
  const connectionsList = el("div"); connectors.append(connectionsList); grid.append(connectors);
  const devices = el("section", "settings-card full"); devices.append(el("h2", "", "登录设备"), hint("可随时撤销某次登录的访问权限。")); const devicesList = el("div"); devices.append(devicesList); grid.append(devices);
  const library = el("section", "settings-card full"); library.append(el("h2", "", "技能库"), hint("在会话设置中选用。")); const skillsList = el("div"); library.append(skillsList); grid.append(library);
  try {
    const [connectionData, deviceData, skillData] = await Promise.all([request("/v1/connectors"), request("/v1/devices"), request("/v1/skills")]);
    if (state.closed || generation !== pageGeneration) return;
    for (const connection of connectionData.items) {
      const content = el("div", "grow"); content.append(el("p", "", connection.display_name || connection.provider), hint(connection.status === "connected" ? "已连接" : "已断开"));
      const item = el("div", "item-row"); item.append(content);
      if (connection.status === "connected") item.append(button("断开", async () => {
        if (!await confirmAction({ title: "断开这个账号？", detail: `${connection.display_name || connection.provider} 将不再供任何会话使用。之后可以重新连接。`, action: "断开账号", danger: true })) return;
        await request(`/v1/connectors/${encodeURIComponent(connection.id)}`, { method: "DELETE" }); await refreshSessions(); renderPage();
      }, "danger small")); connectionsList.append(item);
    }
    if (!connectionData.items.length) connectionsList.append(hint("尚未连接账号。"));
    for (const device of deviceData.items) {
      const content = el("div", "grow"), isCurrent = device.id === state.auth.device_id;
      content.append(el("p", "", `${device.device_name}${isCurrent ? " · 当前登录" : ""}`), hint(device.revoked ? "已撤销" : `授权于 ${timeText(device.created)}`));
      const item = el("div", "item-row"); item.append(content);
      if (!device.revoked) item.append(button("撤销", async () => {
        if (!await confirmAction({ title: "撤销这次登录？", detail: `「${device.device_name}」将失去访问权限。${isCurrent ? "当前页面会退出登录。" : "需要重新验证通行密钥才能访问。"}`, action: "撤销登录", danger: true })) return;
        await request(`/v1/devices/${encodeURIComponent(device.id)}`, { method: "DELETE" }); if (isCurrent) expire(); else renderPage();
      }, "danger small")); devicesList.append(item);
    }
    for (const skill of skillData.items) {
      const item = el("div", "item-row"), content = el("div", "grow"); content.append(el("p", "", skillLabel(skill.name)), hint(skill.description || ""));
      item.append(content); skillsList.append(item);
    }
    if (!skillData.items.length) skillsList.append(hint("技能库为空。"));
  } catch (error) { report(error); }
  await renderAgentSettings(grid, generation);
}

// Memory and schedules exist only with the new agent; a 404 means it is off.
async function renderAgentSettings(grid, generation) {
  let memoryData, scheduleData;
  try {
    [memoryData, scheduleData] = await Promise.all([request("/v1/memory"), request("/v1/schedules")]);
  } catch (error) { if (!(error instanceof ApiError && error.status === 404)) report(error); return; }
  if (state.closed || generation !== pageGeneration) return;
  const memory = el("section", "settings-card full");
  memory.append(el("h2", "", "记忆"), hint("Friday 在所有会话里都会参考这些内容。你说“记住……”时它会自动添加，也可以在这里删改。"));
  for (const [target, title] of [["user", "关于我"], ["memory", "长期记忆"]]) {
    const list = el("div");
    memory.append(el("p", "settings-section-label", `${title}（上限 ${memoryData.limits[target]} 字）`), list);
    for (const entry of memoryData[target]) {
      const item = el("div", "item-row"), content = el("div", "grow"); content.append(el("p", "", entry));
      item.append(content, button("删除", async () => {
        if (!await confirmAction({ title: "删除这条记忆？", detail: entry, action: "删除", danger: true })) return;
        await request("/v1/memory", { method: "POST", body: { action: "remove", target, old_text: entry } }); renderPage();
      }, "danger small"));
      list.append(item);
    }
    if (!memoryData[target].length) list.append(hint("还没有内容。"));
    const input = el("input"); input.type = "text"; input.maxLength = 300; input.placeholder = target === "user" ? "例如：叫我阿杰，时区是伦敦" : "例如：视频默认竖屏，10 秒";
    input.setAttribute("aria-label", `添加到${title}`);
    const add = button("添加", async () => {
      const text = input.value.trim(); if (!text) return;
      add.disabled = true;
      try { await request("/v1/memory", { method: "POST", body: { action: "add", target, text } }); renderPage(); }
      catch (error) { add.disabled = false; report(error); }
    }, "secondary small");
    const row = el("div", "item-row"); row.append(input, add); memory.append(row);
  }
  grid.append(memory);
  const schedules = el("section", "settings-card full");
  schedules.append(el("h2", "", "定时任务"), hint("在聊天里说“每天早上 8 点……”即可创建。到点后会在原会话里自动执行。"));
  for (const item of scheduleData.items) {
    const row = el("div", "item-row"), content = el("div", "grow");
    content.append(el("p", "", `${item.title} · ${item.when}`),
      hint(item.enabled ? `下次：${item.next_run || "—"}${item.notify === "when_relevant" ? " · 仅在有情况时通知" : ""}` : "已暂停"));
    row.append(content,
      button(item.enabled ? "暂停" : "恢复", async () => {
        await request(`/v1/schedules/${encodeURIComponent(item.id)}`, { method: "PATCH", body: { enabled: !item.enabled } }); renderPage();
      }, "secondary small"),
      button("删除", async () => {
        if (!await confirmAction({ title: "删除这个定时任务？", detail: `${item.title}（${item.when}）`, action: "删除", danger: true })) return;
        await request(`/v1/schedules/${encodeURIComponent(item.id)}`, { method: "DELETE" }); renderPage();
      }, "danger small"));
    schedules.append(row);
  }
  if (!scheduleData.items.length) schedules.append(hint("还没有定时任务。"));
  grid.append(schedules);
}

function updateVoice(snapshot) {
  const previousError = voiceState.error;
  voiceState = snapshot || {};
  if (state.closed) return;
  if (voiceState.error && voiceState.error !== previousError) report(new Error(voiceState.error));
  renderVoiceBanner();
}
function renderVoiceBanner() {
  if (state.closed) return;
  const stamp = JSON.stringify([voiceState.sessionID, voiceState.sessionName, voiceState.state, voiceState.transcript, memoState.sessionID, memoState.sessionName, memoState.state, memoState.draftReady]);
  if (banner.dataset.stamp === stamp) return;
  banner.dataset.stamp = stamp;
  banner.replaceChildren(); banner.hidden = !voiceState.sessionID && !memoState.sessionID;
  if (voiceState.sessionID) {
    const description = el("div", "grow"); description.append(el("p", "voice-indicator", `通话 · ${voiceState.sessionName || sessionName(voiceState.sessionID)}`), hint(voiceState.state || "连接中"));
    const actions = el("div", "voice-actions"); actions.append(button("停止播报", () => voice.interrupt(), "small"), button("结束通话", () => voice.stop(), "small"));
    banner.append(description, actions);
    if (voiceState.transcript) banner.append(el("p", "voice-transcript", voiceState.transcript));
  }
  if (memoState.sessionID) {
    const description = el("div", "grow"); description.append(el("p", "voice-indicator", `语音消息 · ${memoState.sessionName || sessionName(memoState.sessionID)}`), hint(memoState.state === "recording" ? "正在录音" : memoState.state === "uploading" ? "正在发送" : memoState.draftReady ? "录音尚未发送" : "正在准备"));
    const actions = el("div", "voice-actions");
    if (memoState.state === "recording") actions.append(button("结束录音", () => recorder.stop(), "small"));
    actions.append(button("查看录音", () => voiceSheet(memoState.sessionID), "small")); banner.append(description, actions);
  }
}
function voiceEvent(event) {
  if (state.closed) return;
  if (event.type === "error") report(new Error(event.message || "语音连接出现问题。"));
  const id = event.session_id || voiceState.sessionID;
  if (id && ["task_submitted", "task_queued", "task_updated", "task_result", "task", "result", "transcript", "recognized"].includes(event.type)) refreshWorkspace({ id, generation: selection.generation }).catch(report);
}
function updateMemo(snapshot) {
  const previousError = memoState.error; memoState = snapshot || {};
  if (state.closed) return;
  if (memoState.error && memoState.error !== previousError) report(new Error(memoState.error));
  if (recordingView?.isConnected) renderRecording(recordingView); renderVoiceBanner();
}
function voiceSheet(id) {
  const session = state.sessions.find(item => item.id === id); if (!session) return;
  openSheet(`语音 · ${session.name}`);
  const other = voiceState.sessionID && voiceState.sessionID !== id;
  const start = button(voiceState.sessionID === id ? "返回通话" : other ? "切换到这里通话" : "开始通话", async () => {
    if (voiceState.sessionID === id) { closeSheet(); return; }
    if (other && !await confirmAction({ title: "切换通话？", detail: `结束「${voiceState.sessionName}」的通话，转到「${session.name}」。已提交的任务会继续执行。`, action: "结束并切换" })) return;
    closeSheet(); await (other ? voice.switchSession(session) : voice.start(session));
  }, "primary");
  sheetContent.append(start, hint("锁屏或切到后台可能中断通话。"), disclosure("通话与会话", hint("通话始终留在开启它的会话中。浏览其他会话不会转移通话，结束通话也不会取消任务。")));
  const memo = section("语音消息", hint("说完可以试听，再决定发送。"));
  const view = el("div", "stack"); view.dataset.sessionId = id; recordingView = view; memo.append(view); sheetContent.append(memo); renderRecording(view);
}
function renderRecording(view) {
  if (!view.isConnected || state.closed) return;
  const id = view.dataset.sessionId, session = state.sessions.find(item => item.id === id);
  if (!session) return;
  const active = memoState.sessionID, target = active ? memoState.sessionName || sessionName(active) : session.name;
  const elapsed = Math.floor(memoState.elapsed || 0), timer = `${String(Math.floor(elapsed / 60)).padStart(2, "0")}:${String(elapsed % 60).padStart(2, "0")}`;
  const stamp = JSON.stringify([id, active, target, memoState.state, memoState.previewURL, memoState.error, memoState.supported, memoState.draftReady]);
  if (view.dataset.stamp === stamp) { const clock = view.querySelector(".record-timer"); if (clock) clock.textContent = timer; return; }
  view.dataset.stamp = stamp; view.replaceChildren();
  const labels = { idle: "准备好时，按下录音。", requestingPermission: "请允许使用麦克风…", recording: "正在录音", stopping: "正在整理录音…", ready: "试听与发送", uploading: "正在发送…" };
  const recording = el("div", "record-state"); recording.append(el("p", "", labels[memoState.state] || labels.idle));
  if (active) recording.append(hint(target));
  if (memoState.state === "recording") recording.append(el("span", "record-timer", timer)); view.append(recording);
  if (memoState.error) view.append(el("p", "error-text", memoState.error));
  if (memoState.previewURL) {
    const audio = el("audio"); audio.controls = true; audio.src = memoState.previewURL; audio.preload = "metadata"; view.append(audio);
  }
  const actions = el("div", "row wrap");
  if (memoState.state === "recording") actions.append(button("结束录音", () => recorder.stop(), "primary"));
  else if (memoState.state === "ready" && memoState.draftReady) {
    actions.append(button("发送语音", async () => { const destination = memoState.sessionID; const job = await recorder.send(); if (!job) return; if (destination) await refreshWorkspace({ id: destination, generation: selection.generation }); toast(`已发送至「${target}」`); }, "primary"), button("丢弃", () => recorder.discard(), "outline"));
  } else if (!["requestingPermission", "stopping", "uploading"].includes(memoState.state)) {
    const start = button("开始录音", () => recorder.start(session), "secondary"); start.disabled = memoState.supported === false; actions.append(start);
    if (memoState.supported === false) view.append(hint("当前浏览器不支持所需录音格式，请使用实时通话或文字。"));
  }
  if (["requestingPermission", "stopping", "uploading"].includes(memoState.state)) actions.append(button("停止并丢弃", async () => {
    if (memoState.state === "uploading" && !await confirmAction({ title: "停止发送并丢弃录音？", detail: `语音可能已经送达。请回到「${target}」核对是否已创建任务。`, action: "停止并丢弃", danger: true })) return;
    recorder.discard();
  }, "outline"));
  view.append(actions);
}

async function poll() {
  if (state.closed || state.polling || document.hidden) return;
  state.polling = true;
  try {
    const result = await request(`/v1/events?after=${state.cursor}`); state.cursor = result.cursor; updateConnection(true);
    if (result.items.length) {
      const ids = new Set(result.items.map(event => event.session_id));
      if (result.items.some(event => ["session_created", "session_updated", "connectors_updated"].includes(event.kind))) {
        await refreshSessions(); if (state.tab === "sessions") renderPage();
      }
      if (selection.id && ids.has(selection.id)) await refreshWorkspace();
      if (state.tab === "tasks") renderPage();
    }
    if (state.tab === "dramas" && (result.items.length || dramas.active()) && await dramas.refresh()) renderPage();
    // Status transitions and cross-channel completions may not emit a UI event.
    if (state.tab === "sessions" && selection.id && (state.jobs.get(selection.id) || []).some(canCancel)) await refreshWorkspace();
  } catch (error) { if (!state.closed) updateConnection(false); }
  finally { state.polling = false; }
}
async function logout() {
  if (!await confirmAction({ title: "退出登录？", detail: "未发送的草稿和录音会清除。已提交的任务会继续执行。", action: "退出登录" })) return;
  await voice.stop(); recorder.discard();
  try { await request("/v1/web/auth/logout", { method: "POST", body: {} }); expire(); }
  catch (error) { report(error); toast("退出未得到服务端确认，请恢复网络后重试。", true); }
}
function clearPrivateUI() {
  pendingConfirmation?.(false);
  mediaPreviews.clear(); for (const url of downloadURLs) URL.revokeObjectURL(url); downloadURLs.clear();
  drafts.clear(); state.jobs.clear(); state.approvals.clear(); state.sessions = []; state.auth = null;
  main.replaceChildren(); banner.replaceChildren(); banner.hidden = true; closeSheet();
  document.querySelector("#toast").hidden = true;
}
function expire() {
  if (state.closed) return;
  state.closed = true; clearInterval(pollTimer); clearTimeout(toastTimer);
  voice.stop().catch(() => {}); recorder.discard(); api.close(); clearPrivateUI(); location.replace("/login");
}
async function boot() {
  try {
    state.auth = await request("/v1/web/auth/session"); api.csrf = state.auth.csrf_token;
    if (!api.csrf || !state.auth.account?.id) throw new Error("登录状态无效。");
    await refreshSessions(); document.querySelector("#app").setAttribute("aria-busy", "false"); updateConnection(true); renderPage();
    pollTimer = setInterval(poll, 4000); await poll();
  } catch (error) {
    if (state.closed) return;
    main.replaceChildren(el("p", "empty", "暂时无法连接服务。请检查网络后重试。"), button("重新连接", () => location.reload(), "secondary wide")); report(error);
  }
}
for (const tab of document.querySelectorAll("[data-tab]")) tab.addEventListener("click", () => {
  if (!state.auth) return;
  state.tab = tab.dataset.tab; if (state.tab === "sessions") selection.select(null); renderPage();
});
document.querySelector("#new-session").addEventListener("click", () => { if (state.auth) createSession().catch(report); });
window.addEventListener("online", () => poll());
window.addEventListener("offline", () => updateConnection(false));
document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
window.addEventListener("pagehide", () => {
  stopViewportTracking();
  // A bfcache snapshot must never restore authenticated content without revalidation.
  clearInterval(pollTimer); state.closed = true; voice.stop().catch(() => {}); recorder.discard(); api.close(); clearPrivateUI();
});
window.addEventListener("pageshow", event => { if (event.persisted) location.reload(); });
if ("serviceWorker" in navigator && isSecureContext) navigator.serviceWorker.register("/pwa/sw.js", { scope: "/" }).catch(() => {});
boot();
