# 私有 PWA 与通行密钥

PWA 与原生 iPhone 工程共用一个 Harness、数据库、session 和任务队列。默认部署使用通行密钥登录，不开放注册、密码登录或原生配对。网页登录页可以从公网访问，业务页面、脚本、历史、文件和语音必须认证。它是有身份门禁的公网服务，不是“公网完全不可达”的私网。

## 启用与首次注册

个人入口现已采用 [Tailscale Funnel](pwa-funnel.md)；自建网关可参考 [手机网关部署](mobile-deployment.md)。选择一个长期固定的 HTTPS 域名。通行密钥绑定域名和 origin；更换域名后须在 Mac 重新生成注册码并添加密钥，不能静默迁移。

```dotenv
MOBILE_ENABLED=1
MOBILE_PUBLIC_URL=https://harness.example.com
PWA_ENABLED=1
MOBILE_NATIVE_ENABLED=0
MOBILE_NATIVE_PAIRING=0
```

安装锁定依赖 `uv sync --locked`。首次创建通行密钥表前会生成 SQLite 一致性备份；保持 `runtime` 目录仅当前宿主用户可访问。随后重启唯一的现有 Harness 服务使配置生效，不要并行启动第二个 GPU 服务。

在 Mac 的终端运行以下命令；真实注册码仅交给本人，不发给模型，不放在网址、日志或截图中。`--database` 如需指定必须放在子命令前。

```sh
.venv/bin/python scripts/mobile_admin.py accounts
.venv/bin/python scripts/mobile_admin.py passkey-invite --account YOUR_ACCOUNT_ID
```

已有 Telegram 账号也可以使用 `passkey-invite --telegram-user YOUR_TELEGRAM_USER_ID`。在 iPhone Safari 打开固定域名的 `/login#setup` 首次设置页，输入注册码和密钥名称，通过系统 Face ID、Touch ID 或设备验证添加通行密钥。注册码 10 分钟有效，在开始注册时即消费；取消或失败后在 Mac 重新生成。没有邀请码就不能添加任何账号或密钥。

以后点击“使用通行密钥登录”，不需要重复邀请码。可将网站添加到主屏幕。通行密钥可能经 iCloud 钥匙串同步，因此身份验证不等同于某一台物理 iPhone 的硬件绑定。每次网页登录建立独立、可撤销的浏览器登录记录。

本机开发只允许显式 `MOBILE_PUBLIC_URL=http://localhost:PORT`。它使用不同 Cookie 名称，不能用于手机公网部署；IP 地址、任意 HTTP 域名、请求头或代理转发头都不能替代配置的 origin。

## 身份与数据边界

- 由 `webauthn` 库验证签名、RP ID、精确 origin、挑战、用户在场和用户验证。服务器只保存公钥与随机用户句柄；私钥、Face ID 生物特征不上传。
- 挑战 5 分钟有效，绑定服务器生成的浏览器流程 Cookie，SQLite 原子消费；重放、错来源、跨站 iframe、错账号和过期请求拒绝。登录 options 不暴露账号或密钥列表。
- 生产登录 Cookie 使用 `__Host-`、`Secure`、`HttpOnly`、`SameSite=Strict`，无 Domain 属性，Path=/。会话绝对有效期 7 天，闲置 12 小时失效；到期需要再次验证。登录 Cookie 不能当作原生 Bearer 凭据使用。
- 写请求必须有精确同源 Origin 和匹配的 CSRF token；WebSocket 升级验证 Cookie、Origin 和设备归属。长期令牌不进入 URL、localStorage 或 IndexedDB。
- 每次请求校验账号、浏览器登录、设备和通行密钥的撤销/过期状态。上传完成及任务入队前重新检查；语音收发和转录入队前也检查，关闭音频连接不取消已接收的持久任务。
- 业务数据和私有页面发送 `no-store`。Service Worker 仅缓存无敏感内容的图标、公共样式和离线提示；页面退出/身份失效清理内存中的消息、草稿与音频。退出尝试清理站点数据，已由用户主动下载到“文件”的资料不能远程收回。
- 主界面不加载第三方脚本，启用限制性 CSP、防嵌入和同源资源策略；模型回复以文字呈现，不执行 HTML。Google Picker 是明确例外，使用其独立策略加载 Google UI，短期票据绑定设备，设备撤销后不能继续使用。
- 凭据撤销属于本机管理；网页登录不能自行签发注册码或新增密钥。原有账号/session/技能快照/文件范围/审批授权仍由后端检查，登录不自动授予任意 Mac 命令权限。

默认 PWA 模式拒绝原生 Bearer 访问和 `/v1/pair`。如确实继续使用原生 App，显式设 `MOBILE_NATIVE_ENABLED=1`；仅需要新原生设备配对时再设 `MOBILE_NATIVE_PAIRING=1`。Telegram 使用独立入口，不受这些开关影响。

## 丢失设备与恢复

在 PWA 设置中可撤销某次浏览器登录。若密钥或同步账号可能泄露，从 Mac 撤销该通行密钥，其所有关联登录都会失效：

```sh
.venv/bin/python scripts/mobile_admin.py passkeys --account YOUR_ACCOUNT_ID
.venv/bin/python scripts/mobile_admin.py revoke-passkey -- YOUR_ACCOUNT_ID CREDENTIAL_ID
.venv/bin/python scripts/mobile_admin.py revoke-device -- YOUR_ACCOUNT_ID DEVICE_ID
```

没有“邮件找回”或公共恢复入口。丢失全部通行密钥时，通过可信 Mac 重新签发一次性注册码。攻击者若已取得宿主 Mac 的当前用户权限，便超出了网页认证的保护边界；Mac 与 TLS 终止的 VPS 都属于受信任宿主。

## 验证与限制

```sh
.venv/bin/python -m pytest tests/test_passkeys.py tests/test_browser_api.py tests/test_pwa_security_edges.py tests/test_mobile_voice.py -q
node --test tests/pwa_client.test.mjs tests/pwa_voice.test.mjs
```

测试使用临时数据库和软件 P-256 authenticator，经真实库验签，不连接 Apple/Google，不加载 GPU。HTTP 测试覆盖匿名访问、登录 CSRF、跨站写入、WebSocket 来源、重放、撤销、慢上传、过期、账号隔离等。它们不代替 iPhone Safari 的 Face ID、后台音频、VPN、耳机和断线恢复验收。

本次实现没有购买域名、部署 VPS、修改真实 `.env`、重启现有服务或向生产数据库签发邀请。PWA 现支持前台会话、任务、文件、审批、资源绑定、实时语音和支持 MP4 录制浏览器的语音留言；网页推送尚未接入，结果在打开页面后同步，Telegram 入口仍可用。原生 APNs 配置不等于网页 Web Push。

参考：[WebAuthn 库注册](https://duo-labs.github.io/py_webauthn/registration.html)、[认证](https://duo-labs.github.io/py_webauthn/authentication.html)、[Apple 通行密钥](https://developer.apple.com/passkeys/)、[Cookie 安全](https://developer.mozilla.org/en-US/docs/Web/Security/Practical_implementation_guides/Cookies)。
