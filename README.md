<div align="center">

# Friday

**A private, fully local AI assistant for Apple Silicon Macs.**
Chat, tools, image and video generation, and voice — reachable from Telegram, a passkey-protected PWA, or a native iPhone app.

[![Python tests](https://github.com/SpectreMercury/friday/actions/workflows/python-tests.yml/badge.svg)](https://github.com/SpectreMercury/friday/actions/workflows/python-tests.yml)
[![Node tests](https://github.com/SpectreMercury/friday/actions/workflows/node-tests.yml/badge.svg)](https://github.com/SpectreMercury/friday/actions/workflows/node-tests.yml)
[![iOS checks](https://github.com/SpectreMercury/friday/actions/workflows/ios-checks.yml/badge.svg)](https://github.com/SpectreMercury/friday/actions/workflows/ios-checks.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/tag/SpectreMercury/friday?label=release&sort=semver)](https://github.com/SpectreMercury/friday/tags)

[![Platform](https://img.shields.io/badge/platform-macOS%2014%2B%20%C2%B7%20Apple%20Silicon-lightgrey?logo=apple)](docs/INSTALL.md#1-requirements)
[![Python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![Node.js](https://img.shields.io/badge/node-%E2%89%A522.19-339933?logo=nodedotjs&logoColor=white)](pi/package.json)
[![llama.cpp](https://img.shields.io/badge/llama.cpp-b11173-8A2BE2)](sources.lock.json)
[![ComfyUI](https://img.shields.io/badge/ComfyUI-pinned-orange)](sources.lock.json)
[![MLX](https://img.shields.io/badge/MLX-speech-black)](requirements-speech.lock)

**English** · [简体中文](README.zh-CN.md) · [日本語](README.ja.md)

</div>

---

Friday runs a 27B language model, an image model, a video model and speech models entirely on your Mac. Nothing is sent to a cloud model: the only network traffic is the messaging channel you choose and the tools you allow (such as web search).

## Contents

- [Features](#features)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Usage](#usage)
- [Project layout](#project-layout)
- [Development](#development)
- [Security and privacy](#security-and-privacy)
- [License](#license)
- [Acknowledgements](#acknowledgements)

## Features

| Area | What you get |
| --- | --- |
| **Chat agent** | Qwen3.8 27B (GGUF, Q4_K_M) on llama.cpp with multi-token-prediction speculative decoding; tool calling, planning, long-term memory, cross-session history search, scheduled tasks and sub-agent delegation |
| **Tools** | Web search and reading, notes and files, exact calculator, attachments (PDF, Word, Excel, OCR), Google Drive/Docs, MCP servers, and admin-only computer/terminal tools behind explicit approval |
| **Images** | Qwen Image 2.1 through ComfyUI, 1024×1024 by default, original PNG returned |
| **Video** | MiniMax H3 text/image-to-video with audio, up to 15 s per clip; multi-shot short-drama projects with storyboard, drafts, review and final export |
| **Voice** | Local Chinese speech recognition and synthesis (Qwen3-ASR / Qwen3-TTS on MLX), voice messages and real-time voice in the app |
| **Clients** | Telegram bot, private PWA with passkey sign-in, native iPhone app with push notifications |
| **Operations** | One GPU queue shared by every client, durable tasks that survive restarts, approval checkpoints, launchd service, pinned and checksummed dependencies and models |

## Architecture

```
 Telegram ─┐                        ┌─ llama.cpp ─── Qwen3.8 27B (chat, tools)
 PWA ──────┼─► HarnessService ──────┼─ ComfyUI ───── Qwen Image 2.1 / MiniMax H3
 iPhone ───┘   · sessions & auth    └─ MLX ───────── Qwen3-ASR / Qwen3-TTS
               · durable task queue
               · agent loop + tools        all bound to 127.0.0.1
               · SQLite (runtime/)
```

A single Python process (`local_ai`) owns identity, sessions, the task queue and GPU scheduling. Model servers are started on demand, bound to `127.0.0.1`, and unloaded when idle. Design notes are in [`docs/`](docs/) (mostly in Chinese).

## Requirements

- Apple Silicon Mac (M1 or later) running macOS 14+
- 32 GB memory for chat only; 64 GB recommended for image and video
- About 100 GB of free disk for a full install (25 GB for chat only)
- [Homebrew](https://brew.sh) and the Xcode Command Line Tools

## Quick start

```sh
git clone https://github.com/SpectreMercury/friday.git
cd friday
./scripts/install.sh            # full install; use --chat-only for chat alone
```

Then add your Telegram bot token and user ID to `.env` and start the service:

```sh
.venv/bin/python scripts/service.py install
```

Send `/start` to your bot. The complete guide, including every option, is in **[docs/INSTALL.md](docs/INSTALL.md)**. AI coding assistants can install the project by following [AGENTS.md](AGENTS.md).

## Configuration

All settings live in `.env` (created from [`.env.example`](.env.example), mode 600, never committed).

| Group | Variables | Needed for |
| --- | --- | --- |
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_USER_ID`, `TELEGRAM_ALLOWED_USER_IDS`, `TELEGRAM_PROXY` | The bot (token and admin ID are required) |
| Agent | `AGENT_V2`, `FRIDAY_SKILLS_DIR`, `TTS_SPEAKER` | Agent version, skills library, voice |
| Phone / PWA | `MOBILE_ENABLED`, `MOBILE_PORT`, `MOBILE_PUBLIC_URL`, `PWA_ENABLED`, `MOBILE_NATIVE_*` | Web and iPhone access |
| Google | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, `GOOGLE_PICKER_*` | Drive / Docs tools |
| Push | `APNS_TEAM_ID`, `APNS_KEY_ID`, `APNS_BUNDLE_ID`, `APNS_KEY_FILE` | iPhone notifications |

See [Configuration](docs/INSTALL.md#3-configuration-env) for what each variable does and how to obtain it.

## Usage

Send ordinary messages to chat; the agent decides when to use tools. Commands:

| Command | Action |
| --- | --- |
| `/image <prompt>` | Generate an image |
| `/video <prompt>` | Generate a 15 s portrait video with audio; attach an image to use it as the first frame |
| `/video_hd <prompt>` | Higher-resolution video (480×864) |
| `/status` | Model, queue and task status |
| `/cancel [task]` | Cancel a task (the latest pending one by default) |
| `/reset` | Clear your chat context (files and task records are kept) |
| `/allow <user id>` · `/deny <user id>` | Admin: grant or revoke access |

## Project layout

| Path | Contents |
| --- | --- |
| `local_ai/` | Service: Telegram bot, mobile API, agent loop, tools, task store, model engines, PWA assets |
| `pi/` | Bridge to the [Pi](https://github.com/earendil-works/pi) agent runtime (Node) |
| `ios/` | Native iPhone app (`Friday`) and SwiftUI client (`Harness`) |
| `assistant-skills/` | Built-in skills copied into the user's skills library |
| `scripts/` | Installer, model downloads, service management, admin and benchmark tools |
| `workflows/` | ComfyUI graphs for image and video |
| `tests/`, `evals/` | Unit tests and agent evaluation cases |
| `deploy/` | Example gateway configurations (Caddy, frp, launchd) |
| `*.lock*` | Pinned dependencies, upstream sources and model checksums |

## Development

```sh
uv sync --frozen                                      # Python environment
.venv/bin/python scripts/setup_pi.py --node "$(command -v node)" --pnpm "$(command -v pnpm)"
.venv/bin/python -m pytest -q                         # Python unit tests
node --test tests/*.test.mjs pi/*.test.mjs            # PWA and Pi bridge tests
./ios/check.sh                                        # iOS contract checks
./ios/Friday/check.sh                                 # full iPhone app build (needs Xcode)
```

The test suites use temporary data and model stubs, so they run without downloaded models; the same suites run in CI on every push. To compare chat back ends on your own Mac, see `scripts/benchmark_chat_backends.py`.

## Security and privacy

- Inference stays on the Mac; every model server listens on `127.0.0.1` only.
- The bot answers private chats only, and refuses everyone when the allow list is empty.
- The PWA uses passkeys with a fixed origin, no public sign-up, CSRF protection and strict cookies; see [PWA security](docs/pwa-security.md).
- Computer and terminal tools require admin approval and run with your macOS account's permissions. **They are not a sandbox.**
- Chats, memory, files and logs stay in `runtime/` on your Mac. Messages sent through Telegram pass through Telegram's servers.
- No telemetry is collected.

Please report vulnerabilities privately through [GitHub security advisories](https://github.com/SpectreMercury/friday/security/advisories/new) rather than public issues.

## License

Friday's source code is released under the **[MIT License](LICENSE)**.

The installer downloads third-party software and model weights that are **not** covered by this license. Each is used under its own terms:

| Component | License |
| --- | --- |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) | MIT |
| [ComfyUI](https://github.com/Comfy-Org/ComfyUI) | GPL-3.0 |
| [ComfyUI-GGUF](https://github.com/leejet/ComfyUI-GGUF) | Apache-2.0 |
| [ComfyUI-AppleSilicon-FP8](https://github.com/pawel-mazurkiewicz/ComfyUI-AppleSilicon-FP8), [ComfyUI-PlagueKind-Nodes](https://github.com/PlagueKind/ComfyUI-PlagueKind-Nodes) | MIT |
| [comfyui-obvpm](https://github.com/obvpm/comfyui-obvpm) | GPL-3.0 |
| [minimax-h3-mac](https://github.com/Bambushu/minimax-h3-mac) workflows | MIT |
| [MLX](https://github.com/ml-explore/mlx), [mlx-audio](https://github.com/Blaizzy/mlx-audio), [Pi](https://github.com/earendil-works/pi) | MIT |

| Model | License |
| --- | --- |
| Qwen3.8 27B (AEON fine-tune, GGUF) | Apache-2.0 |
| Qwen Image 2.1 (quantized fine-tune) | Qwen Research License — **non-commercial** |
| MiniMax H3 (video, VAEs) | MiniMax H3 Community License |
| MiniMax H3 text encoder (Qwen3-VL 32B, GGUF) | Apache-2.0 |
| H3 LoRA (PlagueKind) | MIT |
| Qwen3-ASR / Qwen3-TTS (MLX) | Apache-2.0 |

The default chat, image and text-encoder weights are community "uncensored / abliterated" variants with weakened safety filtering. Review each model's license and card before use; you are responsible for how you use them. Exact sources and revisions are pinned in [`models.lock.json`](models.lock.json) and [`speech-models.lock.json`](speech-models.lock.json).

## Acknowledgements

Built on [llama.cpp](https://github.com/ggml-org/llama.cpp), [ComfyUI](https://github.com/Comfy-Org/ComfyUI), [MLX](https://github.com/ml-explore/mlx), [Pi](https://github.com/earendil-works/pi), the [Qwen](https://github.com/QwenLM) and [MiniMax](https://huggingface.co/MiniMaxAI) model families, and the [MacMax H3 workflow](https://github.com/Bambushu/minimax-h3-mac).
