# 安装与配置

[English](INSTALL.md) · **简体中文** · [日本語](INSTALL.ja.md)

Friday Harness 是一个在你自己 Mac 上运行的本地 AI 助手：聊天（Qwen 27B，llama.cpp）、生图与视频（ComfyUI）、中文语音（MLX），入口为 Telegram 机器人、私有 PWA 和 iPhone App。推理全部在本机进行，不调用云端模型。

> 给 AI 助手：照本文从上到下执行即可；需要人类提供的只有「配置」一节中标为 **必填** 的值。具体规则见仓库根目录 [AGENTS.md](../AGENTS.md)（英文）。

## 1. 系统要求

| 项目 | 要求 |
| --- | --- |
| 硬件 | Apple Silicon Mac（M1 及以上）。只聊天至少 32 GB 内存；要生图/视频建议 64 GB |
| 系统 | macOS 14 或更新；已安装 Xcode 命令行工具（`xcode-select --install`） |
| 包管理 | [Homebrew](https://brew.sh)，安装在默认的 `/opt/homebrew` |
| 磁盘 | 完整安装约 100 GB（模型约 70 GB + 语音 5.5 GB + 依赖）；只装聊天约 25 GB |
| 网络 | 首次安装需要访问 GitHub、Hugging Face、PyPI、npm；运行 Telegram 入口需要能连上 Telegram（可配代理） |

## 2. 一键安装

```sh
git clone https://github.com/SpectreMercury/friday.git
cd friday
./scripts/install.sh
```

脚本可重复执行，已完成的步骤会跳过，模型下载中断后重跑即可续传。它会：

1. 检查芯片、内存、Xcode 命令行工具和 Homebrew；
2. 补装缺少的 uv、ffmpeg、Node（≥22.19）、pnpm、git（已有的沿用，不重复安装）；
3. 用 `uv sync --frozen` 建立 `.venv`（Python 3.12，依赖锁定在 `uv.lock`）；
4. 下载 `sources.lock.json` 固定版本的 llama.cpp 预编译包到 `vendor/llama/`；
5. 安装 Pi Agent 依赖（`pi/pnpm-lock.yaml`），并把 `vendor/node` 链接到本机 Node；
6. 把 ComfyUI、h3-mac 和 4 个自定义节点检出到固定提交，建立 `vendor/ComfyUI/venv`（`requirements-comfy.lock`）；
7. 建立语音环境 `vendor/speech-venv`（`requirements-speech.lock`）；
8. 把 `assistant-skills/` 复制到技能库（默认 `~/.friday/skills`，不会覆盖已有修改）；
9. 若没有 `.env`，从 `.env.example` 复制一份并设为 600 权限；
10. 下载所有模型并逐个校验 SHA-256（`models.lock.json`、`speech-models.lock.json`）；
11. 自检：导入 Python 包并运行 `pi/runner-v2.mjs --check`。

可选参数：

| 参数 | 作用 |
| --- | --- |
| `--chat-only` | 不装 ComfyUI 和语音，只下载聊天模型（约 17 GB） |
| `--no-media` | 不装生图/视频 |
| `--no-speech` | 不装本地语音 |
| `--no-models` | 只装环境，稍后再运行 `.venv/bin/python scripts/download_ranges.py` 和 `scripts/download_speech.py` |

脚本不会启动服务，也不会改动 `.env` 中已有的值。

## 3. 配置（`.env`）

`.env` 只保存在本机，已被 `.gitignore` 忽略，权限必须是 600。**不要提交、粘贴或截图这个文件。**

### 3.1 Telegram 入口（最小可用配置）

| 变量 | 必填 | 说明 | 如何获取 |
| --- | --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | **必填** | 机器人令牌 | 在 Telegram 里找 @BotFather，发送 `/newbot` |
| `TELEGRAM_ADMIN_USER_ID` | **必填** | 管理员的数字用户 ID，可用 `/allow` `/deny` 管理朋友 | 在 Telegram 里给 @userinfobot 发消息 |
| `TELEGRAM_ALLOWED_USER_IDS` | 建议 | 逗号分隔的数字用户 ID；留空时拒绝所有非管理员 | 同上 |
| `TELEGRAM_PROXY` | 可选 | 本机访问 Telegram 的代理，如 `socks5://127.0.0.1:7890` | 你的代理软件 |
| `TTS_SPEAKER` | 可选 | 语音回复音色：`Serena`（默认）、`Vivian`、`Uncle_Fu` | — |
| `AGENT_V2` | 可选 | `1` = 进程内新版 agent（记忆、历史检索、定时任务）；`0` = Pi Agent | — |
| `FRIDAY_SKILLS_DIR` | 可选 | 技能库目录，默认 `~/.friday/skills` | — |

最小 `.env` 示例：

```dotenv
TELEGRAM_BOT_TOKEN=123456789:AA...你的令牌
TELEGRAM_ADMIN_USER_ID=123456789
TELEGRAM_ALLOWED_USER_IDS=123456789
AGENT_V2=1
```

### 3.2 手机入口：私有 PWA / iPhone App（可选）

手机 API 与 Telegram 共用同一进程，只监听 `127.0.0.1`。公网访问需要你自己提供 HTTPS 入口（[Tailscale Funnel](pwa-funnel.md) 或 [VPS + frp + Caddy](mobile-deployment.md)）。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MOBILE_ENABLED` | `0` | `1` 时随服务启动手机 API |
| `MOBILE_PORT` | `8765` | 本机监听端口 |
| `MOBILE_PUBLIC_URL` | — | 固定的公网 HTTPS 地址，通行密钥绑定这个 origin。本机试用可设 `http://localhost:8765` |
| `PWA_ENABLED` | `1` | 网页版（通行密钥登录） |
| `MOBILE_NATIVE_ENABLED` | `0` | 允许原生 iPhone App 连接 |
| `MOBILE_NATIVE_PAIRING` | `0` | 允许新设备配对 |

首次注册通行密钥（在 Mac 终端执行，注册码只交给本人）：

```sh
.venv/bin/python scripts/mobile_admin.py accounts
.venv/bin/python scripts/mobile_admin.py create-account 我的账号 --admin   # 没有账号时
.venv/bin/python scripts/mobile_admin.py passkey-invite --account <账号ID>
```

然后在手机 Safari 打开 `<MOBILE_PUBLIC_URL>/login#setup`。详见 [PWA 安全与配置](pwa-security.md)。

### 3.3 Google Drive / Docs（可选）

| 变量 | 说明 |
| --- | --- |
| `GOOGLE_CLIENT_ID`、`GOOGLE_CLIENT_SECRET` | Google Cloud Console 中的 Web OAuth 客户端 |
| `GOOGLE_REDIRECT_URI` | `<MOBILE_PUBLIC_URL>/v1/oauth/google/callback` |
| `GOOGLE_PICKER_API_KEY`、`GOOGLE_PICKER_APP_ID` | 启用 Google Picker API 后的 API key 与项目编号 |

刷新令牌保存在 macOS 钥匙串，不写入 `.env`。步骤见 [手机网关部署](mobile-deployment.md)。

### 3.4 iPhone 推送（可选，需付费 Apple Developer 账号）

| 变量 | 说明 |
| --- | --- |
| `APNS_TEAM_ID` | 你的 Apple 开发者 Team ID |
| `APNS_KEY_ID` | APNs 密钥 ID |
| `APNS_BUNDLE_ID` | 主 App 的 Bundle ID（与 Xcode 中 `FRIDAY_BUNDLE_ID` 一致） |
| `APNS_KEY_FILE` | `.p8` 密钥文件的绝对路径，放在仓库之外 |

iPhone App 的构建与签名见 [ios/README.md](../ios/README.md)：在 Xcode 中把 `DEVELOPMENT_TEAM` 设为你自己的 Team，`FRIDAY_BUNDLE_ID` 改成你自己的唯一 ID。

## 4. 启动

```sh
# 前台运行（Telegram + 可选手机 API），Ctrl-C 停止
.venv/bin/python -m local_ai.bot

# 不使用 Telegram，只跑手机/PWA 入口
.venv/bin/python -m local_ai.server

# 安装为当前用户的登录自启服务（launchd），并保持 Mac 不休眠
.venv/bin/python scripts/service.py install
.venv/bin/python scripts/service.py status   # stop / start 同理
```

同一时间只能运行一个服务实例（`runtime/service.lock`）。然后在 Telegram 向你的机器人发送 `/start`。

## 5. 验证

```sh
.venv/bin/python -m pytest -q                     # 单元测试，不需要模型
.venv/bin/python scripts/local_test.py chat       # 真实聊天推理（先停掉服务）
.venv/bin/python scripts/local_test.py image --smoke
.venv/bin/python scripts/local_test.py video --smoke
```

## 6. 隐私与数据

- 所有推理在本机完成；Telegram 入口的消息和生成结果会经过 Telegram 服务器。
- 本机数据都在 `runtime/`（聊天数据库 `bot.sqlite3`、日志、记忆 `runtime/memory/`、工作文件 `runtime/workspaces/`）和 `vendor/ComfyUI/output/`，均不进 Git。备份或分享机器前请自行处理。
- 机器人只接受私聊，授权名单为空时不对外开放。
- 管理员的电脑/终端工具在审批后以你的 macOS 账号权限运行，**不是沙箱**。不要把管理员身份给别人。
- 默认模型使用社区发布的「uncensored / abliterated」权重，内容过滤较弱；使用前请阅读各模型仓库的许可证，并自行承担使用责任。可以在 `scripts/download_models.py` 中把模型换成官方版本，然后删除 `models.lock.json` 重新生成锁文件。

## 7. 常见问题

| 现象 | 处理 |
| --- | --- |
| `请先在 .env 填写 TELEGRAM_BOT_TOKEN…` | 按 3.1 填写后重启 |
| Telegram 连接超时 | 设置 `TELEGRAM_PROXY` |
| `Harness 已运行` | 已有一个实例在运行：`scripts/service.py status` / `stop` |
| llama-server 无法打开（被 macOS 阻止） | `xattr -dr com.apple.quarantine vendor/llama` |
| 模型校验失败 | 删除对应文件和 `.verified.json`，重跑 `scripts/download_ranges.py` |
| 生图/视频内存不足 | 关闭其他大程序；或用 `--chat-only` 安装 |
