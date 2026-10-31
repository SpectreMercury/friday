<div align="center">

# Friday

**运行在 Apple Silicon Mac 上的私有、完全本地的 AI 助手。**
聊天、工具调用、生图与视频、语音——可通过 Telegram、通行密钥保护的 PWA 或原生 iPhone App 使用。

[![Python tests](https://github.com/SpectreMercury/friday/actions/workflows/python-tests.yml/badge.svg)](https://github.com/SpectreMercury/friday/actions/workflows/python-tests.yml)
[![Node tests](https://github.com/SpectreMercury/friday/actions/workflows/node-tests.yml/badge.svg)](https://github.com/SpectreMercury/friday/actions/workflows/node-tests.yml)
[![iOS checks](https://github.com/SpectreMercury/friday/actions/workflows/ios-checks.yml/badge.svg)](https://github.com/SpectreMercury/friday/actions/workflows/ios-checks.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/tag/SpectreMercury/friday?label=release&sort=semver)](https://github.com/SpectreMercury/friday/tags)

[![Platform](https://img.shields.io/badge/platform-macOS%2014%2B%20%C2%B7%20Apple%20Silicon-lightgrey?logo=apple)](docs/INSTALL.zh-CN.md#1-系统要求)
[![Python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![Node.js](https://img.shields.io/badge/node-%E2%89%A522.19-339933?logo=nodedotjs&logoColor=white)](pi/package.json)
[![llama.cpp](https://img.shields.io/badge/llama.cpp-b11173-8A2BE2)](sources.lock.json)
[![ComfyUI](https://img.shields.io/badge/ComfyUI-pinned-orange)](sources.lock.json)
[![MLX](https://img.shields.io/badge/MLX-speech-black)](requirements-speech.lock)

[English](README.md) · **简体中文** · [日本語](README.ja.md)

</div>

---

Friday 在你的 Mac 上完整运行一个 27B 语言模型，以及生图、视频和语音模型，不调用任何云端模型。仅有的网络流量来自你选择的消息渠道，以及你允许的工具（例如联网搜索）。

## 目录

- [功能](#功能)
- [架构](#架构)
- [系统要求](#系统要求)
- [快速开始](#快速开始)
- [配置](#配置)
- [使用](#使用)
- [目录结构](#目录结构)
- [开发与测试](#开发与测试)
- [安全与隐私](#安全与隐私)
- [许可证](#许可证)
- [致谢](#致谢)

## 功能

| 领域 | 内容 |
| --- | --- |
| **聊天 Agent** | Qwen3.8 27B（GGUF，Q4_K_M），运行在 llama.cpp 上，启用多 token 预测（MTP）投机解码；支持工具调用、计划、长期记忆、跨会话历史检索、定时任务和子任务委派 |
| **工具** | 联网搜索与网页读取、笔记与文件、精确计算器、附件解析（PDF、Word、Excel、OCR）、Google Drive/Docs、MCP 服务，以及需明确审批、仅管理员可用的电脑/终端工具 |
| **生图** | 通过 ComfyUI 运行 Qwen Image 2.1，默认 1024×1024，返回原始 PNG |
| **视频** | MiniMax H3 文生视频/图生视频，带音频，单段最长 15 秒；多镜头短剧项目，包括分镜、草稿、审片和成片导出 |
| **语音** | 本地中文语音识别与合成（MLX 上的 Qwen3-ASR / Qwen3-TTS），App 内支持语音留言和实时语音 |
| **客户端** | Telegram 机器人、通行密钥登录的私有 PWA、带推送通知的原生 iPhone App |
| **运维** | 所有客户端共用一个 GPU 队列；任务持久化，重启后可恢复；审批检查点；launchd 服务；依赖与模型版本固定并校验哈希 |

## 架构

```
 Telegram ─┐                        ┌─ llama.cpp ─── Qwen3.8 27B（聊天、工具）
 PWA ──────┼─► HarnessService ──────┼─ ComfyUI ───── Qwen Image 2.1 / MiniMax H3
 iPhone ───┘   · 会话与身份认证     └─ MLX ───────── Qwen3-ASR / Qwen3-TTS
               · 持久化任务队列
               · Agent 循环与工具          全部只监听 127.0.0.1
               · SQLite（runtime/）
```

一个 Python 进程（`local_ai`）负责身份、会话、任务队列和 GPU 调度。模型服务按需启动、只监听 `127.0.0.1`，闲置后自动卸载。设计文档见 [`docs/`](docs/)；早期的详细记录见 [开发笔记](docs/development-notes.md)。

## 系统要求

- Apple Silicon Mac（M1 及以上），macOS 14 或更新
- 只聊天需 32 GB 内存；生图和视频建议 64 GB
- 完整安装约需 100 GB 可用磁盘（只聊天约 25 GB）
- [Homebrew](https://brew.sh) 和 Xcode 命令行工具

## 快速开始

```sh
git clone https://github.com/SpectreMercury/friday.git
cd friday
./scripts/install.sh            # 完整安装；只要聊天用 --chat-only
```

然后在 `.env` 中填写 Telegram 机器人令牌和你的用户 ID，再启动服务：

```sh
.venv/bin/python scripts/service.py install
```

向你的机器人发送 `/start` 即可。包含所有选项的完整指南见 **[docs/INSTALL.zh-CN.md](docs/INSTALL.zh-CN.md)**。AI 编程助手可以按照 [AGENTS.md](AGENTS.md) 代为安装。

## 配置

所有设置都在 `.env` 中（由 [`.env.example`](.env.example) 生成，权限 600，不会提交到 Git）。

| 分组 | 变量 | 用途 |
| --- | --- | --- |
| Telegram | `TELEGRAM_BOT_TOKEN`、`TELEGRAM_ADMIN_USER_ID`、`TELEGRAM_ALLOWED_USER_IDS`、`TELEGRAM_PROXY` | 机器人（令牌和管理员 ID 必填） |
| Agent | `AGENT_V2`、`FRIDAY_SKILLS_DIR`、`TTS_SPEAKER` | Agent 版本、技能库、音色 |
| 手机 / PWA | `MOBILE_ENABLED`、`MOBILE_PORT`、`MOBILE_PUBLIC_URL`、`PWA_ENABLED`、`MOBILE_NATIVE_*` | 网页和 iPhone 访问 |
| Google | `GOOGLE_CLIENT_ID`、`GOOGLE_CLIENT_SECRET`、`GOOGLE_REDIRECT_URI`、`GOOGLE_PICKER_*` | Drive / Docs 工具 |
| 推送 | `APNS_TEAM_ID`、`APNS_KEY_ID`、`APNS_BUNDLE_ID`、`APNS_KEY_FILE` | iPhone 通知 |

每个变量的含义和获取方式见 [配置说明](docs/INSTALL.zh-CN.md#3-配置env)。

## 使用

直接发送普通消息即可聊天，Agent 会自行判断何时调用工具。命令：

| 命令 | 作用 |
| --- | --- |
| `/image <描述>` | 生成图片 |
| `/video <描述>` | 生成 15 秒带音频的竖屏视频；附带图片时用作首帧 |
| `/video_hd <描述>` | 更高分辨率视频（480×864） |
| `/status` | 模型、队列和任务状态 |
| `/cancel [任务号]` | 取消任务（默认取消最近一个待处理任务） |
| `/reset` | 清除你的聊天上下文（保留文件和任务记录） |
| `/allow <用户ID>` · `/deny <用户ID>` | 管理员：授权或撤销访问 |

## 目录结构

| 路径 | 内容 |
| --- | --- |
| `local_ai/` | 服务本体：Telegram 机器人、手机 API、Agent 循环、工具、任务存储、模型引擎、PWA 资源 |
| `pi/` | 连接 [Pi](https://github.com/earendil-works/pi) Agent 运行时的桥接层（Node） |
| `ios/` | 原生 iPhone App（`Friday`）与 SwiftUI 客户端（`Harness`） |
| `assistant-skills/` | 内置技能，安装时复制到用户技能库 |
| `scripts/` | 安装脚本、模型下载、服务管理、管理工具和基准测试 |
| `workflows/` | 生图与视频的 ComfyUI 工作流 |
| `tests/`、`evals/` | 单元测试与 Agent 评测用例 |
| `deploy/` | 网关配置示例（Caddy、frp、launchd） |
| `*.lock*` | 固定的依赖版本、上游源码版本和模型校验值 |

## 开发与测试

```sh
uv sync --frozen                                      # Python 环境
.venv/bin/python scripts/setup_pi.py --node "$(command -v node)" --pnpm "$(command -v pnpm)"
.venv/bin/python -m pytest -q                         # Python 单元测试
node --test tests/*.test.mjs pi/*.test.mjs            # PWA 与 Pi 桥接测试
./ios/check.sh                                        # iOS 契约检查
./ios/Friday/check.sh                                 # 完整构建 iPhone App（需要 Xcode）
```

测试使用临时数据和模型替身，不需要下载模型；每次推送都会在 CI 中运行同样的测试。如需在自己的 Mac 上比较不同聊天后端，见 `scripts/benchmark_chat_backends.py`。

## 安全与隐私

- 推理只在本机进行，所有模型服务只监听 `127.0.0.1`。
- 机器人只接受私聊；授权名单为空时拒绝所有人。
- PWA 使用绑定固定 origin 的通行密钥，不开放注册，有 CSRF 防护和严格的 Cookie 策略，详见 [PWA 安全](docs/pwa-security.md)。
- 电脑和终端工具需要管理员审批，并以你的 macOS 账号权限运行。**它们不是沙箱。**
- 聊天、记忆、文件和日志都保存在本机 `runtime/` 中。通过 Telegram 发送的消息会经过 Telegram 服务器。
- 不收集任何遥测数据。

发现安全漏洞请通过 [GitHub 安全公告](https://github.com/SpectreMercury/friday/security/advisories/new) 私下报告，不要提交公开 issue。

## 许可证

Friday 的源代码以 **[MIT 许可证](LICENSE)** 发布。

安装脚本下载的第三方软件和模型权重**不**受本许可证约束，各自按其许可条款使用：

| 组件 | 许可证 |
| --- | --- |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) | MIT |
| [ComfyUI](https://github.com/Comfy-Org/ComfyUI) | GPL-3.0 |
| [ComfyUI-GGUF](https://github.com/leejet/ComfyUI-GGUF) | Apache-2.0 |
| [ComfyUI-AppleSilicon-FP8](https://github.com/pawel-mazurkiewicz/ComfyUI-AppleSilicon-FP8)、[ComfyUI-PlagueKind-Nodes](https://github.com/PlagueKind/ComfyUI-PlagueKind-Nodes) | MIT |
| [comfyui-obvpm](https://github.com/obvpm/comfyui-obvpm) | GPL-3.0 |
| [minimax-h3-mac](https://github.com/Bambushu/minimax-h3-mac) 工作流 | MIT |
| [MLX](https://github.com/ml-explore/mlx)、[mlx-audio](https://github.com/Blaizzy/mlx-audio)、[Pi](https://github.com/earendil-works/pi) | MIT |

| 模型 | 许可证 |
| --- | --- |
| Qwen3.8 27B（AEON 微调版，GGUF） | Apache-2.0 |
| Qwen Image 2.1（量化微调版） | Qwen Research License——**禁止商用** |
| MiniMax H3（视频及 VAE） | MiniMax H3 Community License |
| MiniMax H3 文本编码器（Qwen3-VL 32B，GGUF） | Apache-2.0 |
| H3 LoRA（PlagueKind） | MIT |
| Qwen3-ASR / Qwen3-TTS（MLX） | Apache-2.0 |

默认的聊天、生图和文本编码器权重是社区发布的“uncensored / abliterated”版本，安全过滤较弱。使用前请阅读每个模型的许可证和说明，并自行承担使用责任。确切的来源和版本固定在 [`models.lock.json`](models.lock.json) 和 [`speech-models.lock.json`](speech-models.lock.json) 中。

## 致谢

本项目基于 [llama.cpp](https://github.com/ggml-org/llama.cpp)、[ComfyUI](https://github.com/Comfy-Org/ComfyUI)、[MLX](https://github.com/ml-explore/mlx)、[Pi](https://github.com/earendil-works/pi)、[Qwen](https://github.com/QwenLM) 与 [MiniMax](https://huggingface.co/MiniMaxAI) 模型系列，以及 [MacMax H3 工作流](https://github.com/Bambushu/minimax-h3-mac)。
