# Instructions for AI assistants

When a user asks you to install, deploy or run this repository, follow these steps. The full guide is [docs/INSTALL.md](docs/INSTALL.md) ([简体中文](docs/INSTALL.zh-CN.md), [日本語](docs/INSTALL.ja.md)). Reply to the user in their language.

## Install

1. **Check the machine.** `uname -m` must print `arm64`, `brew --prefix` must print `/opt/homebrew`, and `xcode-select -p` must succeed. If any check fails, stop and tell the user what is missing.
2. **Confirm the scope with the user** — the models are large:
   - Full (chat + image + video + speech; ≈100 GB disk, 64 GB memory recommended): `./scripts/install.sh`
   - Chat only (≈25 GB): `./scripts/install.sh --chat-only`
   - Environments now, models later: add `--no-models`

   Run the install in the background; model downloads take a long time. Re-running the same command resumes an interrupted download.
3. **Ask the user for the required values** and write them into `.env` (the installer creates it from `.env.example`):
   - `TELEGRAM_BOT_TOKEN` — from @BotFather in Telegram (`/newbot`)
   - `TELEGRAM_ADMIN_USER_ID` and `TELEGRAM_ALLOWED_USER_IDS` — the user's numeric ID (from @userinfobot)
   - `AGENT_V2=1` is recommended

   Section 3 of the install guide describes every other variable. Leave features the user did not ask for (phone, Google, push) at their disabled defaults.
4. **Verify:** `.venv/bin/python -m pytest -q` must pass.
5. **Start:** ask whether to run in the foreground (`.venv/bin/python -m local_ai.bot`) or install the login service (`.venv/bin/python scripts/service.py install`). Then have the user send `/start` to the bot.

## Rules

- Keep `.env` at mode 600. Never echo tokens or keys in replies, logs or commits, and never commit `.env`, `runtime/`, `models/` or `vendor/`.
- Run only one service instance. Stop the service before running `scripts/local_test.py` or benchmarks, and **before deleting anything in `runtime/`** — deleting it under a running service leaves the service writing to unlinked files.
- Do not edit `*.lock*` files or `sources.lock.json` unless the user asks to upgrade dependencies or change models.
- The phone/PWA entry point is exposed to the internet. Enable it only when the user explicitly asks, following `docs/pwa-security.md`. Passkey invitation codes go to the user only — run `mobile_admin.py passkey-invite` where the user can see the output, and do not read or repeat the code yourself.

## Development

- Tests: `.venv/bin/python -m pytest -q`, `node --test tests/*.test.mjs pi/*.test.mjs`, `./ios/check.sh`. The Python agent tests need `scripts/setup_pi.py` to have run (it creates `vendor/node`).
- The chat model is a hybrid recurrent model: keep the system prompt and tool list byte-stable across turns, or llama.cpp must re-read the whole prompt.
