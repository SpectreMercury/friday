# インストールと設定

[English](INSTALL.md) · [简体中文](INSTALL.zh-CN.md) · **日本語**

Friday Harness は、ご自身の Mac で動くローカル AI アシスタントです。チャット（llama.cpp 上の Qwen 27B）、画像・動画生成（ComfyUI）、中国語音声（MLX）を備え、Telegram ボット、プライベート PWA、iPhone アプリから利用できます。推論はすべて Mac 上で行われ、クラウドのモデルは呼び出しません。

> AI アシスタントへ：このページを上から順に実行してください。人間が用意する必要があるのは「設定」節で **必須** と書かれた値だけです。ルールはリポジトリ直下の [AGENTS.md](../AGENTS.md)（英語）にあります。

## 1. 動作要件

| 項目 | 要件 |
| --- | --- |
| ハードウェア | Apple Silicon Mac（M1 以降）。チャットのみならメモリ 32 GB 以上、画像・動画には 64 GB を推奨 |
| OS | macOS 14 以降、Xcode コマンドラインツール（`xcode-select --install`） |
| パッケージ管理 | 既定のプレフィックス `/opt/homebrew` にインストールされた [Homebrew](https://brew.sh) |
| ディスク | フルインストールで約 100 GB（モデル約 70 GB＋音声 5.5 GB＋依存関係）、チャットのみで約 25 GB |
| ネットワーク | 初回インストールには GitHub、Hugging Face、PyPI、npm へのアクセスが必要。Telegram を使う場合は Telegram への接続が必要（プロキシ設定可） |

## 2. ワンコマンドインストール

```sh
git clone https://github.com/SpectreMercury/friday.git
cd friday
./scripts/install.sh
```

スクリプトは何度実行しても安全です。完了済みの手順はスキップされ、中断したモデルのダウンロードは再開されます。処理内容：

1. チップ、メモリ、Xcode コマンドラインツール、Homebrew を確認
2. uv、ffmpeg、Node（22.19 以上）、pnpm、git のうち不足しているものだけをインストール（既存のものは再利用）
3. `uv sync --frozen` で `.venv` を作成（Python 3.12、依存関係は `uv.lock` で固定）
4. `sources.lock.json` で固定された llama.cpp のリリースを `vendor/llama/` にダウンロード
5. Pi Agent の依存関係（`pi/pnpm-lock.yaml`）をインストールし、`vendor/node` を手元の Node にリンク
6. ComfyUI、h3-mac、4 つのカスタムノードを固定コミットでチェックアウトし、`vendor/ComfyUI/venv`（`requirements-comfy.lock`）を作成
7. 音声用の環境 `vendor/speech-venv`（`requirements-speech.lock`）を作成
8. `assistant-skills/` をスキルライブラリ（既定 `~/.friday/skills`）にコピー（ローカルの編集は上書きしない）
9. `.env` がなければ `.env.example` から作成し、パーミッションを 600 に設定
10. すべてのモデルをダウンロードし、それぞれの SHA-256 を検証（`models.lock.json`、`speech-models.lock.json`）
11. セルフチェック：Python パッケージを import し、`pi/runner-v2.mjs --check` を実行

オプション：

| オプション | 効果 |
| --- | --- |
| `--chat-only` | ComfyUI と音声を省略し、チャットモデル（約 17 GB）のみダウンロード |
| `--no-media` | 画像・動画を省略 |
| `--no-speech` | ローカル音声を省略 |
| `--no-models` | 環境構築のみ。後で `.venv/bin/python scripts/download_ranges.py` と `scripts/download_speech.py` を実行 |

スクリプトはサービスを起動せず、`.env` の既存の値も変更しません。

## 3. 設定（`.env`）

`.env` は Mac 上にのみ置かれ、Git では無視され、パーミッションは 600 である必要があります。**コミット、貼り付け、スクリーンショットは絶対にしないでください。**

### 3.1 Telegram（最小構成）

| 変数 | 必須 | 意味 | 取得方法 |
| --- | --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | **必須** | ボットのトークン | Telegram で @BotFather に `/newbot` を送る |
| `TELEGRAM_ADMIN_USER_ID` | **必須** | 管理者の数値ユーザー ID。`/allow`・`/deny` で友人を管理できる | Telegram で @userinfobot にメッセージを送る |
| `TELEGRAM_ALLOWED_USER_IDS` | 推奨 | カンマ区切りの数値ユーザー ID。空なら管理者以外は全員拒否 | 同上 |
| `TELEGRAM_PROXY` | 任意 | Telegram への接続に使うプロキシ（例：`socks5://127.0.0.1:7890`） | プロキシアプリ |
| `TTS_SPEAKER` | 任意 | 音声返信の声：`Serena`（既定）、`Vivian`、`Uncle_Fu` | — |
| `AGENT_V2` | 任意 | `1` = プロセス内エージェント（記憶、履歴検索、定期タスク）、`0` = Pi Agent | — |
| `FRIDAY_SKILLS_DIR` | 任意 | スキルライブラリのディレクトリ。既定は `~/.friday/skills` | — |

最小の `.env`：

```dotenv
TELEGRAM_BOT_TOKEN=123456789:AA...あなたのトークン
TELEGRAM_ADMIN_USER_ID=123456789
TELEGRAM_ALLOWED_USER_IDS=123456789
AGENT_V2=1
```

### 3.2 スマートフォン：プライベート PWA / iPhone アプリ（任意）

モバイル API は Telegram と同じプロセスで動き、`127.0.0.1` のみで待ち受けます。外部からのアクセスには、ご自身で用意する HTTPS の入口が必要です（[Tailscale Funnel](pwa-funnel.md) または [VPS + frp + Caddy](mobile-deployment.md)、いずれも中国語）。

| 変数 | 既定 | 意味 |
| --- | --- | --- |
| `MOBILE_ENABLED` | `0` | `1` でサービスと一緒にモバイル API を起動 |
| `MOBILE_PORT` | `8765` | ローカルのポート |
| `MOBILE_PUBLIC_URL` | — | 固定の公開 HTTPS アドレス。パスキーはこの origin に紐づく。ローカル試用なら `http://localhost:8765` |
| `PWA_ENABLED` | `1` | Web アプリ（パスキーでサインイン） |
| `MOBILE_NATIVE_ENABLED` | `0` | ネイティブ iPhone アプリの接続を許可 |
| `MOBILE_NATIVE_PAIRING` | `0` | 新しいデバイスのペアリングを許可 |

最初のパスキーの登録（Mac で実行し、コードは本人にだけ渡す）：

```sh
.venv/bin/python scripts/mobile_admin.py accounts
.venv/bin/python scripts/mobile_admin.py create-account "My account" --admin   # アカウントがない場合のみ
.venv/bin/python scripts/mobile_admin.py passkey-invite --account ACCOUNT_ID
```

その後、スマートフォンの Safari で `<MOBILE_PUBLIC_URL>/login#setup` を開きます。サービスは `TELEGRAM_ADMIN_USER_ID` 用の管理者アカウントを自動で作成するため、通常 `accounts` にはすでに 1 件表示されます。詳細は [PWA セキュリティ](pwa-security.md)（中国語）。

### 3.3 Google Drive / Docs（任意）

| 変数 | 意味 |
| --- | --- |
| `GOOGLE_CLIENT_ID`、`GOOGLE_CLIENT_SECRET` | Google Cloud Console の Web OAuth クライアント |
| `GOOGLE_REDIRECT_URI` | `<MOBILE_PUBLIC_URL>/v1/oauth/google/callback` |
| `GOOGLE_PICKER_API_KEY`、`GOOGLE_PICKER_APP_ID` | Google Picker API を有効にした後の API キーとプロジェクト番号 |

リフレッシュトークンは `.env` ではなく macOS のキーチェーンに保存されます。手順は [ゲートウェイのデプロイ](mobile-deployment.md)（中国語）。

### 3.4 iPhone のプッシュ通知（任意、有料の Apple Developer アカウントが必要）

| 変数 | 意味 |
| --- | --- |
| `APNS_TEAM_ID` | Apple Developer の Team ID |
| `APNS_KEY_ID` | APNs キーの ID |
| `APNS_BUNDLE_ID` | メインアプリのバンドル ID（Xcode の `FRIDAY_BUNDLE_ID` と同じ） |
| `APNS_KEY_FILE` | `.p8` キーの絶対パス。リポジトリの外に置く |

iPhone アプリのビルドと署名は [ios/README.md](../ios/README.md)（中国語）を参照してください。Xcode で `DEVELOPMENT_TEAM` をご自身のチームに、`FRIDAY_BUNDLE_ID` を独自の ID に変更します。

## 4. 起動

```sh
# フォアグラウンド（Telegram＋任意でモバイル API）。Ctrl-C で停止
.venv/bin/python -m local_ai.bot

# Telegram を使わず、スマートフォン/PWA のみ
.venv/bin/python -m local_ai.server

# Mac をスリープさせないユーザー単位のログインサービス（launchd）としてインストール
.venv/bin/python scripts/service.py install
.venv/bin/python scripts/service.py status   # stop / start / restart も同様
```

同時に動かせるインスタンスは 1 つだけです（`runtime/service.lock`）。その後、Telegram でボットに `/start` を送ります。

## 5. 動作確認

```sh
.venv/bin/python -m pytest -q                     # 単体テスト（モデル不要）
.venv/bin/python scripts/local_test.py chat       # 実際のチャット推論（先にサービスを停止）
.venv/bin/python scripts/local_test.py image --smoke
.venv/bin/python scripts/local_test.py video --smoke
```

## 6. プライバシーとデータ

- 推論はすべて Mac 上で行われます。Telegram 経由で送受信したメッセージと結果は Telegram のサーバーを経由します。
- ローカルデータは `runtime/`（チャットデータベース `bot.sqlite3`、ログ、記憶 `runtime/memory/`、作業ファイル `runtime/workspaces/`）と `vendor/ComfyUI/output/` にあり、いずれもコミットされません。バックアップや Mac の譲渡の前にご自身で整理してください。消去する場合は、先にサービスを停止してから `runtime/` を削除します。
- ボットはプライベートチャットのみ受け付け、許可リストが空なら全員を拒否します。
- 管理者のコンピューター/ターミナル操作は、承認後に macOS アカウントの権限で実行されます。**サンドボックスではありません。** 管理者権限を他人に渡さないでください。
- 既定のモデルは安全フィルターが弱いコミュニティ版（"uncensored / abliterated"）の重みです。使用前に各モデルリポジトリのライセンスを確認してください。利用方法の責任は利用者にあります。公式モデルに切り替えるには `scripts/download_models.py` を編集し、`models.lock.json` を削除して再生成します。

## 7. トラブルシューティング

| 症状 | 対処 |
| --- | --- |
| `请先在 .env 填写 TELEGRAM_BOT_TOKEN…`（「先に .env に TELEGRAM_BOT_TOKEN を記入してください」） | 3.1 を記入して再起動 |
| Telegram への接続がタイムアウトする | `TELEGRAM_PROXY` を設定 |
| `Harness 已运行`（「Harness は実行中です」） | すでにインスタンスが動いています：`scripts/service.py status` / `stop` |
| macOS が llama-server をブロックする | `xattr -dr com.apple.quarantine vendor/llama` |
| モデルのチェックサムが一致しない | 該当ファイルと `.verified.json` を削除し、`scripts/download_ranges.py` を再実行 |
| 画像・動画生成でメモリが足りない | 他の大きなアプリを閉じるか、`--chat-only` でインストール |
