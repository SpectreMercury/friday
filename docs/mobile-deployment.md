# 手机入口、Google 连接器与有限 Mac 维护

这份指南用于部署仓库内的手机服务。模板不会自动创建 VPS、购买域名、安装证书或重启当前 Telegram 服务；先填入自己的域名、路径和凭据，再由宿主启动。

个人使用现在优先采用 [通行密钥 PWA](pwa-security.md)，无需 Apple 签名。下文网关、账号与连接器仍共用；原生 Bearer 配对需另外显式开启，PWA 的首次注册使用 `passkey-invite`，不是 `invite`。

## 1. 单一服务与接入网关

保留 Telegram 时，手机 API 和 Telegram 运行在同一 harness 进程中，共用 SQLite、任务队列、GPU 调度与服务锁。API 默认只监听 `127.0.0.1:8765`，`GET /healthz` 返回最小健康状态。数据库默认为 `runtime/bot.sqlite3`。

在仓库根目录执行 `uv sync` 安装依赖，在本机 `.env` 中设置：

```dotenv
MOBILE_ENABLED=1
MOBILE_PORT=8765
MOBILE_PUBLIC_URL=https://harness.example.com
PWA_ENABLED=1
MOBILE_NATIVE_ENABLED=0
MOBILE_NATIVE_PAIRING=0
```

`MOBILE_PUBLIC_URL`、Google 回调的 origin、Caddy 域名和 App 中填写的 HTTPS origin 必须一致。改端口时同步修改 frpc 的 `localPort` 和 watchdog 的 `--local-health`；不要把回环监听改为公网监听。

通过 `.venv/bin/python scripts/service.py install` 安装既有 Telegram/harness LaunchAgent，已有服务可用 `restart` 重启后读取配置。首次迁移前先停止旧服务；数据库迁移会在原目录生成 `bot.sqlite3.pre-sessions-*.bak`。本次代码实现没有代你修改 `.env`、安装 LaunchAgent 或重启现有服务。

不需要 Telegram 时，可单独运行 `.venv/bin/python -m local_ai.server`，此入口直接开启手机 API。它与 Telegram 入口二选一，共用服务锁，不能并行运行。当前 `scripts/service.py` 安装的是 Telegram 入口；若将 LaunchAgent 改为纯手机入口，仍需保留维护工具使用的固定 `local.qwen.telegram` label。

账号命令都通过项目 Python 执行，例如 `.venv/bin/python scripts/mobile_admin.py accounts`。`create-account NAME` 创建受邀账号（仅所有者加 `--admin`）；默认 PWA 使用 `passkey-invite --account ACCOUNT_ID`，或 `passkey-invite --telegram-user TELEGRAM_UID` 为已有 Telegram 账号创建通行密钥注册码。注册码 10 分钟有效，在开始注册验证时即消费；取消或失败后回 Mac 重新生成。指定其他数据库时，把 `--database PATH` 放在子命令前。

原生 App 使用单独的 `invite --account ACCOUNT_ID` / `invite --telegram-user TELEGRAM_UID` 配对邀请，需显式开启 `MOBILE_NATIVE_ENABLED=1` 与 `MOBILE_NATIVE_PAIRING=1`；这些原生邀请不能用于 PWA。配对完成后可关闭新设备配对开关。

部署链路：iPhone → HTTPS/WSS → VPS 上 Caddy → VPS 回环地址 frps 端口 → 双向 TLS 隧道 → Mac 回环地址手机 API。手机不需要替换现有 VPN。

1. 给 VPS 域名配置 DNS；仅开放 Caddy 所需的 `80/443` 和 frp 的 `7000/TCP`。端口 `18765` 仅绑定 VPS 回环地址，模型 API 和终端不转发到公网。
2. 使用 `deploy/Caddyfile.example`，替换 `harness.example.com`。Caddy 自动管理公网 HTTPS；模板不启用访问日志、正文日志或缓存，`/healthz` 在公网返回 404。
3. 为 frp 创建独立私有 CA、带正确 SAN 的服务器证书及客户端证书。客户端证书使用 clientAuth，服务器证书使用 serverAuth；两端设置 `trustedCaFile`，服务器强制 TLS，客户端校验服务器名称。私钥、随机 frp token 和配置文件仅允许各自服务账号读取。
4. 填写 `deploy/frps.toml.example` 与 `deploy/frpc.toml.example`。不要使用示例 token；它和 TLS 客户端证书都必须匹配。
5. 在 VPS 启动 frps 与 Caddy，再在 Mac 启动 frpc。按对应工具验证配置后，先测试文本对话及事件补齐，再测 WSS 语音。
6. 将 `deploy/local.qwen.mobile-tunnel.plist.example` 复制到当前用户的 `~/Library/LaunchAgents/local.qwen.mobile-tunnel.plist`，把 `/opt/homebrew/bin/frpc` 改为实际安装位置，把配置路径改为已填写的 frpc TOML。所有 plist 路径必须是绝对路径，不能写 `~` 或依赖 shell 展开。设置文件权限 `0600` 后注册：

