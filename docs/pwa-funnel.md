# 个人手机入口：Tailscale Funnel

示例地址：`https://your-mac.your-tailnet.ts.net/login`（替换成你自己的 Tailscale Funnel 域名）。
手机用 Safari 访问；手机端无需开启 Tailscale VPN，可继续使用现有代理。
登录页可从公网访问，业务接口由邀请注册的通行密钥、登录 Cookie、Origin 与 CSRF 校验保护。

## 本机结构

- Homebrew 安装 `tailscale` 命令行版。
- `local.qwen.mobile-tunnel` 是当前用户的 LaunchAgent，运行专用 `tailscaled`。
- 使用 `--tun=userspace-networking`，不接管系统 DNS、路由或现有 VPN。
- 状态、证书和套接字保存在 `~/Library/Application Support/QwenHarness/Tailscale/`，目录权限 `0700`。
- 守护进程禁用日志上传；本地日志为该目录中的 `daemon.log`。
- Funnel 只代理 `127.0.0.1:8765`。模型接口与终端没有配置公网转发。
- 手机 API 与 Telegram 共用 `local.qwen.telegram` 进程，不能另起第二个 Harness。

`.env` 已配置六项：`MOBILE_ENABLED=1`、`MOBILE_PORT=8765`、
`MOBILE_PUBLIC_URL=https://your-mac.your-tailnet.ts.net`、`PWA_ENABLED=1`、
`MOBILE_NATIVE_ENABLED=0`、`MOBILE_NATIVE_PAIRING=0`。
更换 origin 后，旧域名的通行密钥不能直接迁移。

## 检查与关闭

```sh
tailscale --socket="$HOME/Library/Application Support/QwenHarness/Tailscale/tailscaled.sock" status
tailscale --socket="$HOME/Library/Application Support/QwenHarness/Tailscale/tailscaled.sock" funnel status
curl --fail https://your-mac.your-tailnet.ts.net/login --output /dev/null
```

需要关闭公网入口时，仅关闭该 Funnel：

```sh
tailscale --socket="$HOME/Library/Application Support/QwenHarness/Tailscale/tailscaled.sock" funnel --https=443 off
```

重新启用：

```sh
tailscale --socket="$HOME/Library/Application Support/QwenHarness/Tailscale/tailscaled.sock" funnel --bg 8765
```

`--bg` 的代理配置持久保存；LaunchAgent 在该 Mac 用户登录后运行。
它不能保障断电、完全断网或退出 macOS 用户后仍可访问。
Funnel 的带宽和服务可用性由 Tailscale 提供；手机语音表现仍需真机测试。

## TLS 安全连接失败排查

Friday 新建会话出现“暂时未完成 / TLS 错误导致安全连接失败”时，先在同一手机网络打开上述 `/login` 页面。若同样失败，先检查入口和网络出口。

2026-09-28 实测：本机 `127.0.0.1:8765` 正常，Funnel 证书有效期至 2026-12-26。当前 `X51 日本 IPLC专线 x1.8` 出口在 TLS 握手阶段断开，指定公网 IP 也失败；通过 `HK 00 BGP x1.0` 出口，使用 curl 默认的证书校验访问 `/login` 返回 200，未携带凭据的 `/v1/sessions` 返回预期的 401。重启隧道及重新启用 Funnel 未消除日本出口故障。这是当时的网络测试结果，不保证节点长期可用；手机 App 的复测仍需解锁设备并切换出口。

遇到相同情况，可先切换手机代理节点，再重新打开 Friday 创建会话。若使用域名分流，只调整 `your-mac.your-tailnet.ts.net` 的出口并复测。保留原服务域名和 HTTPS 证书校验，不需要退出账号或修改通行密钥；不要将动态的 Funnel 公网 IP 固定为 App 服务地址。

## 首次注册与验证

复用原 Telegram 所有者账号，通过 `scripts/mobile_admin.py passkey-invite` 生成一次性注册码。
注册码仅通过 Mac 本地文件或剪贴板交给本人，不写入网址、聊天或日志；开始注册后即消费，10 分钟过期。
手机直接打开 `https://your-mac.your-tailnet.ts.net/login#setup` 的首次设置页，或在登录页选择“首次使用 · 创建通行密钥”，输入注册码和密钥名称，再完成系统身份验证。首次设置页不显示已有密钥登录按钮，避免在尚未创建密钥时误入跨设备扫码登录。
注册成功后删除本地暂存注册码文件。

启用前已备份数据库与 `.env` 至 `runtime/deployment-backups/20260927-135327/`。
首次通行密钥迁移还会生成独立 SQLite 快照。
本地与公网均验证了登录页 200、未登录 `/app` 跳转、业务接口及私有脚本 401、原生配对 404、跨来源认证请求 403。
真机注册、首次对话、语音与主屏幕安装需由手机完成后继续验收。

参考：[Funnel 文档](https://tailscale.com/docs/features/tailscale-funnel)。
