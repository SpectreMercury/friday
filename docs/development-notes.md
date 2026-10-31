# 开发笔记（中文）

> 项目早期的详细设计与实测记录，保留作参考。安装和使用请看 [README](../README.zh-CN.md)。

## 本地 Harness：Telegram、私有 PWA 与 iPhone

运行在这台 Mac 上的聊天、生图和视频服务。Telegram 入口是你自己用 @BotFather 创建的机器人。推理在本机，消息和结果经过 Telegram。

## iPhone 与独立 Session

仓库包含通行密钥登录的私有 PWA、原生 SwiftUI iPhone 工程，以及与 Telegram 共用的 `HarnessService`。PWA 关闭公开注册，首次密钥注册须从 Mac 生成一次性授权码。部署与身份恢复见 [PWA 安全与配置](pwa-security.md)。手机入口默认关闭；本次代码变更没有重启现有服务、迁移正在使用的数据库或部署公网网关。

- **会话隔离**：每个账号下的 session 分别保存上下文、文件、任务和资源范围。新会话为空白；派生只复制明确选择的消息、文件和技能选择，不复制授权、审批、执行任务或通话。
- **技能同步**：统一技能库由 Mac 管理，每个 session 独立启用。配置用修订号防止覆盖；任务创建时保存技能正文快照，运行和恢复沿用原版本。
- **手机入口**：会话、任务、设置三个页面；文字、附件、实时语音、语音留言、审批、设备配对及推送。实时通话固定到开启时的 session，浏览别的会话不会改变归属。
- **连接与维护**：Google Drive/Docs OAuth、Picker 选择文档及服务端资源范围检查；所有者在明确启用管理的 session 中使用诊断和有限服务重启。Lark 留待后续接入。

从 [iPhone 构建说明](../ios/README.md) 和 [部署与账号配置](mobile-deployment.md) 开始；核心边界及接口见 [Session 架构说明](mobile-architecture.md)。在 `.env` 设置 `MOBILE_ENABLED=1` 后，由现有 Telegram 服务启动本地手机 API；两端共用同一队列和 GPU 调度，不要另开第二个服务实例。

本地检查：

```sh
.venv/bin/python -m pytest -q
./ios/check.sh
```

Python 测试使用临时数据及替身提供方；iOS 检查包括 Swift 语法、项目文件和可在本机运行的 Foundation 契约测试。2026-09-27 已安装并初始化 Xcode 27.0，用 iOS 27 SDK 完成模拟器与未签名 iPhone 设备目标的完整编译，两者无编译警告。尚未完成真机签名构建、真实 Google/APNs/VPS 联调或 iPhone 音频验收，不能用编译结果推定首音频延迟和锁屏效果。

## 使用

先向机器人发送 `/start`。普通文字通过 Pi Agent 调用 Qwen3.8 27B；它可以按需要调用本地工具。

| 操作 | 功能 |
| --- | --- |
| `/image 一只坐在窗边的橘猫` | Qwen Image 2.1 生图，默认 1024×1024、25 步 |
| `/video 纸船在池塘中缓慢漂动` | H3 视频与音频，默认 15 秒、352×608、8 步 |
| 图片/文档＋问题 | 保存原附件并按问题提取资料（文本、PDF、Word、Excel、图片OCR） |
| 图片＋`/video 描述` | 明确请求使用图片作为视频首帧 |
| `/video_hd 描述` | 480×864 视频；图片说明也可用此命令 |
| `/status` | 模型下载状态、自己的任务与当前引擎 |
| `/cancel 任务号` | 取消自己的任务；省略编号取消最近的待处理任务 |
| `/reset` | 清除自己的聊天历史；先取消未完成的聊天任务 |
| `/allow 数字用户ID` | 管理员添加朋友 |
| `/deny 数字用户ID` | 管理员撤销授权，并取消该用户未完成的任务 |

短剧可直接提交剧本／文本附件和目标时长；先估算24小时容量，再持久化分镜、逐段交付并合并。当前配置为1个GPU生成槽、2个分镜预备窗口；见 [短剧队列与实测容量](video-projects-2026-09-28.md)。

