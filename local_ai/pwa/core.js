// Browser state is intentionally memory-only. Credentials are HttpOnly cookies.
export class ApiError extends Error {
  constructor(message, status = 0, code = "") {
    super(message); this.name = "ApiError"; this.status = status; this.code = code;
  }
}

export function apiPath(value) {
  if (typeof value !== "string" || !value.startsWith("/v1/") || /[\\#\r\n]/.test(value)) {
    throw new Error("无法打开此内容，请刷新页面后重试。");
  }
  const normalized = new URL(value, "https://harness.invalid");
  if (normalized.origin !== "https://harness.invalid" || !normalized.pathname.startsWith("/v1/")) throw new Error("无法打开此内容，请刷新页面后重试。");
  return value;
}

export function sessionPath(id, suffix = "") {
  if (!id) throw new Error("请先选择会话。");
  return `/v1/sessions/${encodeURIComponent(id)}${suffix}`;
}

export class API {
  constructor({ fetcher = globalThis.fetch.bind(globalThis), onUnauthorized = () => {} } = {}) {
    this.fetcher = fetcher; this.onUnauthorized = onUnauthorized;
    this.csrf = ""; this.closed = false; this.controllers = new Set();
  }
  async request(path, { method = "GET", body, headers = {}, signal, responseType = "json" } = {}) {
    if (this.closed) throw new ApiError("登录已结束，请重新登录。", 401);
    apiPath(path);
    const controller = new AbortController();
    const requestHeaders = new Headers(headers);
    method = method.toUpperCase();
    requestHeaders.set("Accept", responseType === "blob" ? "*/*" : "application/json");
    if (!["GET", "HEAD"].includes(method)) {
      if (!this.csrf) throw new ApiError("登录验证未完成，请重新登录。", 401);
      requestHeaders.set("X-CSRF-Token", this.csrf);
    }
    if (body !== undefined && !(body instanceof Blob) && !(body instanceof FormData) && !(body instanceof ArrayBuffer)) {
      body = JSON.stringify(body); requestHeaders.set("Content-Type", "application/json");
    }
    const cancel = () => controller.abort();
    if (signal?.aborted) controller.abort();
    signal?.addEventListener("abort", cancel, { once: true });
    this.controllers.add(controller);
    try {
      const response = await this.fetcher(path, {
        method, headers: requestHeaders, body, signal: controller.signal,
        credentials: "same-origin", cache: "no-store", redirect: "error", referrerPolicy: "no-referrer",
      });
      if (this.closed) throw new ApiError("登录已结束，请重新登录。", 401);
      if (response.status === 401) {
        this.close(); this.onUnauthorized(); throw new ApiError("登录已失效，请重新登录。", 401);
      }
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new ApiError(data.error?.message || `操作未完成，请稍后重试（${response.status}）。`, response.status, data.error?.code);
      }
      return responseType === "blob" ? response.blob() : response.json();
    } finally {
      this.controllers.delete(controller); signal?.removeEventListener("abort", cancel);
    }
  }
  close() {
    this.closed = true; this.csrf = "";
    for (const controller of this.controllers) controller.abort();
    this.controllers.clear();
  }
}

// A send always captures its original workspace, text and idempotency key.
export class Drafts {
  constructor(uuid = () => crypto.randomUUID()) { this.items = new Map(); this.uuid = uuid; }
  get(id) {
    if (!this.items.has(id)) this.items.set(id, { text: "", sending: false, request: null, uncertain: false });
    return this.items.get(id);
  }
  set(id, text) { const draft = this.get(id); if (!draft.sending && !draft.uncertain) draft.text = text; }
  begin(id) {
    const draft = this.get(id);
    if (draft.sending || !draft.text.trim()) return null;
    if (!draft.request) draft.request = { sessionID: id, text: draft.text.trim(), request_id: this.uuid() };
    draft.sending = true;
    return { ...draft.request };
  }
  complete(id, requestID) {
    const draft = this.get(id);
    if (draft.request?.request_id !== requestID) return;
    draft.text = ""; draft.sending = false; draft.request = null; draft.uncertain = false;
  }
  failed(id, requestID, uncertain) {
    const draft = this.get(id);
    if (draft.request?.request_id !== requestID) return;
    draft.sending = false; draft.uncertain = uncertain;
    if (!uncertain) draft.request = null;
  }
  clear() { this.items.clear(); }
}