```sh
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/local.qwen.mobile-tunnel.plist"
```

当前模板适用于已登录的 macOS 用户 LaunchAgent。`gui/UID` 域不存在时，应先登录该用户；模板不提供开机登录前的系统 LaunchDaemon。维护工具依赖固定 label、当前账号所有权和不可被其他账号修改的 plist。

Caddy 是 TLS 终止点，运营 VPS 的人属于受信任链路。不要给业务请求增加正文调试日志；APNs、OAuth 或其他访问令牌都不得放在业务 URL 查询参数中。OAuth 回调的短期 code 不应进入网关访问日志。

参考：[Caddy reverse_proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)、[frp 双向 TLS](https://gofrp.org/en/docs/features/common/network/network-tls/)。

## 2. Google Drive / Docs

在 Google Cloud 项目启用 Drive API 和 Docs API，建立 **Web application** OAuth client，配置授权同意界面及初期测试用户。登记精确的 HTTPS 回调，例如：

```dotenv
GOOGLE_CLIENT_ID=your-web-client-id
GOOGLE_CLIENT_SECRET=your-web-client-secret
GOOGLE_REDIRECT_URI=https://harness.example.com/v1/oauth/google/callback
GOOGLE_PICKER_API_KEY=browser-api-key
GOOGLE_PICKER_APP_ID=google-cloud-project-number
```

手机调用 `POST /v1/connectors/google/authorize`，在系统浏览器打开返回的 `authorization_url`（同时保留原生客户端兼容字段 `auth_url`，两者相同）。Mac 使用一次性、10 分钟有效、与 harness 账号绑定的 state 和 PKCE verifier 换取离线授权；回调不依据浏览器提交的账号 ID 选择归属。授权信息与刷新令牌保存到当前 Mac 用户的 Keychain，SQLite 只保存不可用于 Google 登录的引用。Google Picker 页面按 Google 官方集成方式使用短期 access token；它仅存在于受限浏览器页面及 Google Picker 中，不进入模型、同步事件、本服务 URL 或日志。Keychain 锁定时需要在 Mac 上解锁，不能回退成明文文件。

授权成功后，连接仍然属于账号，**不自动给任何 session 授权资源**。在 session 的资源设置中选连接，使用最新 `revision` 提交完整绑定清单。HTTP 参数名是 **`revision`，不是 `expected_revision`**；后者只是 Python 内部方法的参数名。配置冲突返回 HTTP 409，应刷新后重新编辑；替换绑定是原子的。断开连接会同时移除该账号各 session 的绑定、删除本机凭据，Google 账号上的应用同意记录可在 Google 账号安全设置中另行撤销。

HTTP 接口按以下契约调用。除 OAuth 回调与 Picker 票据页面/选择回调外，默认 PWA 使用同源、HttpOnly 登录 Cookie；写请求还须携带匹配的 `X-CSRF-Token` 与精确同源 `Origin`，不能仅凭 Cookie 写入。只有显式启用的原生 App 使用 `Authorization: Bearer <设备凭据>`；浏览器登录 Cookie 不能当作 Bearer 凭据使用。OAuth 回调验证一次性 state，Picker 页面与选择回调验证绑定账号、会话和发起设备的一次性票据。

| 接口 | 请求 / 返回 |
|---|---|
| `GET /v1/connectors` | 返回 `{"items":[连接信息]}` |
| `GET /v1/sessions/{sid}/connectors` | 返回 `{"items":[绑定],"revision":整数}` |
| `PUT /v1/sessions/{sid}/connectors` | 请求 `{"revision":整数,"bindings":[绑定]}`；返回 `{"items":[绑定],"revision":新整数}` |
| `POST /v1/connectors/google/authorize` | PWA 发送 `{}`；返回含 `authorization_url` 与兼容字段 `auth_url` 的对象 |
| `POST /v1/connectors/{cid}/picker` | 请求 `{"session_id":"...","revision":整数}`；返回 `{"url":"...","expires_at":时间戳}` |
| `GET /v1/google/picker?ticket=...` | 系统浏览器获取一次性 HTML 页面 |
| `POST /v1/google/picker/selection` | 页面发送 `{"ticket":"...","ids":["文档ID"]}`；返回 `{"items":[绑定],"revision":新整数}` |
| `DELETE /v1/connectors/{cid}` | 断开当前账号拥有的连接 |

每个绑定形如 `{"connection_id":"...","resource_ids":["文档ID"],"can_create":false}`。`PUT` 是替换整个 session 绑定清单；移除某连接时也必须携带其他应保留的绑定。session 的名称、skills 和资源配置共用修订号，不能用缓存的旧修订号静默覆盖新设置。

当前注册能力：

- `google_connections`：发现当前 session 绑定的连接编号及资源范围。
- `google_list_files`：分页读取明确绑定文件的元数据，不调用全盘搜索。
- `google_read_doc`：分页读取 Docs 各标签页的正文，并标记外部内容为不可信资料。
- `google_create_doc`：在允许创建的 session 中创建空文档，回执文档 ID 自动加入该 session 的资源范围。
- `google_append_doc`：向已绑定文档的第一个标签页末尾追加文本；可携带 `revision_id` 拒绝并发修改。

请求仅使用 `drive.file`。在同一 Google Cloud 项目启用 **Google Picker API**，配置 API key 和项目编号；将 HTTPS 网关 origin 加入 Web OAuth client 的 Authorized JavaScript origins。API key 限制到 Picker API，并允许自己的 `https://harness.example.com/*` 和 `https://docs.google.com/*` 网站来源。Picker 页面只发送 origin 作为 Referer，不发送票据或路径。

在 session 中点击“选择 Google 文档”，App 会申请绑定该账号、session、连接及配置修订号的 10 分钟一次性票据，然后用系统浏览器打开服务端 Picker 页面。页面在加载第三方脚本前从浏览器地址栏历史移除票据，并带有 nonce CSP、防嵌入及 no-store 响应头。选择文档后，Mac 用当前连接逐一验证 Google 已授予访问，再原子合并资源 ID，保留现有 `can_create` 和其他绑定。配置修订冲突时返回 409，用户需回到 App 刷新。

**填写文件 ID 本身不会让 Google 授予权限**；手动输入仅适合已经由本应用创建或经同一 Cloud 应用 Picker 授权的文件。已有 Google 文档通过上述 Picker 流程授权，不能通过扩大到全盘 Drive scope 绕过选择。

文档操作通过现有工具执行日志记录。收到超时、5xx、重定向或缺失写入回执时，结果标记为不确定，禁止自动重复创建或追加；需要在 Google 上核对实际结果。多个 session 明确绑定同一 ID 时，修改的是同一外部文档。Lark 适配器尚未实现，也不会假装已经可用。

参考：[Web OAuth 离线授权](https://developers.google.com/identity/protocols/oauth2/web-server)、[Drive 文件权限范围](https://developers.google.com/workspace/drive/api/guides/api-specific-auth)、[Google Picker](https://developers.google.com/workspace/drive/picker/guides/overview)。

## 3. Mac 维护与独立守护进程

`mac_status` 和 `mac_restart_service` 仅在所有者账号、当前 session 明确启用 `management` 时可见；每次执行重新验证权限。维护工具只允许两个固定服务：

| 名称 | 固定 LaunchAgent label |
|---|---|
| `harness` | `local.qwen.telegram` |
| `tunnel` | `local.qwen.mobile-tunnel` |

重启先写入 SQLite 持久请求，返回 `queued`；**这不代表已经重启**。独立 watchdog 至少延迟 3 秒再处理，给 harness 留出保存回执的时间。watchdog 再次验证账号与 session 授权，先 `bootout` 等待服务终止，再 `bootstrap`；不会执行任意 shell，也不修改 DNS、代理、VPN 或防火墙。

检查实际数据库路径后填写 `deploy/local.qwen.mobile-watchdog.plist.example` 中的 Python、数据库及 `WorkingDirectory` 绝对路径，复制到 `~/Library/LaunchAgents/local.qwen.mobile-watchdog.plist`，权限设为 `0600` 后，用相同的 `launchctl bootstrap` 命令注册该 plist。watchdog 必须和 harness 使用同一个数据库，并且数据库已经由 harness 或管理 CLI 初始化。

以下命令会实际处理已排队的维护请求，**不是只读检查**。仅在确认这些重启请求应执行，且另一个 watchdog 未运行时手工测试：

```sh
.venv/bin/python -m local_ai.maintenance --database /absolute/path/to/bot.sqlite3 --once
```

默认只处理明确提交的维护请求。需要断网时有限自恢复，再在 watchdog 参数中加入 `--auto-recover`。连续 3 次健康探测失败才重启 harness；每个连续故障最多尝试 3 次，重试退避且次数持久保存，恢复健康才重置计数。watchdog 自身中断时，已经开始的重启被标记 `uncertain`，不会盲目重放。

可选 `--gateway-health https://...` 用于检测通过隧道访问的真实后端健康状态；Caddy 默认封锁公网 `/healthz`，因此只有部署了单独限制来源的健康探测地址后才配置此项。不要把 Caddy 自己固定返回 200 的页面当作隧道健康证明。未配置时隧道仅响应明确重启请求。

这一版不执行网络设置变更，因此无需设置回滚。未来若增加 DNS / 代理等修复，应先加入配置快照、校验和回滚；物理断线、断电、Mac 休眠且无运行服务时无法靠手机即时修复。

## 4. APNs（仅原生 App）

本节不适用于 PWA。网页 Web Push 尚未接入；PWA 在页面打开后同步任务结果，不能使用原生 APNs 设备 token 代替网页推送订阅。

启用 Apple Developer App ID 的 Push Notifications，给 iPhone 工程配置相同 Bundle ID、签名团队及正确的 `aps-environment` entitlement。Mac 保存 Apple 的 `.p8` 私钥，权限 `0600`，填写：

```dotenv
APNS_TEAM_ID=ABCDEFGHIJ
APNS_KEY_ID=KLMNOPQRST
APNS_KEY_FILE=/absolute/private/path/AuthKey_KLMNOPQRST.p8
APNS_BUNDLE_ID=your.bundle.identifier
```

手机使用 `POST /v1/devices/{did}/push` 发送 `{"token":"十六进制设备token","environment":"sandbox"}` 注册当前登录设备；分发签名改用 `production`。注意签名 entitlement 的 Debug 值叫 `development`，对应 HTTP 注册值 `sandbox`；Release 两者均为 `production`。工程内的 `APS_ENVIRONMENT` 与 `HARNESS_PUSH_ENVIRONMENT` 已分别设置这组对应关系，仍需有效的 Apple 签名配置。APNs 采用 `httpx` HTTP/2 与 PyJWT/cryptography 的 ES256 签名，不自行实现加密。依赖随项目安装。

后台 worker 读取持久 session 事件，仅对任务完成、部分完成、失败、需要输入和待审批事件排队推送；等待子任务及继续中的任务不声称已经完成。发送前再次检查设备未撤销、令牌未过期、账号启用和 session 归属。推送只包含通用提示及 session/job/event ID，不包含转录、文档正文或任务标题；点击后通过认证 API 同步真实结果。APNs 接受请求记录在独立通知队列中；任务结果以任务数据库为准，客户端是否已同步以 App 的同步游标为准。

临时失败最多退避重试 5 次，重试保留相同通知 ID；失效 token 的 HTTP 410 回执移除注册。未配置 APNs 时保留 App 内事件同步。APNs 是尽力送达服务，不能代替任务持久化和恢复。

参考：[APNs 连接](https://developer.apple.com/documentation/usernotifications/establishing-a-connection-to-apns)、[通知请求](https://developer.apple.com/documentation/usernotifications/sending-notification-requests-to-apns)。

## 5. 上线前验收

自动化测试覆盖一次性 Picker 票据、选择后的 Google 权限验证、原子合并及配置冲突、连接器跨账号/session 拒绝、资源撤销、配置修订冲突、PKCE state 单次使用、令牌刷新、创建归属、外部写入失败不重试、维护权限与有限恢复、推送撤销设备与持久队列。测试使用替身提供方，不会操作真实文档或重启服务。

本次交付尚未配置或上线：VPS、DNS、公网/隧道证书、frp token、Google Cloud OAuth/Picker 项目、Apple 开发者签名及 APNs key。模板中的示例域名、`REPLACE_*` 和 `/ABSOLUTE/PATH` 都必须由真实配置替换；仓库不包含这些凭据。当前机器检查时未找到 Caddy、frpc、frps 可执行文件，没有安装或启动它们。TOML/plist 只做过解析校验，未执行 Caddy/frp 自带校验或真实隧道握手。

以下依赖外部账号和真实设备，必须另做验收：Google OAuth/Picker 页面及实际文档授权、Keychain 解锁、VPS TLS/frp 配置、watchdog 实际服务重启和有限恢复、Apple 签名及 APNs 实收、iPhone Wi-Fi/蜂窝/VPN 下语音锁屏/插话/耳机/来电/重连。iPhone 工程需要完整 Xcode 与 iPhone SDK；具体构建入口及真机限制见 `ios/README.md`。

语音指标仍是待测目标：简单问答说完到真机出声中位数 ≤3 秒、P95 ≤5 秒；插话停播 ≤300 毫秒。Python 测试、Swift 语法/契约检查或模板解析通过，都不能证明真机性能，也不代表这些链路已上线。