每人最多 3 个未完成任务（分镜预备窗口单独限流），同一会话按序推进，最多四个不同会话并行；GPU 推理仍互斥，外部工具等待和结果发送不占推理锁。切换模型时释放上一模型的内存；闲置 10 分钟后卸载。图片以原 PNG 文件返回。视频默认竖屏；上传图片建议使用竖图以免首帧被拉伸。机器人仅接受私聊，不处理群组消息。

## 新版 Agent（AGENT_V2，评测中）

在 `.env` 设置 `AGENT_V2=1` 后，聊天任务改由进程内的 `local_ai/agent/` 执行，不再启动 Pi 子进程。未设置时仍走下方的 Pi Agent。设计与取舍见短剧流水线改造方案文档（“通用 Agent 架构”标签页）。

- **循环**：模型回复 → 执行工具 → 结果写回，直到给出最终回复；开启 thinking。不再有任务合同、证据分类和模型复审，写入、生成、发送是否成功以 `tool_operations` 回执为准。每次最多 40 次工具调用、20 分钟，用完后任务进入“已暂停”，说“继续”接着做。
- **工具集**：默认只给模型 core / web / files / media 四组（约 24 个工具），其他组（drama、google、computer、admin、mcp）由模型用 `enable_toolset` 按需打开，权限仍由宿主检查。
- **记忆**：账号级 `runtime/memory/<account>/USER.md` 与 `MEMORY.md`，每次任务放进系统提示；说“记住……”会写入，App 设置页可查看和删除。
- **历史检索**：所有会话的消息写入 `agent_messages` 并建 FTS5（trigram）索引，模型用 `session_search` / `session_read` 找回以前的对话。
- **定时任务**：`schedule` 工具创建（需在 App 里确认），到点在原会话执行；`notify=when_relevant` 时没有情况就回复“【静默】”，不推送。App 设置页可暂停或删除。
- **其他**：执行中同一会话发来的新消息并入当前任务；`delegate` 用干净上下文跑子任务，只带回报告；审批等待时释放 GPU，批准后从检查点续跑。

评测集在 `evals/agent_cases.py`，取自本机历史任务的同类用例。运行前先停止常驻服务：

```sh
.venv/bin/python scripts/eval_agent.py --agent v1   # 现有 Pi harness
.venv/bin/python scripts/eval_agent.py --agent v2   # 新版 agent
```

结果在 `runtime/evals/<agent>-<时间>/summary.md`。

## 短剧项目

App 底部“短剧”标签：导入剧本（粘贴或 .txt/.md/.fountain）→ 设定（角色外观、场景、风格，可让 Friday 补全）→ 分镜（逐场生成，可逐镜编辑、增删、重出）→ 草稿（256×416、4 步）→ 审片通过 → 正式（与草稿同 seed）→ 合成全片。每个项目有自己的会话，渲染不会卡住聊天；一镜失败不影响其他镜头。聊天里也可以用 drama 工具集完成同样的操作。旧的 `generate_video_project` 在新版 agent 中已停用。

## Pi Agent

使用官方 `@earendil-works/pi-agent-core` 与 `pi-ai` **0.87.1**，依赖由 `pi/pnpm-lock.yaml` 固定。Pi 负责真实 Agent 循环、模型工具调用和工具结果回传；Python 负责 Telegram 身份、授权、数据库、工具执行和 GPU 调度。没有修改 Pi 上游源码。

可以直接发送：

- “把明天下午三点开会保存到 meeting.md，再读给我听。”
- “列出我的笔记。”
- “帮我生成一张窗边的橘猫图片。”
- “做一段纸船在池塘上漂动的视频。”
- “查询我最近的任务和本机模型状态。”

基础工具：`calculate`（十进制算术与条件比较）、`list_files`、`read_file`、`write_file`、`system_status`、`task_status`、`generate_image`、`generate_video`。现已增加联网搜索、网页读取和管理员电脑工具，范围与确认规则见下方“联网与电脑工具”。附件不推断为视频；只有显式 `/video` 或 `/video_hd` 附件命令使用首帧工作流。结构化 API、技能发现和独立语音合成也通过统一能力注册表开放。