// Out-of-order responses never replace the currently selected workspace.
export class Selection {
  constructor() { this.id = null; this.generation = 0; }
  select(id) { this.id = id; return { id, generation: ++this.generation }; }
  current(ticket) { return ticket.id === this.id && ticket.generation === this.generation; }
  ticket() { return { id: this.id, generation: this.generation }; }
}

export function statusText(job) {
  if (job.status_text) return job.status_text;  // the server owns status wording for every client
  const outcomes = { needs_input: "等待补充", blocked: "暂时受阻", partial: "部分完成", continued: "已并入上一条", paused: "已暂停", silent: "无需提醒" };
  const statuses = { queued: "排队中", pending: "排队中", running: "正在处理", waiting_approval: "等待批准", waiting: "等待补充", succeeded: "已完成", completed: "已完成", done: "已完成", failed: "未完成", cancelled: "已取消", canceled: "已取消" };
  return outcomes[job.outcome] || statuses[job.status] || job.status || "等待更新";
}
export function canCancel(job) { return ["queued", "pending", "running", "waiting", "waiting_approval"].includes(job.status); }
export function resultText(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "object" && (typeof value.text === "string" || typeof value.message === "string")) return value.text || value.message;
  return JSON.stringify(value, null, 2);
}
export function byteSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes >= 1073741824) return `${(bytes / 1073741824).toFixed(1)} GB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}

export function safeExternalURL(value, { origin, google = false } = {}) {
  const url = new URL(value, origin);
  if (url.protocol !== "https:" || url.username || url.password) throw new Error("此连接无法安全打开，请返回后重试。");
  if (google ? url.hostname !== "accounts.google.com" : url.origin !== origin) throw new Error("连接地址不匹配，请刷新页面后重试。");
  return url.href;
}
// Installed WebKit may exclude safe areas from visualViewport.height. Leave
// the resting shell anchored to all four fixed edges; only a keyboard needs a
// visual-viewport override. Never substitute the physical screen dimensions.
export function appViewport({ height, visualHeight, visualTop = 0, scale = 1, standalone = false }) {
  if (Math.abs(scale - 1) > .01) return null; // preserve pinch zoom
  const visible = Number.isFinite(visualHeight) && visualHeight > 0;
  const appHeight = visible ? visualHeight : height;
  if (!Number.isFinite(appHeight) || appHeight <= 0) return null;
  const keyboard = visible && Number.isFinite(height) && height - visualHeight > 120;
  // null releases the height override, allowing fixed top + bottom to size it.
  if (standalone && !keyboard) return { height: null, top: 0, keyboard: false };
  const top = visible && Number.isFinite(visualTop) ? Math.max(0, visualTop) : 0;
  return { height: appHeight, top, keyboard };
}

export function trackAppViewport(win = window, doc = document) {
  const standalone = win.matchMedia("(display-mode: standalone)");
  let frame = null;
  const update = () => {
    frame = null;
    const viewport = win.visualViewport;
    // Leave pinch zoom to the browser rather than resizing content under it.
    if (viewport && Math.abs(viewport.scale - 1) > .01) return;
    const geometry = appViewport({ height: win.innerHeight,
      standalone: standalone.matches || win.navigator.standalone === true,
      visualHeight: viewport?.height, visualTop: viewport?.offsetTop, scale: viewport?.scale ?? 1 });
    if (!geometry) return;
    if (geometry.height === null) {
      doc.documentElement.style.removeProperty("--app-height");
      doc.documentElement.style.removeProperty("--app-bottom");
    } else {
      doc.documentElement.style.setProperty("--app-height", `${geometry.height}px`);
      doc.documentElement.style.setProperty("--app-bottom", "auto");
    }
    doc.documentElement.style.setProperty("--app-top", `${geometry.top}px`);
    doc.documentElement.classList.toggle("keyboard-open", geometry.keyboard);
  };
  const schedule = () => { if (frame === null) frame = win.requestAnimationFrame(update); };
  const listeners = [[win, "resize"], [win, "orientationchange"], [win, "pageshow"],
    [doc, "focusin"], [doc, "focusout"], [doc, "visibilitychange"], [standalone, "change"]];
  if (win.visualViewport) listeners.push([win.visualViewport, "resize"], [win.visualViewport, "scroll"]);
  for (const [target, event] of listeners) target.addEventListener(event, schedule);
  update();
  return () => {
    for (const [target, event] of listeners) target.removeEventListener(event, schedule);
    if (frame !== null) win.cancelAnimationFrame(frame);
  };
}
