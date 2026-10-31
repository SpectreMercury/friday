# 独立 Session 的执行边界

一个 `HarnessService` 接收 Telegram 与手机请求，复用任务队列、Pi 检查点、工具执行记录、SQLite 和推理锁。Mac 是受信任宿主；手机只通过认证 API 访问服务。`local_ai.bot` 同时承接两种入口，`local_ai.server` 用于单独启用手机的部署，两者不能同时持有同一个服务锁。

## 身份与归属

| 概念 | 持久表示 | 边界 |
|---|---|---|
| 账号 | `accounts` | 独立于 Telegram ID；拥有设备和 connector 账号 |
| Session | `sessions` | 持久工作空间；包含技能选择、文件目录和管理开关 |
| Task | `tasks`、`task_inputs` | 用户目标和后续补充，归属一个 session |
| 请求执行 | `jobs` | 对目标的一次输入及其检查点、操作记录、产物和交付状态 |
| Run | `task_runs` | 请求每次执行/恢复的独立记录，保留开始、结束、attempt 和状态 |
| Voice session | `VoiceHub` 的临时连接 | 绑定账号、设备、session 和音频 epoch；断线不删除持久任务 |

数据经任务、请求或 session 外键关联原工作空间。服务端先用 Bearer 设备凭据取得账号，再检查 session 归属；不接受客户端声明的账号作为权限依据。新手机账号使用内部执行编号，不伪造 Telegram 用户。Telegram 私聊始终映射到自己的默认 session。

会话历史、输入关联、任务证据、附件解析缓存和文件工具均限定 session；账号级任务列表只汇总自己的任务。工作目录拒绝路径穿越、软链接和硬链接文件。跨 session 资料只能显式复制：派生入口选择可见消息和文件，引用材料被标记为资料，不作为新授权或指令。多个 session 主动绑定同一 Google 文档时，共享的是外部原件。

## 技能与权限

`assistant-skills` 是统一技能库，`sessions.skill_names` 是各自启用清单。`task_skills` 在任务入队时保存正文、内容哈希和描述；同一目标恢复/继续沿用快照，新目标读取当时的库版本。新任务继承对话时移除历史 `list_skills` / `read_skill` 正文，执行端只允许读取自己的快照。

session 设置与 connector 绑定使用同一递增修订号。过期更新返回 HTTP 409，用户刷新后再编辑。技能只描述方法；connector 绑定、Google 自身授权与 Mac 管理开关分别检查。审批同时限定账号、session、具体请求和操作，批准只能消费一次；等待时释放执行名额，取消后旧审批不能复活任务。

## 手机 API

接口实现位于 `local_ai/mobile_api.py`，默认仅监听 Mac 的 `127.0.0.1:8765`。响应禁止缓存；网关不得记录正文和凭据。

| 接口 | 用途 |
|---|---|
| `POST /v1/pair` | 一次性邀请换取设备凭据 |
| `GET/POST /v1/sessions` | 列出或创建独立工作空间 |
| `GET/PATCH /v1/sessions/{sid}` | 读取/修订名称、归档及管理开关 |
| `GET/POST /v1/sessions/{sid}/messages` | 当前上下文与文字输入，提交带 `request_id` 去重 |
| `POST /v1/sessions/{sid}/voice-messages` | M4A 原始请求体，`Idempotency-Key` 去重，最大20 MB、解码时限制180秒 |
| `GET /v1/tasks`、`GET /v1/sessions/{sid}/tasks` | 账号汇总和会话内请求状态 |
| `GET /v1/sessions/{sid}/tasks/{jid}/runs` | 当前目标的执行/恢复记录 |
| `POST /v1/sessions/{sid}/tasks/{jid}/cancel` | 取消原工作空间的请求 |
| `GET/POST /v1/sessions/{sid}/files` | 文件列表和 multipart 上传 |
| `PUT /v1/sessions/{sid}/skills` | `revision` 与 `skill_names` 更新启用清单 |
| `GET/PUT /v1/sessions/{sid}/connectors` | 独立资源绑定及修订号 |
| `GET /v1/sessions/{sid}/approvals` | 待处理审批；具体审批端点提交决定 |
| `POST /v1/sessions/{sid}/fork`、`…/reset` | 显式复制或清空当前会话上下文 |
| `GET /v1/events?after=CURSOR` | 按账号分页补齐，事件包含原 session 与递增游标 |
| `POST /v1/sessions/{sid}/voice` | 申请唯一实时名额，返回 WSS 路径 |

其余设备、Google OAuth/Picker、APNs 和维护配置见部署指南。文件、媒体下载同样认证；正文不放入推送。实时音频上行16 kHz单声道PCM16，下行协商采样率；队列有界，session 与 epoch 检查拒绝迟到包。插话清播放，补充通过原 session 提交，取消是独立动作。通话占用媒体调度名额但不长期持有推理锁，语音留言可以继续排队。

## 迁移与回退

第一次升级数据库前，`Store` 用 SQLite backup API 保存权限为0600的 `*.pre-sessions-*.bak`。升级应先通过原服务管理命令停止唯一实例；账号管理脚本拒绝对正在运行的旧核心执行首次迁移。旧任务、附件和按用户保存的工作文件归入对应 Telegram 默认 session，不复制到其他会话。备份不会凭空创建过去的 Run 记录。

回退手机入口使用 `MOBILE_ENABLED=0` 并重启原服务，保留新核心、数据库和 Telegram；不要把旧程序直接指向已迁移数据库。归档只隐藏入口，已有任务照常运行；清空上下文前须结束当前 session 的待处理任务。

本地测试覆盖隔离、技能快照、审批恢复、幂等提交、迁移、连接器资源范围、音频连接代次、推送及有限恢复。外部授权、部署和真机音频仍需按 [部署验收](mobile-deployment.md) 与 [iPhone 验收](../ios/README.md) 执行。
