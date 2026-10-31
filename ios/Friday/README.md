# Friday · 私人 iPhone 客户端

独立的 **Objective-C + UIKit** 工程：`Friday.xcodeproj`，scheme 为 `Friday`，最低 iOS 17。App、音频层、通知内容扩展均为 Objective-C；没有 Swift、WebView 外壳或第三方依赖。原 `ios/Harness` SwiftUI 工程保留作参考，不参与 Friday 构建。

## 界面与会话

- 会话、任务、设置使用原生导航；对话页隐藏底部标签栏。
- 单排输入框通过 `UIKeyboardLayoutGuide` 跟随键盘，使用系统安全区，不读取屏幕像素高度。输入最多展开至 132pt，正文独立滚动。
- 用户消息为紧凑气泡，助手回复支持选择与复制；系统字号缩放用于正文。原生图像预览及 AVPlayer 视频/音频控件带加载与重试入口，文件支持 Quick Look。
- 会话新建、重命名、归档/恢复、技能选择、连接器绑定、Google Picker、文件导入、显式选择对话/文件派生新会话、审批、取消及 owner-only Mac 诊断。
- 每个聊天控制器持有固定 session ID；草稿与重试 ID 按 session 保存，切换页面不改变发送或语音的目的地。技能与资源配置提交修订号，409 冲突要求重新加载。
- 登录使用系统 `ASWebAuthenticationSession` 打开现有通行密钥页面。成功后用户明确连接设备，以一次性 PKCE 回调换取设备凭据并存入 Keychain。凭据不放 URL、不放 UserDefaults、不随跨域重定向发送。
- 浏览器桥接只对管理员开放。Mac 配置 `MOBILE_NATIVE_ENABLED=1`，保留 `MOBILE_NATIVE_PAIRING=0`；旧邀请码配对接口保持关闭。

## 语音

实时通话使用 AVAudioEngine、系统 voice processing、16kHz 单声道 PCM16 上行、服务端协商下行格式。通话绑定开始时的 session；媒体包按连接 generation、session、epoch 丢弃迟到数据。上传与播放均有有界缓冲；手动插话立即清空旧播报。

界面将停止播报、结束通话与停止任务分开。通话开启后可通过 audio background mode 在锁屏继续；来电、旧音频设备断开、权限失败或网络拥塞停止采音。结束通话不取消 Mac 上的持久任务。

语音留言显式开始、停止、发送，AAC/M4A，最长 180 秒、20 MB；与实时通话互斥。录音草稿固定目的会话与请求 ID，发送失败可重试；关闭面板、录音中进入后台或中断会清理录音。

## 推送与卡片

`FridayNotification` 是随 App 嵌入的 **原生通知内容扩展**。锁屏/横幅的外层由 iOS 控制；长按展开使用自定义紧凑卡片。类别为：

| 类别 | 状态 | 操作 |
|---|---|---|
| FRIDAY_RESULT | 已完成 | 查看会话、补充要求 |
| FRIDAY_INPUT | 需要补充 / 部分完成 | 查看会话、补充要求 |
| FRIDAY_APPROVAL | 等待确认 | 解锁并查看原始请求 |
| FRIDAY_STOPPED | 失败 / 已停止 | 查看会话、补充要求 |

- 推送仅包含通用状态文案与内部定位 ID，不传对话正文、任务标题、文件或连接器凭据。
- 使用 thread identifier 按 session 分组；同一任务使用稳定 collapse ID，尚未发送的旧状态被新状态替换。普通运行进度不连续推送。
- 点通知返回原 session，账号不匹配时不处理；审批必须解锁后进入 App。通知不会直接批准操作。
- 通知内文字回复需要解锁，持久化到排除备份、`NSFileProtectionComplete` 保护的本地待发箱，然后使用稳定请求 ID 提交。断线提示已保留，重新打开 App 后重试；退出登录清理待发箱。
- 设备令牌注册、APNs sandbox/production 区分、过期令牌处理、服务端持久 outbox、有限重试已经接入。
- 设置会区分 Mac 未配置 APNs 与手机未注册。系统通知权限、专注模式、通知摘要及网络会影响展示和及时性；不请求 Critical Alerts 或绕过静音。

## 会员申请期间的本地测试

`Friday Local` scheme 使用 `Local` 构建配置，连接同一个真实 Mac 服务，移除远程推送 entitlement 并停止 APNs 注册；没有演示启动参数。可在 Xcode 中选择 Personal Team 进行基础真机测试。完整推送仍使用 `Friday` scheme，等待付费会员生效及 APNs 配置完成。

