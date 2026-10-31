// Short-drama projects: list, import, settings, storyboard, film. All text from the
// server is rendered as DOM text; video sources are fixed same-origin API paths.
const STATUS = { planned: "已规划", drafting: "草稿渲染中", draft_review: "待审草稿", approved: "草稿通过", finalizing: "正式渲染中", done: "完成" };
const STAGE = { setup: "设定中", storyboard: "分镜中", producing: "制作中", ready: "可导出" };
const OPS = { analyze: "补全设定", storyboard: "生成分镜", export: "合成" };
const JOB = { queued: "排队中", running: "进行中", succeeded: "已完成", failed: "失败", cancelled: "已取消" };
const enc = encodeURIComponent;

export function createDramas(ui) {
  const { el, button, hint, heading, field, request, report, toast, confirmAction, openSheet, closeSheet, sheetContent, withButton, emptyState, timeText } = ui;
  const view = { id: null, tab: "board", data: null };

  const minutes = seconds => seconds < 90 ? `${Math.max(1, Math.round(seconds))} 秒` : `${Math.round(seconds / 60)} 分钟`;
  const textarea = (value = "", rows = 3, max = 3000) => { const node = el("textarea"); node.value = value || ""; node.rows = rows; node.maxLength = max; return node; };
  const chip = (status, error) => { const node = el("span", `drama-chip ${status}${error ? " failed" : ""}`, error ? "失败" : STATUS[status] || status); return node; };
  const characterName = id => view.data?.characters.find(c => c.id === id)?.name || "?";

  async function load() {
    view.data = await request(`/v1/dramas/${enc(view.id)}`);
    return view.data;
  }
  async function mutate(path, method, body) {
    try { view.data = await request(path, { method, body: { revision: view.data.project.revision, ...body } }); }
    catch (error) {
      if (error.status === 409) { toast("项目已在别处修改，已刷新为最新内容。", true); await load(); }
      else throw error;
    }
    ui.renderPage();
  }

  // -- list ----------------------------------------------------------------
  async function renderList(main, generation) {
    main.setAttribute("aria-label", "短剧");
    main.append(heading("短剧", "导入剧本 → 设定 → 分镜 → 草稿 → 正式 → 成片", button("导入剧本", importSheet, "primary small")));
    const list = el("div", "drama-list"); main.append(list);
    const data = await request("/v1/dramas");
    if (generation !== ui.generation()) return;
    for (const item of data.items) {
      const card = button("", () => open(item.id), "drama-card");
      const progress = el("div", "drama-progress"), bar = el("span"); bar.style.width = `${item.shots ? Math.round(item.done / item.shots * 100) : 0}%`; progress.append(bar);
      card.append(el("p", "drama-card-title", item.title),
        hint(`${STAGE[item.stage] || item.stage} · ${item.shots} 镜${item.seconds ? ` · 约 ${minutes(item.seconds)}` : ""} · ${timeText(item.updated)}`), progress);
      list.append(card);
    }
    if (!data.items.length) list.append(emptyState("还没有短剧项目", "导入一个剧本开始。也可以在聊天里把剧本发给 Friday，让它帮你导入。"));
  }

  function importSheet() {
    openSheet("导入剧本");
    const title = el("input"); title.maxLength = 100; title.placeholder = "例如：雨夜咖啡馆 · 第 1 集";
    const script = textarea("", 12, 30000); script.placeholder = "粘贴剧本正文。支持「第1场 内景·咖啡馆·夜」这类场次标题和「角色：台词」格式；没有场次标题的散文也可以。";
    const file = el("input"); file.type = "file"; file.accept = ".txt,.md,.fountain,text/plain,text/markdown";
    file.addEventListener("change", async () => {
      const chosen = file.files?.[0]; if (!chosen) return;
      if (chosen.size > 200_000) { report(new Error("文件太大；单个项目最多 30000 字，请按集拆开。")); return; }
      script.value = await chosen.text(); if (!title.value) title.value = chosen.name.replace(/\.[^.]+$/, "").slice(0, 100);
    });
    const hd = el("select"); hd.append(new Option("标准 352×608（推荐）", "sd"), new Option("高清 480×864（更慢）", "hd"));
    const counter = hint(""); const count = () => { counter.textContent = `${script.value.length} / 30000 字`; }; script.addEventListener("input", count); count();
    const submit = button("导入", () => withButton(submit, async () => {
      if (!title.value.trim()) { title.focus(); return; }
      if (!script.value.trim()) { script.focus(); return; }
      const created = await request("/v1/dramas", { method: "POST", body: { title: title.value.trim(), script: script.value, resolution: hd.value } });
      closeSheet(); view.tab = "bible"; await open(created.project.id);
      toast(`已导入 ${created.scenes.length} 个场次、${created.characters.length} 个角色。`);
    }), "primary");
    sheetContent.append(field("标题", title), field("剧本", script), counter, field("或选择文本文件（.txt / .md / .fountain）", file), field("分辨率", hd), submit,
      hint("Word 文档请在聊天里发给 Friday 导入。角色都按虚构人物处理。"));
    title.focus();
  }

  async function open(id) {
    view.id = id; if (!["script", "bible", "board", "film"].includes(view.tab)) view.tab = "board";
    await load(); ui.renderPage();
  }

  // -- project -------------------------------------------------------------
  function renderProject(main) {
    const data = view.data, project = data.project;
    main.setAttribute("aria-label", project.title);
    const back = button("‹ 全部短剧", () => { view.id = null; view.data = null; ui.renderPage(); }, "quiet small");
    main.append(back, heading(project.title, `${STAGE[data.stage]} · ${data.shots.length} 镜 · ${project.resolution === "hd" ? "高清" : "标准"}`));
    const tabs = el("div", "drama-tabs");
    for (const [key, label] of [["script", "剧本"], ["bible", "设定"], ["board", "分镜"], ["film", "成片"]]) {
      const tab = button(label, () => { view.tab = key; ui.renderPage(); }, "quiet small");
      if (view.tab === key) tab.setAttribute("aria-current", "page"); tabs.append(tab);
    }
    main.append(tabs, planningLine(data));
    ({ script: renderScript, bible: renderBible, board: renderBoard, film: renderFilm })[view.tab](main, data);
  }

  function planningLine(data) {
    const active = data.planning.find(job => ["queued", "running"].includes(job.status));
    const failed = !active && data.planning[0]?.status === "failed" ? data.planning[0] : null;
    if (active) return el("p", "notice", `正在${OPS[active.op] || active.op}（${JOB[active.status]}）。完成后这里会自动更新。`);
    if (failed) return el("p", "notice error", `${OPS[failed.op] || failed.op}失败：${failed.error || "未知原因"}`);
    return el("span");
  }

  function renderScript(main, data) {
    const pre = el("pre", "drama-script", data.project.script); main.append(pre);
  }

  function renderBible(main, data) {
    const plan = button("让 Friday 补全空白的设定", () => withButton(plan, async () => {
      await request(`/v1/dramas/${enc(view.id)}/plan`, { method: "POST", body: { op: "analyze" } }); await load(); ui.renderPage();
    }), "secondary small");
    const toolbar = el("div", "drama-toolbar"); toolbar.append(plan);
    main.append(toolbar, hint("外观和场景用英文写会更稳定。修改后，用到它的镜头会回到“已规划”，已生成的版本仍保留。"));
    const style = textarea(data.project.style, 2, 1000);
    const saveStyle = button("保存风格", () => mutate(`/v1/dramas/${enc(view.id)}`, "PATCH", { style: style.value }), "secondary small");
    const styleCard = el("section", "settings-card full"); styleCard.append(el("h2", "", "整体风格"), style, saveStyle); main.append(styleCard);
    const people = el("section", "settings-card full"); people.append(el("h2", "", "角色"));
    for (const c of data.characters) {
      const name = el("input"); name.value = c.name; name.maxLength = 30;
      const description = textarea(c.description, 2, 1500), appearance = textarea(c.appearance, 3, 1500);
      appearance.placeholder = "English look, e.g. a woman in her late 20s, short black hair, beige trench coat";
      const save = button("保存", () => mutate(`/v1/dramas/${enc(view.id)}/characters/${enc(c.id)}`, "PUT", { name: name.value, description: description.value, appearance: appearance.value }), "secondary small");
      const card = el("div", "drama-entity"); card.append(field("名字", name), field("设定（中文）", description), field("外观（英文，会写进每个出场镜头）", appearance), save); people.append(card);
    }
    people.append(button("添加角色", () => mutate(`/v1/dramas/${enc(view.id)}/characters/new`, "PUT", { name: "新角色" }), "quiet small"));
    main.append(people);
    const places = el("section", "settings-card full"); places.append(el("h2", "", "场次与场景"));
    for (const s of data.scenes) {
      const setting = textarea(s.setting, 2, 1500); setting.placeholder = "English setting: place, time, light, props";
      const save = button("保存", () => mutate(`/v1/dramas/${enc(view.id)}/scenes/${enc(s.id)}`, "PUT", { setting: setting.value }), "secondary small");
      const card = el("div", "drama-entity"); card.append(el("p", "drama-card-title", s.heading), field("场景（英文）", setting), save); places.append(card);
    }
    main.append(places);
  }

  async function submit(kind, shotIds) {
    const path = `/v1/dramas/${enc(view.id)}/takes`;
    const preview = await request(path, { method: "POST", body: { kind, shot_ids: shotIds } });
    const label = kind === "draft" ? "草稿" : "正式";
    const ahead = preview.jobs_ahead ? `前面还有 ${preview.jobs_ahead} 个生成任务。` : "";
    const basis = preview.basis === "extrapolated" ? "（草稿耗时按正式版推算，尚未实测）" : "";
    if (!await confirmAction({ title: `提交 ${preview.shots.length} 镜${label}？`,
      detail: `第 ${preview.shots.join("、")} 镜。预计约 ${minutes(preview.estimated_seconds)}${basis}。${ahead}可以随时取消排队中的镜头。`, action: `提交${label}` })) return;
    await request(path, { method: "POST", body: { kind, shot_ids: shotIds, confirm: true } });
    await load(); ui.renderPage(); toast(`已提交 ${preview.shots.length} 镜${label}。`);
  }

  function renderBoard(main, data) {
    const shots = data.shots;
    const planned = shots.filter(s => s.status === "planned").length, approved = shots.filter(s => s.status === "approved").length;
    const busy = shots.some(s => ["drafting", "finalizing"].includes(s.status));
    const bar = el("div", "drama-toolbar");
    const storyboard = button(shots.length ? "重新生成全部分镜" : "生成分镜", async () => {
      if (shots.length && !await confirmAction({ title: "重新生成全部分镜？", detail: "现有镜头会被替换（手写过 prompt 的镜头保留）。已生成的视频版本不会删除，但会和新镜头脱钩。", action: "重新生成", danger: true })) return;
      await request(`/v1/dramas/${enc(view.id)}/plan`, { method: "POST", body: { op: "storyboard" } }); await load(); ui.renderPage();
    }, shots.length ? "quiet small" : "primary small");
    bar.append(storyboard);
    if (planned) bar.append(button(`提交草稿 · ${planned} 镜`, () => submit("draft", "all_pending"), "primary small"));
    if (approved) bar.append(button(`提交正式 · ${approved} 镜`, () => submit("final", "all_pending"), "primary small"));
    if (busy) bar.append(button("取消排队", async () => {
      if (!await confirmAction({ title: "取消这个项目的生成？", detail: "排队中和正在生成的镜头都会停止；已完成的版本保留。", action: "全部取消", danger: true })) return;
      view.data = await request(`/v1/dramas/${enc(view.id)}/cancel`, { method: "POST", body: {} }); ui.renderPage();
    }, "danger small"));
    main.append(bar);
    const est = data.estimate;
    main.append(hint(`剩余镜头正式渲染约 ${minutes(est.final_seconds_remaining)}，草稿约 ${minutes(est.draft_seconds_remaining)}（按本机实测推算）。`));
    if (!shots.length) { main.append(emptyState("还没有分镜", "先到“设定”核对角色和场景，再点“生成分镜”。")); return; }
    for (const scene of data.scenes) {
      const own = shots.filter(s => s.scene_id === scene.id);
      const head = el("div", "drama-scene-head"); head.append(el("h2", "grow", scene.heading),
        button("重出本场", async () => {
          if (!await confirmAction({ title: `重新生成「${scene.heading}」的分镜？`, detail: "本场现有镜头会被替换（手写过 prompt 的保留）。", action: "重新生成" })) return;
          await request(`/v1/dramas/${enc(view.id)}/plan`, { method: "POST", body: { op: "storyboard", scene_ids: [scene.id] } }); await load(); ui.renderPage();
        }, "quiet small"),
        button("加一镜", () => mutate(`/v1/dramas/${enc(view.id)}/shots`, "POST", { scene_id: scene.id, after: own.at(-1)?.id }), "quiet small"));
      main.append(head);
      for (const shot of own) {
        const rowNode = button("", () => shotSheet(shot.id), "drama-shot");
        const body = el("div", "grow");
        const lines = shot.dialogue.map(d => `${d.name ? d.name + "：" : ""}${d.line}`).join(" / ");
        body.append(el("p", "drama-shot-action", `${shot.index}. ${shot.action}`),
          hint([shot.camera, `${shot.duration} 秒`, shot.character_ids.map(characterName).join("、"), lines].filter(Boolean).join(" · ")));
        if (shot.last_error) body.append(el("p", "hint error", shot.last_error));
        rowNode.append(body, chip(shot.status, shot.last_error && ["planned", "approved"].includes(shot.status)));
        main.append(rowNode);
      }
    }
  }

  function takeVideo(take) {
    const video = el("video", "drama-video"); video.controls = true; video.preload = "metadata"; video.playsInline = true;
    video.src = `/v1/dramas/${enc(view.id)}/takes/${enc(take.id)}/file?preview=1`; return video;
  }

  function shotSheet(shotId) {
    const shot = view.data.shots.find(s => s.id === shotId); if (!shot) return;
    openSheet(`镜头 ${shot.index} · ${STATUS[shot.status]}`);
    const current = shot.takes.find(t => t.id === (shot.final_take_id || shot.draft_take_id));
    if (current) sheetContent.append(takeVideo(current));
    if (shot.takes.length) {
      const versions = el("div", "drama-takes");
      shot.takes.forEach((take, i) => {
        const selected = take.id === shot.draft_take_id || take.id === shot.final_take_id;
        const label = `${take.kind === "draft" ? "草稿" : "正式"} v${shot.takes.filter((t, j) => j <= i && t.kind === take.kind).length} · ${JOB[take.status] || "等待"}${selected ? " ✓" : ""}`;
        const node = button(label, async () => {
          if (!take.has_output || selected) return;
          await mutate(`/v1/dramas/${enc(view.id)}/shots/${enc(shot.id)}/selected`, "PUT", { take_id: take.id }); shotSheet(shot.id);
        }, `quiet small${selected ? " selected" : ""}`);
        node.disabled = !take.has_output; versions.append(node);
      });
      sheetContent.append(field("版本（点选用于成片）", versions));
    }
    const actions = el("div", "drama-toolbar");
    if (shot.status === "draft_review") {
      actions.append(button("通过", async () => { await mutate(`/v1/dramas/${enc(view.id)}/shots/${enc(shot.id)}`, "PATCH", { approved: true }); closeSheet(); }, "primary small"));
    }
    if (shot.status === "approved") {
      actions.append(button("提交正式", async () => { closeSheet(); await submit("final", [shot.id]); }, "primary small"),
        button("取消通过", async () => { await mutate(`/v1/dramas/${enc(view.id)}/shots/${enc(shot.id)}`, "PATCH", { approved: false }); closeSheet(); }, "quiet small"));
    }
    if (["planned", "draft_review"].includes(shot.status)) {
      actions.append(button(shot.takes.some(t => t.kind === "draft") ? "重出草稿" : "出草稿", async () => { closeSheet(); await submit("draft", [shot.id]); }, "secondary small"));
    }
    if (shot.status === "planned") {
      actions.append(button("跳过草稿，直接通过", async () => {
        if (!await confirmAction({ title: "跳过草稿？", detail: "没有草稿就直接进入正式渲染，单镜约 20 分钟以上。", action: "直接通过" })) return;
        await mutate(`/v1/dramas/${enc(view.id)}/shots/${enc(shot.id)}`, "PATCH", { approved: true, skip_draft: true }); closeSheet();
      }, "quiet small"));
    }
    sheetContent.append(actions);
    const action = textarea(shot.action, 2, 300), visual = textarea(shot.visual, 3, 1200), camera = el("input");
    camera.value = shot.camera; camera.maxLength = 120;
    const duration = el("input"); duration.type = "number"; duration.min = 4; duration.max = 15; duration.value = shot.duration;
    const dialogue = textarea(shot.dialogue.map(d => `${d.name}：${d.line}`).join("\n"), 3, 3000);
    dialogue.placeholder = "每行一句：角色：台词";
    const override = textarea(shot.prompt_override || "", 4, 3000); override.placeholder = "留空则自动拼装：风格 + 场景 + 角色外观 + 机位 + 画面 + 台词";
    const save = button("保存镜头", () => withButton(save, async () => {
      const lines = dialogue.value.split("\n").map(line => line.trim()).filter(Boolean).map(line => {
        const match = line.match(/^([^：:]{1,30})[：:](.+)$/); return match ? { name: match[1].trim(), line: match[2].trim() } : { name: "", line };
      });
      const body = {};
      if (JSON.stringify(lines) !== JSON.stringify(shot.dialogue)) body.dialogue = lines;
      if (camera.value !== shot.camera) body.camera = camera.value;
      if (Number(duration.value) !== shot.duration) body.duration = Number(duration.value);
      if ((override.value || null) !== (shot.prompt_override || null)) body.prompt_override = override.value || null;
      if (action.value !== shot.action) body.action = action.value;
      if (visual.value !== shot.visual) body.visual = visual.value;
      if (!Object.keys(body).length) { closeSheet(); return; }
      await mutate(`/v1/dramas/${enc(view.id)}/shots/${enc(shot.id)}`, "PATCH", body); closeSheet(); toast("已保存。画面或台词有改动的镜头需要重新出草稿。");
    }), "primary");
    const remove = button("删除镜头", async () => {
      if (!await confirmAction({ title: `删除第 ${shot.index} 镜？`, detail: shot.action, action: "删除", danger: true })) return;
      view.data = await request(`/v1/dramas/${enc(view.id)}/shots/${enc(shot.id)}?revision=${view.data.project.revision}`, { method: "DELETE" }); closeSheet(); ui.renderPage();
    }, "danger small");
    const details = el("details", "disclosure"); details.append(el("summary", "", "手写完整 prompt（高级）"), override);
    sheetContent.append(field("这一镜发生什么（中文）", action), field("画面描述（英文，可留空）", visual), field("景别与运镜", camera),
      field("时长（4–15 秒）", duration), field("台词", dialogue), details, save, remove);
  }

  function renderFilm(main, data) {
    const bar = el("div", "drama-toolbar");
    const exportFilm = kind => async () => {
      await request(`/v1/dramas/${enc(view.id)}/exports`, { method: "POST", body: { kind } }); await load(); ui.renderPage(); toast("开始合成，完成后会出现在下面。");
    };
    bar.append(button("合成草稿全片", exportFilm("draft"), "secondary small"), button("合成正式全片", exportFilm("final"), "primary small"));
    main.append(bar, hint("草稿全片用每镜选定的草稿（已有正式版的用正式版）；正式全片要求每镜都有正式版。"));
    if (!data.exports.length) { main.append(emptyState("还没有成片", "所有镜头都有可用版本后即可合成。")); return; }
    for (const item of data.exports) {
      const card = el("section", "settings-card full");
      card.append(el("h2", "", `${item.kind === "final" ? "正式" : "草稿"}全片 · ${timeText(item.created)}`), hint(JOB[item.status] || item.status));
      if (item.error) card.append(el("p", "hint error", item.error));
      if (item.ready) {
        const video = el("video", "drama-video wide"); video.controls = true; video.preload = "metadata"; video.playsInline = true;
        video.src = `/v1/dramas/${enc(view.id)}/exports/${enc(item.id)}/file?preview=1`;
        const link = el("a", "secondary small", "下载"); link.href = `/v1/dramas/${enc(view.id)}/exports/${enc(item.id)}/file`; link.download = `${data.project.title}.mp4`;
        card.append(video, link);
      }
      main.append(card);
    }
  }

  return {
    async render(main, generation) {
      if (!view.id) return renderList(main, generation);
      if (!view.data) await load();
      if (generation !== ui.generation()) return;
      renderProject(main);
    },
    // Refresh the open project unless the user is typing in it.
    async refresh() {
      if (!view.id) return false;
      const typing = document.activeElement && ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName) && document.querySelector("#main").contains(document.activeElement);
      if (typing) return false;
      await load(); return true;
    },
    active() {
      return !!view.data && (view.data.planning.some(job => ["queued", "running"].includes(job.status)) ||
        view.data.shots.some(s => ["drafting", "finalizing"].includes(s.status)) || view.data.exports.some(e => ["queued", "running"].includes(e.status)));
    },
    open,
  };
}