新 session 的工作文件保存在独立的 `runtime/workspaces/<workspace_key>/`；旧 Telegram 默认 session 保留原 `<Telegram用户ID>` 目录。模型笔记工具只接受扁平 txt/md/json/csv 文件名，禁止目录穿越、符号链接和硬链接文件访问。最多 100 个文件，每个写入最多 6000 字符，读取最多 2000 字符；同一 session 内同名文件覆盖。`/reset` 清除 Telegram 默认 session 的聊天上下文，保留笔记和任务记录。

每次执行使用 Pi 子进程，但任务、原始请求、后续补充、可修订计划、完整工具对话和操作回执保存在 SQLite，进程不再等同于任务。宿主按语义关联追问；明确换话题保持独立。执行中的补充在模型/工具边界处理，尚未启动的旧动作可被阻止；已经发生的动作不能假装撤回。

简单问答不强制计划；复杂任务可以修订计划。原始要求与计划分开，独立审查对照用户要求、实际工具结果和回执，允许 completed / partial / pending / blocked / needs_input；执行结束和 Telegram 交付另行记录。审查也是模型判断，不能保证任意问题都正确，因此保留可重放的跨场景评测与证据。

每次执行最多 24 轮、48 次工具调用、15 分钟工作时间；宿主另设 30 分钟总上限，收尾和审查共用该上限。单次生成超时 300 秒，任务要求提取超时 180 秒，审查超时 300 秒；最多两次审查修复，剩余总时间不足时不保证全部机会都能执行。文字输出达到单次 3072 token 上限时，从原文末尾自动续写，最多续写两次并共用工作预算；仍未写完时保留正文并标记未完成。生成中断不能被审查改成已完成，审查明确给出的部分完成状态也不会再被升级。Pi 仅接收动态工具 schema，不接收 Telegram Token 或宿主凭据。模型视图按完整工具对压缩，原始检查点和执行证据仍在数据库，可用 task_evidence 分页读取。

媒体工具返回真实排队任务号，随后由原 worker 生成并自动回传。它们计入每用户 3 个未完成任务额度；同一 Agent 任务重复提交相同媒体请求会复用任务号。取消运行中的 Agent 任务时，也取消其尚未执行的媒体子任务；已经完成的笔记写入不会回滚。工具执行记录和笔记仅保存在本机。

Node.js 要求 >=22.19.0；当前 `vendor/node` 指向本机 Node 24.19.0。迁移机器时安装 Node 和 pnpm，再执行：

```sh
.venv/bin/python scripts/setup_pi.py --node /absolute/path/to/node --pnpm /absolute/path/to/pnpm
vendor/node pi/runner-v2.mjs --check
```

Pi 推理仍只访问本机 llama.cpp。未配置云端推理、遥测接收端或自动加载第三方插件。`runtime/pi.log` 为本机错误诊断日志；SQLite 工具记录可能包含笔记内容，请按个人数据管理。

## 本机配置

`.env` 保存 Bot Token、管理员和授权名单，权限为 600，已被 Git 忽略。不要分享该文件。授权名单为空时不会对外开放服务。推理接口只监听 `127.0.0.1`。

`models.lock.json` 固定每个模型的来源、版本、文件大小与 SHA-256。`sources.lock.json` 固定上游项目版本。模型、运行时、日志和聊天数据库都不提交到 Git。

从项目目录执行：

```sh
# 下载/断点续传并逐个校验模型
.venv/bin/python scripts/download_ranges.py
# 前台启动 Bot
.venv/bin/python -m local_ai.bot
# 安装当前用户的登录自启服务
.venv/bin/python scripts/service.py install
# 查看、停止、重新启动
.venv/bin/python scripts/service.py status
.venv/bin/python scripts/service.py stop
.venv/bin/python scripts/service.py start
```

