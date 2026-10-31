# Friday 桌面与网络工具

在 Friday 的目标会话设置中开启「Mac 管理」，管理员即可用自然语言调用。新会话、派生会话不自动继承该开关。每次执行检查账号、会话和任务状态；确认等待结束后再次检查授权。

## 安装与系统权限

在项目目录运行：

```sh
.venv/bin/python scripts/install_desktop.py
```

安装位置为 `~/Applications/Friday Desktop.app`，使用固定 bundle ID `com.personal.Friday.desktop` 和本机签名，无网络能力。需要 macOS 14 或更新版本和 Xcode 命令行编译工具。相同源码重复安装不重建；更新辅助程序后应重新检查系统权限。

`computer_permissions` 默认只查询。请求 `screen_recording` 或 `accessibility` 时，先通过 Friday 操作确认，再由 Mac 本人完成系统授权：

- 系统设置 → 隐私与安全性 → 屏幕与系统音频录制 → Friday Desktop。
- 辅助功能为可选授权，用于读取当前前台应用的可访问文字；未授权时仍可截图和 OCR。
- 后端通过 LaunchServices 启动辅助应用，使用 Friday Desktop 自己的授权身份，不直接作为 Python 子进程执行。授权后重新调用。不要修改 TCC 数据库或关闭系统保护。

开发终端有权限不代表 launchd 后台服务有权限。后台返回 `screen_permission_required` 时，明确告知此缺口，不声称已经看到屏幕，也不改用其他程序绕过。

## 屏幕与应用

| 工具 | 行为 |
|---|---|
| `computer_permissions` | 检查或请求 macOS 权限 |
| `computer_apps` | 最多150个已安装应用、60个运行应用；返回真实 bundle ID |
| `computer_open_app` | 确认后通过 NSWorkspace 打开应用，返回进程状态 |
| `computer_screen` | 截取指定显示器，默认1；返回最多8000字 OCR、有限前台辅助功能文字、截图文件地址 |

例如：“看看 Mac 当前屏幕上有什么错误”“打开计算器”。截图作为私有附件和会话文件保存，会在对应任务的聊天消息中展示预览，也可从手机「会话文件」预览；遵循原有会话归属和最多100个文件限制。临时截图用完删除，持久文件权限为0600。截屏包含当时可见内容，不会持续录屏。辅助功能读取跳过安全文本框，但整个屏幕上的可见敏感信息仍可能出现在截图中。

当前 Qwen 文本接口不能直接理解位图。报告须依据 OCR 与界面文字，注明截断、识别失败或辅助功能不可用；不能声称辨认所有图标、图表和布局。打开应用成功只表示系统已启动该进程，不代表应用内部操作完成。通用鼠标键盘及浏览器点击尚未提供。

## 网络诊断与修复

| 工具 | 行为 |
|---|---|
| `network_diagnose` | 有界 DNS 查询、无凭据 HEAD 请求、TLS校验和系统代理状态 |
| `network_proxy_options` | 查询已运行 Clash Verge 的代理组及候选节点 |
| `network_proxy_test` | 对指定节点测试指定网址，不切换代理 |
| `network_repair` | 确认后刷新用户可用的 DNS 缓存，或切换候选节点并复测 |
| `mac_restart_service` | 原有独立维护程序重启 harness/tunnel；queued 不表示重启完成 |

例如：“检查 Friday 服务地址为什么连不上，比较香港和日本节点”“测试这个节点，确认后切换并检查连接”。诊断 URL 只接受无用户名、密码、查询参数及片段的 HTTPS；HTTP 仅允许 localhost 回环。curl 禁用用户配置，不携带 Cookie/令牌、不跟随重定向、不跳过证书校验。401/403 可证明连接已经建立，不能证明业务请求成功。系统代理配置不包括全部 TUN 路由，Fake-IP 本身不说明 DNS 出错。

代理集成仅连接 `/tmp/verge/verge-mihomo.sock`，不读取订阅或节点密码。切换前查询原值并测试候选；确认文案列出代理组、原节点、新节点以及对其他应用的影响。确认后发现代理已被其他操作修改则停止。切换后复测系统实际请求；失败时仅在当前选择仍为本次节点时恢复原节点，避免覆盖用户的新设置。异常、恢复不确定和取消不允许盲目重放；先查询真实状态。跨订阅更新的持久性由代理应用控制。

这些工具操作 Mac 的代理，不能更改 iPhone 的代理。自动刷新不会改 DNS 服务器，也不会要求 sudo 或关闭 TLS 安全检查。更广泛的网络配置可使用原有逐条确认的 terminal 工具。

## 本机验证（2026-09-28）

已实测安装、应用发现（80个已安装应用）、启动计算器，以及经 LaunchServices 启动的 launchd/Python 后台截图与 OCR；屏幕权限已由本人开启。辅助功能权限未开启，因此辅助功能树读取尚未实测，截图与 OCR 不受此项缺失影响。真实 HTTPS 入口返回200并通过证书校验。代理切换与失败回退通过隔离测试验证，未为验收修改真实代理选择。

后端580项测试、Pi运行时37项测试通过。新版本已重启加载，并通过 `/healthz` 校验运行中的 build ID 与当前源码一致。

实现依据：[Apple NSWorkspace](https://developer.apple.com/documentation/appkit/nsworkspace)、[ScreenCaptureKit](https://developer.apple.com/documentation/screencapturekit)、[Mihomo 控制 API](https://wiki.metacubex.one/api/)。

## 会话权限、用时与消息刷新

Mac 的屏幕录制许可与 Friday 会话的「Mac 管理」是两层独立授权。聊天顶部显示会话权限状态；缺少会话权限时，工具发现返回 `mac_management_required`，运行时停止继续查询并交付具体操作提示，结果显示「需要操作」或「未完成」。开启入口仅向管理员显示，开启后需重新发送原请求，不会自动重放之前的电脑操作。

聊天与任务列表显示从提交到结束的总用时（含排队、确认等待和重试），执行中的计时在客户端更新。后端返回稳定的 `timing` 时间戳，不把当前时间加入消息版本；旧任务使用已有运行记录或结束事件，确实缺少记录则显示「用时未记录」。

流式草稿原地更新，其他变化只刷新对应消息；阅读旧消息时保持位置，截图加载也只刷新所属消息。导航验证覆盖键盘避让、后台更新保留阅读位置、草稿原地更新与缓存排除草稿；Debug/Release 构建和 Objective-C 合约检查通过。