1. 用数据线连接 iPhone 与 Mac，手机解锁并选择「信任」。
2. 打开本目录 `Friday.xcodeproj`；Xcode → Settings → Apple Accounts 登录自己的 Apple 账号。
3. 选择 scheme **Friday Local**；在项目 Build Settings 的 **Local** 配置设置 `DEVELOPMENT_TEAM`，App 和通知扩展会继承；或在两个 target 的 Signing & Capabilities 中选择同一 Personal Team。若 Bundle ID 已被占用，将项目 `FRIDAY_BUNDLE_ID` 改为自己的唯一值。
4. 选择连接的 iPhone 为运行设备，根据 Xcode 提示在 iPhone「设置 → 隐私与安全性」开启 Developer Mode，点击 Run 安装。
5. Friday 登录页保留 `https://your-mac.your-tailnet.ts.net`，使用已有通行密钥登录，再确认连接此设备。当前入口使用 Tailscale Funnel，手机无需开启 Tailscale VPN；网络排查见 [手机入口说明](../../docs/pwa-funnel.md)。

此方式通过 Xcode 安装，不需要 App Store。根据 [Apple 的 Personal Team 说明](https://developer.apple.com/help/account/basics/about-your-developer-account)，免费签名的描述文件通常 7 天到期，需要重新构建安装。这里已验证模拟器及未签名真机构建；实际 Personal Team 签名和实体手机安装尚待连接设备后验证。

Mac 上可先选 `Harness Validation` 模拟器运行；不传 `--friday-demo` 就进入真实登录。模拟器未持有你的通行密钥时，系统登录可能要求用 iPhone 完成验证。界面演示模式和真实登录模式使用同一套原生界面，演示模式不会连接后端。

## 自用安装，不上架

1. Apple Developer 会员生效后，在 Xcode 的 Apple Accounts 中登录你的账号。
2. 打开 `Friday.xcodeproj`。在 **Project → Build Settings** 设置 `DEVELOPMENT_TEAM`；将 `FRIDAY_BUNDLE_ID` 改成你团队内唯一的 ID，例如自选的 `com.yourname.Friday`。App 与通知扩展继承同一个 Team，扩展自动使用 `.notifications` 后缀。
3. 主 App 保持 Automatic Signing 与 Push Notifications entitlement；连接并信任 iPhone，开启 Developer Mode，选择手机后运行 `Friday` scheme。
4. Debug 使用 development entitlement 和 APNs sandbox；Release 使用 production。服务端 `APNS_BUNDLE_ID` 必须等于主 App ID，不是扩展 ID。
5. 登录 Friday 后允许系统通知。在 iPhone 设置中选择锁屏、通知中心、横幅及预览偏好。

只安装到自己的注册设备即可。无需 App Store Connect 上架或 App Review。签名和开发者会员仍需要按 Apple 规则续期，不能保证永久不维护。

如需导出自用安装包：Xcode Archive → Distribute App → 注册设备的 Release Testing / Ad Hoc 方式；不要选择上传 App Store。当前 Xcode 27 的命令行导出 method 为 `release-testing`，destination 为 `export`。

## Mac 的 APNs 配置

在 Apple Developer 创建 APNs authentication key，下载 `.p8` 后仅保存在 Mac 的私有目录，文件权限 `600`。**不要把私钥内容贴到聊天或提交到 Git。** 在本机 `.env` 配置：

```
APNS_TEAM_ID=<Apple Team ID>
APNS_KEY_ID=<APNs Key ID>
APNS_KEY_FILE=<Mac 上 .p8 的绝对路径>
APNS_BUNDLE_ID=<主 App 的 Bundle ID>
```

重启 `local.qwen.telegram` 服务。凭据留在 Mac，不打包到 App，也不进入网关。通知通道为 Mac → Apple APNs → iPhone，点开后 App 仍通过现有加密服务地址访问 Mac。

## 验证与边界

执行 `./ios/Friday/check.sh`：Objective-C Foundation 契约测试、完整模拟器构建、未签名 Release 真机构建。服务端测试包括真实 HTTP + 软件 WebAuthn 认证、PKCE、过期/重复兑换、CSRF、撤销、账号隔离与通知合并。

模拟器演示仅在 **Debug + Simulator** 编译条件下可用：启动参数 `--friday-demo --friday-chat` 或 `--friday-demo --friday-card`。演示不会登录真实账号或发送模型任务；Release 与真机构建无法启用该入口。

已进行模拟器启动和原生页面/通知卡片内容的截图检查。**未完成**付费 Team 真机签名、真实 APNs 到达/锁屏长按、通知内回复、真机键盘与耳机/来电/锁屏语音验收。通知卡片预览不是实际 APNs 投递证明；这些需要签名后的 iPhone 与有效 Apple 推送密钥。

## 当前预览

以下均为模拟器中的演示数据，不代表真机 APNs 投递：

- [原生消息页](Previews/chat-simulator.png)
- [通知内容卡片](Previews/notification-card-simulator.png)

本次验证：23 项 Objective-C 契约检查、Debug 模拟器构建、未签名 Release 真机构建通过；后端完整套件 512 项通过，最后认证与推送相关改动再次通过 29 项检查。

## 能力与升级

管理会话设置中新增「能力与升级」：提出新能力、查看完整草案与验证记录、确认具体版本、选择本会话的 MCP，以及生成历史版本恢复草案。核心发布由 Mac 的独立升级器执行，App 不运行后端代码。详见 [升级机制](../../docs/self-evolution.md)。Debug 模拟器可用 `--friday-demo --friday-upgrades` 查看演示入口。

### 原生加载与列表 UI（2026-09-27）

- 会话/任务和异步子页面使用统一骨架屏；已有内容刷新时保留。错误退出骨架状态并提供重试，成功的空结果有独立空状态。
- 对话历史、等待回复、媒体预览使用对应占位；登录、发送、文件上传/下载和审批有局部忙碌提示。骨架动画遵循“减弱动态效果”，离屏/后台停止。
- 设置和会话菜单为单行，SF Symbols 图标 16pt；任务保留状态与所属会话，并支持全部、进行中、已结束筛选。
- 模拟器演示可组合 `--friday-demo --friday-slow` / `--friday-offline` 和 `--friday-chat` / `--friday-settings` / `--friday-tasks` / `--friday-upgrades`。演示分支只在 Debug + Simulator 生效；真机始终使用真实接口。
- `check.sh`：35 项 Objective-C 合约检查，覆盖初次加载、失败恢复、刷新保留数据、空结果与任务状态筛选；模拟器和设备 Release 构建通过。`Previews` 下的加载和状态截图为模拟器演示数据。

### 会话与媒体缓存（2026-09-27）

- `FRCache` 按服务地址、账号与登录设备隔离，使用排除备份的 Library/Caches 和 `NSFileProtectionComplete`；退出登录删除本机缓存。会话只保存消息快照、同步版本及阅读位置，不保存审批、令牌或语音状态。
- 打开会话先恢复消息快照，后台核对；服务端 `/messages?revision=…` 在内容未变时返回 `unchanged`，依旧先执行认证与归属校验。旧服务端可继续返回完整消息，客户端兼容。
- 任务产物按资源路径和服务端文件版本缓存，同一资源并发下载合并；普通可变会话文件仍每次下载。成功媒体跨页面和重启复用，失败可重试。媒体连接等待上限 120 秒，失败展示 HTTP/系统错误，重试立即显示进度指示。
- 磁盘缓存目标上限 256 MiB / 160 项；单个较大文件保留供当前预览，淘汰更旧条目。操作系统可回收缓存，Mac 上的原始资料不受影响。
- 已通过 41 项 Objective-C 合约检查、50 项相关后端测试、Debug 模拟器和 Release 设备构建。模拟器断网冷启动已验证恢复缓存对话。Personal Team 的 Local 版本已签名并覆盖安装到实体 iPhone；三张真实图片在手机端下载和解码成功，首次下载为 19–21 秒。轻量预览新版已签名并覆盖安装；设备再次自动锁屏，缩略图真机耗时验收待解锁。
- 调试构建可显式传 `--friday-media-diagnostic`，使用当前已登录账号下载最多三张图片预览，只记录成功状态、字节数、耗时、解码结果与错误，不记录令牌或对话内容。该入口不会创建任务或绕过授权；Release 不包含。

- 图片聊天预览为最长边 768px 的 JPEG，原生黑色背景下合成预览透明度，原图和 alpha 通道不变。点图片或文件名按需下载原图并打开 Quick Look，点击具有进度反馈和 44pt 最小触控区域。缩略图接口同样检查账号、session 与撤销状态，采用有界线程并发及 64 项内存缓存，不产生公开图片地址。

### 紧凑布局（2026-09-27）

- 会话、任务、设置及各子列表改用全宽列表，去掉 inset-grouped 卡片和内部留白叠加；左右 16pt，设置单行 44pt 起，随系统字号增加行高。正文 16pt，任务状态与全部/进行中/已结束筛选保持。
- 聊天正文左右 12pt，消息上下 8pt；输入区高 46pt 起，继续使用 keyboardLayoutGuide 和系统安全区。工具图标 18pt、发送 22pt，触控区域 44pt。
- 底部导航保留悬浮圆角胶囊和选中态，压缩到 44pt 高；18pt 图标与 12pt 文字同排，胶囊宽度最多 320pt，并适配窄屏。使用公开的 UIViewController 容器 API，保留每个 UINavigationController 的导航栈和系统安全区。进入聊天或其子页面隐藏底栏，返回会话列表恢复；不通过修改系统 tab bar 的内部 frame 强行压缩。
- 骨架屏匹配 44/56pt 列表和 112pt 对话单元，数量按可视高度计算，跟随正文边缘对齐；已缓存内容不重新显示骨架。
- 已通过模拟器构建、Local 真机签名构建；在 390pt、402pt 两种手机宽度检查设置、任务、聊天和加载页，并检查放大系统字号。截图位于 `Previews/compact-*`，均为模拟器演示数据。新版已覆盖安装到配对的 iPhone。

- Debug 模拟器可运行 `--friday-demo --friday-navigation-check`：14 项原生导航检查，在 390pt 和 402pt 手机模拟器分别通过。覆盖三个标签实际按钮动作、单一可见子控制器、44pt 按钮行、内容底部无重复 inset、进入聊天/子页面隐藏底栏、返回恢复、输入框位于键盘布局线之上。测试不会编入真机或 Release。当前紧凑悬浮胶囊截图：`Previews/compact-tabs-44.png`。

### 聊天组件与授权弹窗（2026-09-28）

- 修复首次开启 Mac 管理时加载状态重建按钮样式、丢失导航小字号的问题，并限制导航标题宽度。
- 授权使用独立原生弹窗和明确按钮；支持完整说明、单次允许/拒绝、稍后处理、失败重试和防重复提交。
- 操作、状态、图片、视频、音频、文件与加载占位移入独立 UIKit 组件库，生产聊天页已接入。组件接口、演示入口、截图与验证限制见 [组件库文档](COMPONENTS.md)。

### 原生 Markdown 回复（2026-09-28）

聊天回复和流式草稿接入 `FRMarkdownView`：支持常用 Markdown 排版，围栏代码与表格有独立横向滚动区域，提供复制代码/整条回复。解析及 UIKit 回归检查已通过，详情和截图见 [组件库文档](COMPONENTS.md#markdown-消息2026-09-28)。

### 参考 DeepSeek Harness 的聊天交互调整（2026-09-28）

消息尾部改为小图标和耗时行，任务详情可展开，权限入口去重，授权弹窗默认半屏，媒体改为预览配紧凑文件行。保留 44pt 触控和原有授权边界。源码参考及截图见 [设计调整记录](../../docs/deepseek-harness-ui-reference.md)。

## 短剧、记忆、定时任务（2026-10-20）

- 底部标签改为 4 个：会话、短剧、任务、设置。新页面在 `Sources/FRAgentViews.m`，共享的列表控制器声明在 `Sources/FRList.h`。
- 短剧：项目列表 → 导入剧本（粘贴文本）→ 项目页分“设定 / 分镜 / 成片”三段 → 镜头详情（播放与选用版本、通过草稿、重出、逐项编辑、删除）。项目页在有任务进行时每 5 秒刷新。
- 设置新增“记忆”和“定时任务”。Mac 端未开启新版 agent 时，这两页提示原因而不是空白。
- 任务与会话列表使用服务端返回的 `title`、`status_text`、`last`、`active`、`waiting`；运行中的任务显示进度清单（`todo`）。聊天页副标题只在 Mac 管理开启时出现。
- 服务端错误的 `{"error":{"message":…}}` 现在原样显示，不再统一显示“服务暂时无法连接”。

截图用的演示参数（仅 Debug 模拟器）：`--friday-real` 换成接近真实使用的数据（暂停、并入、进度清单、视频审批、短剧、记忆、定时任务）；`--friday-dramas` 打开短剧标签；`--friday-page-memory`、`--friday-page-schedules`、`--friday-page-drama`、`--friday-page-drama-shot`、`--friday-page-drama-bible` 直接打开对应二级页。