服务使用 `caffeinate` 保持本机醒着，停止服务后恢复正常休眠行为。断网时 Telegram 会暂存消息，恢复联网后继续轮询；Telegram 不保证保留超过 24 小时的未收取消息。开机后需要登录该 macOS 用户，自启服务才会运行。

## 本地验证与日志

运行本地推理验证前，先停止 Bot 服务，避免抢占模型资源。

```sh
.venv/bin/python -m pytest -q
.venv/bin/python scripts/local_test.py chat
.venv/bin/python scripts/local_test.py image
.venv/bin/python scripts/local_test.py video --smoke
```

`--smoke` 生图只跑 4 步，视频使用较小画面和较短时长；它用于检验功能，不代表最终画质。视频标准配置沿用 [MacMax 工作流](https://github.com/Bambushu/minimax-h3-mac)，编码器改为 [pottokao 的 H3 专用 abliterated GGUF](https://huggingface.co/pottokao/MiniMax-H3-TextEncoder-Qwen3VL-32B-abliterated-GGUF)。衍生工作流沿用上游 MIT 许可，权重按各自仓库许可使用。

- `runtime/bot.log`：Bot 服务日志，不记录 Token 和聊天正文。
- `runtime/chat.log`、`image.log`、`video.log`：推理服务日志。
- `runtime/*-history.json`：生成任务的本地诊断记录，可能含提示词。
- `runtime/bot.sqlite3`：各用户独立的聊天记录、授权与任务队列。
- `vendor/ComfyUI/output/telegram/`：生成文件，按任务编号分目录。

服务所有者取得锁后恢复被中断任务；只读操作可以重做，成功操作复用回执，结果不确定的写入先核对。发送前写入 outbox；Telegram 明确拒绝可按策略重试，连接中断或回执丢失则记为 uncertain，暂停自动重发。用户明确要求再次发送时形成新的交付请求。原片和日志保留在本机；`/reset` 只删除该用户的聊天上下文，不删除历史任务与生成文件。

## 历史实测记录（2026-09-25；最新系统验收见改造说明）

2026-09-25，M5 Pro / 64 GB，全部 9 个权重文件通过 SHA-256 校验。使用 llama.cpp（聊天）与 ComfyUI（图片/视频），无需 Ollama。

| 项目 | 结果 | 含服务启动的实测耗时 |
| --- | --- | --- |
| Qwen3.8 27B 聊天 | 两轮短对话，正确记住上一轮暗号 | 3.53 秒 |
| Qwen Image 2.1 | 1024×1024、25 步，PNG 完整 | 166 秒 |
| H3 文字生成视频 | 352×608、124 帧、5.17 秒，H.264 + AAC | 359 秒 |
| H3 图片生成视频 | 256×416、73 帧、3.04 秒，H.264 + AAC | 172 秒 |

两个 MP4 均通过完整解码检查并抽查画面。图生视频使用小尺寸验证；默认 352×608 和高清 480×864 的图生视频耗时会更长。高清模式尚未单独实测。上述耗时是这些具体输入的记录，不是所有请求的固定速度。

Telegram 的图片请求、排队、生成和 PNG 回传已完整验证；聊天、文字视频及首帧视频已完成本地推理验证。视频测试时系统已用内存峰值约 41 GiB（包含操作系统和其他应用，不等于模型独占显存）。功能测试 8 项通过，覆盖授权、用户隔离、排队、取消和凭据日志脱敏。自启服务已恢复。

完整测试报告见 `runtime/test-*.json`，生成文件见 `vendor/ComfyUI/output/telegram/`。

### Pi 集成验证

同日完成 Pi 集成，功能测试共 12 项通过，新增文件访问边界、媒体任务归属与去重、撤销授权、子进程取消与媒体子任务取消检查。

- 实际 Qwen + Pi 连续调用写笔记、读笔记、查询状态，结果正确，约 24 秒。
- 普通文字请求经 Pi 提交图片和视频任务，再由本地推理引擎实际生成：图片约 30 秒，视频约 163 秒。两者使用 smoke 配置；视频通过完整解码检查。
- 同一用户跨两次 Pi 进程正确取回测试编号，另一用户无法取得该编号。
- 后台启动时 Pi 运行时预检通过，Telegram 服务已恢复。此次 Pi 验证为本地集成测试，尚未通过用户 Telegram 消息验证完整 Pi 链路。

验证日志：`runtime/pi-smoke.log`、`runtime/pi-media-test.log`、`runtime/pi-history-test.log`。

### Telegram 对话显示

收到已授权用户的消息时添加 👀 reaction；等待和生成回复期间定期刷新 typing。普通对话不发送排队、开始处理或任务编号提示，媒体结果也不附内部编号。需要任务详情时可主动使用 `/status`。Reaction 或 typing 请求失败不影响实际回复。相关测试在内的功能测试共 14 项通过。

## 中文语音：STT / TTS

- 识别：Qwen3-ASR-1.7B，MLX 8bit。
- 合成：Qwen3-TTS-12Hz-1.7B-CustomVoice，MLX 8bit；默认 Serena 中文女声，可在 `.env` 设置 `TTS_SPEAKER` 后重启服务。
- Telegram 语音消息或音频附件（最长 3 分钟、20 MB）会自动转录给 Pi；回复同时发送文字和 OGG/Opus 语音。文字输入仍只回文字。
- 保留 👀 reaction 和 typing，不发送转录过程或内部步骤。语音合成失败时保留文字回复。
- 语音回复最多朗读前 600 字符，完整内容保留在文字回复中。该版本处理发送完成的语音消息，不是实时通话。
- MLX 使用独立环境 `vendor/speech-venv`，依赖固定于 `requirements-speech.lock`；模型版本、大小、校验值固定于 `speech-models.lock.json`，下载约 5.5 GB。运行时离线加载模型。
- 语音、聊天和媒体推理共享 GPU 调度锁，逐阶段加载并释放模型。后台排队不会抢占正在生成的视频。
- 原始输入音频在处理结束或取消后删除；识别文字进入该用户的聊天记录，生成语音保留在 `runtime/speech-output/`。诊断日志为 `runtime/speech.log`。

重新安装语音环境：

```sh
uv venv vendor/speech-venv --python .venv/bin/python
uv pip sync --python vendor/speech-venv/bin/python requirements-speech.lock
.venv/bin/python scripts/download_speech.py
```

模型来源：[Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR)、[Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS)；Mac 推理：[MLX Audio](https://github.com/Blaizzy/mlx-audio)。上游中文能力与速度说明不等于本机实测结果。

语音本机实测（2026-09-25）：19 项功能测试通过。官方中文样本准确转写“甚至出现交易几乎停滞的情况。”，含首次加载约 5.70 秒；Serena 合成“你好，我是你的本地助手。现在可以用中文语音和我聊天了。”约 32.38 秒，再转录约 1.11 秒，文字内容一致。OGG/Opus 完整解码通过。速度仅代表该短样本；尚未通过用户的真实 Telegram 语音验证整条链路。报告：`runtime/speech-test/report.json`。复测脚本：`scripts/test_speech_local.py`（先停止 Bot，准备官方样本于 `runtime/speech-test/official-zh.wav`）。

### 任务执行与能力发现

当前实现与验收记录见 [系统改造说明](system-upgrade.md)。`capabilities` 返回当前用户实际可见能力及缺失配置，`list_skills` / `read_skill` 按需加载本地任务指导。计划只描述方法，不能授予权限。

公开 JSON 接口可以使用 `api_get`（支持 JSON pointer 与数组分页）。`config/providers.json` 注册具名 GET 接口、参数 schema 和默认参数；已接入 Open-Meteo 地理编码与天气接口。需要认证时仅允许引用 `PROVIDER_*` 专用环境变量，认证留在 Python 宿主，不进入模型；带凭据的 provider 默认只向管理员开放，需配置 `access: user` 才共享给已授权用户；禁止认证跨重定向泄露。不承诺未接入系统或未提供凭据的私有 API 已可用。

生产技能单独放在 `~/.friday/skills/<name>/SKILL.md`，由 `FRIDAY_SKILLS_DIR` 指定；`assistant-skills/` 只保留分发种子。运行 `.venv/bin/python scripts/install_skills.py` 仅补装缺少的技能，不覆盖本地修改。修改独立库后，新任务读取新版，已有任务保留快照；每个 session 分别选择启用。详情见 [技能库维护](skills-library.md)。技能提供指导，不授予权限或自动运行脚本。

独立语音使用 `generate_audio`，按用户指定文本生成最多 600 字的 OGG。输入语音仍走识别，再执行用户要求；不会因出现“音频”一词直接拒绝或生成视频。

管理员电脑写入可选择单次批准，或批准本次执行请求（含重启续接）内对一个明确目录的文件写入；授权绑定身份、会话、不可变请求及目录，24 小时失效。原始 shell 命令仍逐条确认，不将目录批准误当作 shell 沙箱。批准状态持久化，重启后仍可接续；撤销/取消不能继续执行。


## 联网与电脑工具

所有已授权用户可使用 `web_search`（DDGS，按 Bing → DuckDuckGo → Brave 切换，无需 API Key）和 `web_read`（公开网页正文）。搜索可能受限流或验证码影响；读取工具不执行网页 JavaScript，不登录网站，不解析 PDF/Office 文件。搜索词发送到搜索服务，网页请求发送到目标网站；Qwen 推理仍在本机。

网页工具拒绝本机、私网地址、携带凭据的网址和非标准端口，逐次校验重定向与 DNS。当前 Mac 的 TUN 代理使用 198.18.0.0/15 虚拟 DNS 地址，遇到该地址时通过固定 Google DoH 端点解析公开地址，再校验和连接；域名会发送到该 DNS 服务。

只有所有者在启用设备管理的 session 中可使用下列工具，其他账号或 session 看不到这些工具，执行端也再次检查身份和当前设置。迁移时保留原 Telegram 所有者默认会话的管理能力，随后可在 App 中关闭：

| 工具 | 能力 |
| --- | --- |
| `computer_list` | 列出主目录下的文件或目录，最多 100 项 |
| `computer_find` | 按文件名关键词查找，最多 30 个结果和 10000 项扫描 |
| `computer_read` | 读取小于 1 MB 的 UTF-8 文本，返回最多 8000 字符 |
| `computer_write` | 创建或覆盖文本，每次最多 2000 字符；完整展示路径和内容后确认 |
| `terminal` | 在 Mac 用户账号下运行 zsh 命令，超时 60 秒；结果有长度上限 |

文件专用工具默认开放管理员主目录内的普通文件，排除隐藏配置、Library、助手 runtime/vendor/models 目录和部分凭据/数据库类型。**终端不是操作系统沙箱**，批准的命令具有该 Mac 用户账号的权限，可以访问账号可访问的其他目录或联网；它不会继承 Bot Token 等进程环境变量，但这不阻止已批准命令自行读取文件。

`pwd`、`date`、`uptime`、`uname -a`、`sw_vers`、`df -h` 这几个精确只读命令自动执行。其他原始终端命令需要单次确认；文件写入可单次批准或对本次请求的明确目录授权。按钮绑定管理员身份、私聊和任务，记录保留 24 小时；当前执行最多等待 3 分钟，收到新的用户输入会先结束等待并处理补充。服务重启保留未决批准；拒绝、取消或授权过期后不执行。`/cancel` 终止当前操作及相关子进程。

示例：

- “搜索 Python asyncio 官方文档，读原文后给我一句总结和链接。”
- “列出我的 Downloads 文件夹。”
- “在 Documents 里按文件名查找包含 invoice 的文件。”
- “读取指定路径的 txt 文件。”
- “把这段文字保存到 Documents 下的 welcome.txt。”（弹出确认）
- “用终端查询磁盘剩余空间。”

现已接入 Mac 屏幕截图与 OCR、可用的辅助功能文字、应用发现和打开、DNS/HTTP/TLS/代理诊断、DNS 缓存刷新及 Clash Verge 节点切换。桌面能力需先运行 `.venv/bin/python scripts/install_desktop.py` 安装本机辅助程序，并在对应会话开启「Mac 管理」。屏幕录制与辅助功能授权由 macOS 单独管理，后台服务的授权须实际检查，不能沿用开发终端的测试结果。详细操作见 [桌面与网络工具](desktop-network.md)。

当前模型只接收文本，屏幕理解依据 OCR 和辅助功能信息；截图保存在当前会话文件中，不等同于完整图像理解。尚未接入通用浏览器页面点击、浏览器登录会话或鼠标键盘控制。外部资料（包括屏幕文字、应用名和代理节点名）标记为不可信输入，不能授权操作。

联网与电脑工具实测（2026-09-25）：累计 41 项测试通过。实际 Qwen + Pi 在同一轮完成测试文件读取、联网搜索、官方网页读取和终端 `date` 调用，约 35.70 秒，测试编号和引用网址正确。报告：`runtime/computer-test/report.json`。确认按钮的身份绑定、拒绝、取消与单次使用已做模拟测试；尚未通过用户的 Telegram 点击完成真实确认交互验证。服务已恢复。

## 媒体生成与发送核验（2026-09-26 修复）

媒体任务现与发起对话关联，生成状态、本地文件和 Telegram 消息回执分别保存。“没收到／自查”仍交给模型理解并核查记录；明确要求已有媒体时，通过 `list_media` 和 `send_media` 查询并实际发送，按逐文件回执报告结果。发送失败记录与重试时间持久化；缺文件或累计三次发送失败后尝试通知用户。正常对话不展示内部检查。

历史验证：54 项测试通过；普通橘猫请求完成真实 Qwen 与图像模型测试，Telegram 发送使用模拟适配器验证。旧数据没有消息回执，保留为不可核验，不补造“已送达”。具体原因、与 OpenClaw／Hermes 的对照及限制见 [交付核验说明](delivery-review.md)。


现在可直接说“把所有生成的照片都给我”或“把照片再发给我”。图片清单按类型分页，发送失败会单独报告；本轮重复调用不会重复发送已经有回执的文件。新一轮明确要求重发时可以再次发送。照片不会自动混入视频。此功能不自动重放过去的请求。


## 全本地语音优化（2026-09-26）

Telegram 语音现使用常驻 ASR/TTS，聊天与语音模型可同时保留在内存中，图像/视频生成前释放语音模型。电话场景另有流式音频、断句、插话取消和普通话/英语/日语/粤语路由组件；粤语采用本机 macOS Sinji 声音。未接入云端语音。

本地短句的 ASR→Qwen→首音频约 1.7–2.4 秒，不含断句和电话网络。电话线路与完整订位流程尚未接通。具体测试方法、误差和边界见 [本地语音实测说明](local-voice-runtime.md)。

## Harness 失败恢复（2026-09-26）

修复搜索失败后被轮次上限截断为空回复，以及失败请求未进入对话历史、追问误跳回旧媒体的问题。检索会切换来源；同一搜索通道连续两次完整失败后停止空转，保留网页读取路径。空页和验证页返回明确质量标记。模型中断会在原工具记录上续接一次，额度用完后仍生成基于记录的正文。

媒体校验限定在相关请求内；无关媒体声明触发回到原始任务的修复，不再把餐厅查询变成图片自查。已授权的检索不再以“要我重新查吗”收尾；这类回复会触发只读补救。真实失败会记录具体原因并保留到后续上下文；`/reset` 同时清除上下文恢复入口，但保留可主动查询的任务记录。

验证方法、实跑范围和限制见 [Harness 恢复验证](harness-recovery.md)。

## 能力迭代与核心升级

Friday 支持生成并审阅 skills、本机 MCP 与核心代码升级草案。原生 App 在管理会话的「能力与升级」中确认具体版本；独立升级器先运行候选测试，等待任务结束后切换，启动健康检查失败自动恢复旧代码。技能与 MCP 仍按会话选择、按任务固定版本。范围、恢复边界及运维说明见 [自我升级](self-evolution.md)。
