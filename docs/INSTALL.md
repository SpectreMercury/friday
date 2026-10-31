# Installation and Configuration

**English** · [简体中文](INSTALL.zh-CN.md) · [日本語](INSTALL.ja.md)

Friday Harness is a local AI assistant that runs on your own Mac: chat (Qwen 27B on llama.cpp), image and video generation (ComfyUI), and Chinese speech (MLX), reachable through a Telegram bot, a private PWA, and an iPhone app. All inference runs on the Mac; no cloud models are called.

> For AI assistants: follow this page top to bottom. The only values a human must supply are the ones marked **Required** in the Configuration section. Rules are in [AGENTS.md](../AGENTS.md) at the repository root.

## 1. Requirements

| Item | Requirement |
| --- | --- |
| Hardware | Apple Silicon Mac (M1 or later). At least 32 GB of memory for chat only; 64 GB recommended for image/video |
| OS | macOS 14 or later, with the Xcode Command Line Tools (`xcode-select --install`) |
| Package manager | [Homebrew](https://brew.sh) at its default prefix `/opt/homebrew` |
| Disk | About 100 GB for a full install (≈70 GB models + 5.5 GB speech + dependencies); about 25 GB for chat only |
| Network | First install needs GitHub, Hugging Face, PyPI and npm; the Telegram entry point needs to reach Telegram (a proxy can be configured) |

## 2. One-command install

```sh
git clone https://github.com/SpectreMercury/friday.git
cd friday
./scripts/install.sh
```

The script is safe to re-run: finished steps are skipped and interrupted model downloads resume. It:

1. checks the chip, memory, Xcode Command Line Tools and Homebrew;
2. installs whichever of uv, ffmpeg, Node (≥22.19), pnpm and git are missing (existing ones are reused);
3. creates `.venv` with `uv sync --frozen` (Python 3.12, dependencies locked in `uv.lock`);
4. downloads the llama.cpp release pinned in `sources.lock.json` into `vendor/llama/`;
5. installs the Pi Agent dependencies (`pi/pnpm-lock.yaml`) and links `vendor/node` to your Node;
6. checks out ComfyUI, h3-mac and four custom nodes at pinned commits and creates `vendor/ComfyUI/venv` (`requirements-comfy.lock`);
7. creates the speech environment `vendor/speech-venv` (`requirements-speech.lock`);
8. copies `assistant-skills/` into the skills library (default `~/.friday/skills`; local edits are never overwritten);
9. creates `.env` from `.env.example` if it does not exist, with mode 600;
10. downloads every model and verifies each SHA-256 (`models.lock.json`, `speech-models.lock.json`);
11. self-checks: imports the Python package and runs `pi/runner-v2.mjs --check`.

Options:

| Option | Effect |
| --- | --- |
| `--chat-only` | Skip ComfyUI and speech; download only the chat model (≈17 GB) |
| `--no-media` | Skip image/video |
| `--no-speech` | Skip local speech |
| `--no-models` | Set up environments only; later run `.venv/bin/python scripts/download_ranges.py` and `scripts/download_speech.py` |

The script never starts the service and never changes values already in `.env`.

## 3. Configuration (`.env`)

`.env` stays on this Mac, is ignored by Git, and must have mode 600. **Never commit, paste or screenshot it.**

### 3.1 Telegram (minimum working setup)

| Variable | Required | Meaning | How to get it |
| --- | --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | **Required** | Bot token | Message @BotFather in Telegram and send `/newbot` |
| `TELEGRAM_ADMIN_USER_ID` | **Required** | Your numeric user ID; the admin can `/allow` and `/deny` friends | Message @userinfobot in Telegram |
| `TELEGRAM_ALLOWED_USER_IDS` | Recommended | Comma-separated numeric user IDs; when empty, everyone except the admin is refused | Same as above |
| `TELEGRAM_PROXY` | Optional | Proxy used to reach Telegram, e.g. `socks5://127.0.0.1:7890` | Your proxy app |
| `TTS_SPEAKER` | Optional | Voice for spoken replies: `Serena` (default), `Vivian`, `Uncle_Fu` | — |
| `AGENT_V2` | Optional | `1` = in-process agent (memory, history search, scheduled tasks); `0` = Pi Agent | — |
| `FRIDAY_SKILLS_DIR` | Optional | Skills library directory, default `~/.friday/skills` | — |

Minimal `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=123456789:AA...your-token
TELEGRAM_ADMIN_USER_ID=123456789
TELEGRAM_ALLOWED_USER_IDS=123456789
AGENT_V2=1
```

### 3.2 Phone access: private PWA / iPhone app (optional)

The phone API runs in the same process as Telegram and listens only on `127.0.0.1`. Public access needs an HTTPS entry point you provide ([Tailscale Funnel](pwa-funnel.md) or [VPS + frp + Caddy](mobile-deployment.md), both in Chinese).

| Variable | Default | Meaning |
| --- | --- | --- |
| `MOBILE_ENABLED` | `0` | `1` starts the phone API with the service |
| `MOBILE_PORT` | `8765` | Local port |
| `MOBILE_PUBLIC_URL` | — | Fixed public HTTPS address; passkeys are bound to this origin. For local testing use `http://localhost:8765` |
| `PWA_ENABLED` | `1` | Web app (passkey sign-in) |
| `MOBILE_NATIVE_ENABLED` | `0` | Allow the native iPhone app to connect |
| `MOBILE_NATIVE_PAIRING` | `0` | Allow pairing new devices |

Register the first passkey (run on the Mac; give the code only to its owner):

```sh
.venv/bin/python scripts/mobile_admin.py accounts
.venv/bin/python scripts/mobile_admin.py create-account "My account" --admin   # only if no account exists
.venv/bin/python scripts/mobile_admin.py passkey-invite --account ACCOUNT_ID
```

Then open `<MOBILE_PUBLIC_URL>/login#setup` in Safari on the phone. The service creates an admin account for `TELEGRAM_ADMIN_USER_ID` automatically, so `accounts` usually lists one already. Details: [PWA security](pwa-security.md) (Chinese).

### 3.3 Google Drive / Docs (optional)

| Variable | Meaning |
| --- | --- |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Web OAuth client from Google Cloud Console |
| `GOOGLE_REDIRECT_URI` | `<MOBILE_PUBLIC_URL>/v1/oauth/google/callback` |
| `GOOGLE_PICKER_API_KEY`, `GOOGLE_PICKER_APP_ID` | API key and project number after enabling the Google Picker API |

Refresh tokens are kept in the macOS Keychain, not in `.env`. Steps: [gateway deployment](mobile-deployment.md) (Chinese).

### 3.4 iPhone push notifications (optional, paid Apple Developer account)

| Variable | Meaning |
| --- | --- |
| `APNS_TEAM_ID` | Your Apple Developer Team ID |
| `APNS_KEY_ID` | APNs key ID |
| `APNS_BUNDLE_ID` | The main app's bundle ID (same as `FRIDAY_BUNDLE_ID` in Xcode) |
| `APNS_KEY_FILE` | Absolute path to the `.p8` key, kept outside the repository |

Building and signing the iPhone app: [ios/README.md](../ios/README.md) (Chinese). In Xcode set `DEVELOPMENT_TEAM` to your team and change `FRIDAY_BUNDLE_ID` to an ID of your own.

## 4. Start

```sh
# Foreground (Telegram + optional phone API); Ctrl-C to stop
.venv/bin/python -m local_ai.bot

# Phone/PWA only, without Telegram
.venv/bin/python -m local_ai.server

# Install as a per-user login service (launchd) that keeps the Mac awake
.venv/bin/python scripts/service.py install
.venv/bin/python scripts/service.py status   # also: stop / start / restart
```

Only one instance may run at a time (`runtime/service.lock`). Then send `/start` to your bot in Telegram.

## 5. Verify

```sh
.venv/bin/python -m pytest -q                     # unit tests, no models needed
.venv/bin/python scripts/local_test.py chat       # real chat inference (stop the service first)
.venv/bin/python scripts/local_test.py image --smoke
.venv/bin/python scripts/local_test.py video --smoke
```

## 6. Privacy and data

- All inference runs on the Mac. Messages and results sent through the Telegram entry point pass through Telegram's servers.
- Local data lives in `runtime/` (chat database `bot.sqlite3`, logs, memory `runtime/memory/`, work files `runtime/workspaces/`) and `vendor/ComfyUI/output/`; none of it is committed. Clean it up yourself before backing up or handing over the machine. To wipe it, stop the service first, then delete `runtime/`.
- The bot accepts private chats only and refuses everyone when the allow list is empty.
- The admin's computer/terminal tools run with your macOS account's permissions after approval. **They are not a sandbox.** Do not give admin rights to anyone else.
- The default models are community "uncensored / abliterated" weights with weak content filtering. Read each model repository's license before use; you are responsible for how you use them. To switch to official models, edit `scripts/download_models.py`, delete `models.lock.json` and regenerate it.

## 7. Troubleshooting

| Symptom | Fix |
| --- | --- |
| `请先在 .env 填写 TELEGRAM_BOT_TOKEN…` ("fill in TELEGRAM_BOT_TOKEN in .env first") | Fill in section 3.1 and restart |
| Telegram connection times out | Set `TELEGRAM_PROXY` |
| `Harness 已运行` ("Harness is already running") | An instance is already running: `scripts/service.py status` / `stop` |
| macOS blocks llama-server | `xattr -dr com.apple.quarantine vendor/llama` |
| Model checksum fails | Delete the file and its `.verified.json`, then re-run `scripts/download_ranges.py` |
| Out of memory during image/video | Close other large apps, or install with `--chat-only` |
