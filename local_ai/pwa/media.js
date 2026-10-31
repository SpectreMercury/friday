// Media URLs are supplied by the server after checking the actual file format.
// Only an exact, authenticated URL in this session may become an element source.
const MEDIA_TYPES = new Map([
  ["image/jpeg", "image"], ["image/png", "image"], ["image/webp", "image"], ["image/gif", "image"],
  ["video/mp4", "video"], ["audio/mp4", "audio"], ["audio/wav", "audio"], ["audio/ogg", "audio"],
]);
const pathPart = value => encodeURIComponent(value).replace(/[!'()*]/g, character => `%${character.charCodeAt(0).toString(16).toUpperCase()}`);

export function mediaDescriptor(item, sessionID, { file = false } = {}) {
  if (!sessionID || !item || (!file && item.session_id !== sessionID)) return null;
  const name = file ? item.name : item.artifact_filename;
  const mime = file ? item.media_type : item.artifact_media_type;
  const url = file ? item.preview_url : item.artifact_preview_url;
  const kind = MEDIA_TYPES.get(mime);
  if (!kind || typeof name !== "string" || !name || (!file && !item.id)) return null;
  const base = `/v1/sessions/${pathPart(sessionID)}`;
  const expected = file ? `${base}/files/${pathPart(name)}?preview=1` : `${base}/tasks/${pathPart(item.id)}/artifact?preview=1`;
  if (url !== expected) return null;
  const version = !file && typeof item.artifact_version === "string" ? item.artifact_version : "";
  return { url, mime, kind, name, sessionID, ...(version ? { version } : {}) };
}

// Child tool prompts are implementation details. Their artifacts belong beside
// the original user turn, including nested children and truncated old parents.
export function conversationEntries(items, sessionID) {
  const rows = (items || []).filter(item => item.session_id === sessionID);
  const byID = new Map(rows.map(item => [item.id, item]));
  const entries = new Map(rows.filter(item => !item.parent_job_id).map(job => [job.id, { key: job.id, job, artifacts: [] }]));
  for (const item of rows) {
    if (!item.artifact_url) continue;
    let root = item;
    const seen = new Set();
    while (root.parent_job_id && byID.has(root.parent_job_id) && !seen.has(root.id)) {
      seen.add(root.id); root = byID.get(root.parent_job_id);
    }
    const key = root.parent_job_id ? `orphan:${root.parent_job_id}` : root.id;
    if (!entries.has(key)) entries.set(key, { key, job: null, artifacts: [] });
    entries.get(key).artifacts.push(item);
  }
  return [...entries.values()];
}

export function createMediaPreview(descriptor, { document = globalThis.document, active = () => true, onReady = () => {} } = {}) {
  const create = (tag, className, text) => {
    const node = document.createElement(tag); node.className = className;
    if (text) node.textContent = text;
    return node;
  };
  const node = create("figure", `media-preview media-${descriptor.kind}`);
  node.dataset.sessionId = descriptor.sessionID;
  const stage = create("div", "media-stage"), overlay = create("div", "media-state");
  const spinner = create("span", "media-spinner"); spinner.setAttribute("aria-hidden", "true");
  const status = create("span", "media-status"); status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
  const retry = create("button", "small quiet", "重新加载"); retry.type = "button";
  overlay.append(spinner, status, retry); node.append(stage); stage.append(overlay);
  let disposed = false, media = null, removeListeners = () => {};
  const current = () => !disposed && active() && node.isConnected;
  const release = () => {
    removeListeners();
    if (!media) return;
    if (descriptor.kind !== "image") media.pause();
    media.removeAttribute("src");
    if (descriptor.kind !== "image") media.load();
    media.remove(); media = null;
  };
  const load = () => {
    if (disposed || !active()) return;
    release(); stage.dataset.state = "loading"; overlay.hidden = false; retry.hidden = true; spinner.hidden = false;
    status.textContent = descriptor.kind === "image" ? "正在载入图片…" : descriptor.kind === "video" ? "正在载入视频…" : "正在载入音频…";
    media = create(descriptor.kind === "image" ? "img" : descriptor.kind, "media-content");
    const target = media;
    media.setAttribute("aria-label", descriptor.name);
    if (descriptor.kind === "image") {
      media.alt = descriptor.name; media.loading = "lazy"; media.decoding = "async";
      media.referrerPolicy = "no-referrer";
    } else {
      media.controls = true; media.preload = "metadata";
      if (descriptor.kind === "video") { media.playsInline = true; media.setAttribute("playsinline", ""); }
    }
    const ready = () => {
      if (!current() || media !== target) return;
      stage.dataset.state = "ready"; overlay.hidden = true; onReady();
    };
    const failed = () => {
      if (!current() || media !== target) return;
      stage.dataset.state = "error"; overlay.hidden = false; spinner.hidden = true; retry.hidden = false;
      status.textContent = "暂时无法预览，可重试或下载原文件。";
    };
    const event = descriptor.kind === "image" ? "load" : "loadedmetadata";
    media.addEventListener(event, ready); media.addEventListener("error", failed);
    removeListeners = () => { target.removeEventListener(event, ready); target.removeEventListener("error", failed); };
    // Native requests carry same-origin cookies and allow the browser to use
    // Range requests. Large videos are never fetched into a JavaScript Blob.
    media.src = descriptor.version ? `${descriptor.url}&version=${pathPart(descriptor.version)}` : descriptor.url; stage.append(media);
  };
  retry.addEventListener("click", () => { if (current()) load(); });
  load();
  return { node, dispose() { if (disposed) return; disposed = true; release(); } };
}

export class MediaPreviews {
  constructor(options = {}) { this.options = options; this.items = new Map(); }
  get(scope, descriptor) {
    const key = `${scope}:${descriptor.url}:${descriptor.mime}`;
    const previous = this.items.get(key);
    // Polling should preserve playback until this task publishes a new file.
    // Dispose the old element before replacing it, including its late events.
    if (previous && previous.version !== descriptor.version) { previous.dispose(); this.items.delete(key); }
    if (!this.items.has(key)) this.items.set(key, { ...createMediaPreview(descriptor, this.options), version: descriptor.version });
    return this.items.get(key).node;
  }
  releaseWithin(root) {
    for (const [key, view] of this.items) if (root.contains(view.node)) { view.dispose(); this.items.delete(key); }
  }
  prune() {
    for (const [key, view] of this.items) if (!view.node.isConnected) { view.dispose(); this.items.delete(key); }
  }
  clear() { for (const view of this.items.values()) view.dispose(); this.items.clear(); }
}
