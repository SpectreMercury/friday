<div align="center">

# Friday

**Apple Silicon Mac で動く、プライベートで完全ローカルな AI アシスタント。**
チャット、ツール実行、画像・動画生成、音声を、Telegram、パスキーで保護された PWA、ネイティブ iPhone アプリから利用できます。

[![Python tests](https://github.com/SpectreMercury/friday/actions/workflows/python-tests.yml/badge.svg)](https://github.com/SpectreMercury/friday/actions/workflows/python-tests.yml)
[![Node tests](https://github.com/SpectreMercury/friday/actions/workflows/node-tests.yml/badge.svg)](https://github.com/SpectreMercury/friday/actions/workflows/node-tests.yml)
[![iOS checks](https://github.com/SpectreMercury/friday/actions/workflows/ios-checks.yml/badge.svg)](https://github.com/SpectreMercury/friday/actions/workflows/ios-checks.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/tag/SpectreMercury/friday?label=release&sort=semver)](https://github.com/SpectreMercury/friday/tags)

[![Platform](https://img.shields.io/badge/platform-macOS%2014%2B%20%C2%B7%20Apple%20Silicon-lightgrey?logo=apple)](docs/INSTALL.ja.md#1-動作要件)
[![Python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![Node.js](https://img.shields.io/badge/node-%E2%89%A522.19-339933?logo=nodedotjs&logoColor=white)](pi/package.json)
[![llama.cpp](https://img.shields.io/badge/llama.cpp-b11173-8A2BE2)](sources.lock.json)
[![ComfyUI](https://img.shields.io/badge/ComfyUI-pinned-orange)](sources.lock.json)
[![MLX](https://img.shields.io/badge/MLX-speech-black)](requirements-speech.lock)

[English](README.md) · [简体中文](README.zh-CN.md) · **日本語**

</div>

---

Friday は 27B の言語モデルに加え、画像・動画・音声モデルをすべて Mac 上で実行します。クラウドのモデルは一切呼び出しません。通信が発生するのは、選択したメッセージ経路と、許可したツール（Web 検索など）だけです。

## 目次

- [機能](#機能)
- [アーキテクチャ](#アーキテクチャ)
- [動作要件](#動作要件)
- [クイックスタート](#クイックスタート)
- [設定](#設定)
- [使い方](#使い方)
- [ディレクトリ構成](#ディレクトリ構成)
- [開発とテスト](#開発とテスト)
- [セキュリティとプライバシー](#セキュリティとプライバシー)
- [ライセンス](#ライセンス)
- [謝辞](#謝辞)

## 機能

| 分野 | 内容 |
| --- | --- |
| **チャットエージェント** | Qwen3.8 27B（GGUF、Q4_K_M）を llama.cpp で実行し、マルチトークン予測（MTP）による投機的デコードを使用。ツール呼び出し、計画、長期記憶、セッション横断の履歴検索、定期タスク、サブエージェントへの委任に対応 |
| **ツール** | Web 検索とページ読み込み、メモとファイル、正確な計算機、添付ファイル解析（PDF、Word、Excel、OCR）、Google Drive/Docs、MCP サーバー、明示的な承認が必要な管理者専用のコンピューター/ターミナル操作 |
| **画像** | ComfyUI 上の Qwen Image 2.1。既定は 1024×1024、元の PNG を返却 |
| **動画** | MiniMax H3 によるテキスト/画像からの音声付き動画生成（1 クリップ最大 15 秒）。絵コンテ、ドラフト、レビュー、最終書き出しを備えた複数ショットのショートドラマ制作 |
| **音声** | ローカルの中国語音声認識・音声合成（MLX 上の Qwen3-ASR / Qwen3-TTS）、アプリ内のボイスメッセージとリアルタイム音声 |
| **クライアント** | Telegram ボット、パスキーでサインインするプライベート PWA、プッシュ通知対応のネイティブ iPhone アプリ |
| **運用** | 全クライアントで共有する単一の GPU キュー、再起動後も継続する永続タスク、承認チェックポイント、launchd サービス、バージョン固定とハッシュ検証済みの依存関係・モデル |

## アーキテクチャ

```
 Telegram ─┐                        ┌─ llama.cpp ─── Qwen3.8 27B（チャット、ツール）
 PWA ──────┼─► HarnessService ──────┼─ ComfyUI ───── Qwen Image 2.1 / MiniMax H3
 iPhone ───┘   · セッションと認証   └─ MLX ───────── Qwen3-ASR / Qwen3-TTS
               · 永続タスクキュー
               · エージェントループとツール   すべて 127.0.0.1 のみで待ち受け
               · SQLite（runtime/）
```

1 つの Python プロセス（`local_ai`）が ID、セッション、タスクキュー、GPU スケジューリングを管理します。モデルサーバーは必要に応じて起動され、`127.0.0.1` のみで待ち受け、アイドル時にアンロードされます。設計資料は [`docs/`](docs/)（主に中国語）にあります。

## 動作要件

- Apple Silicon Mac（M1 以降）、macOS 14 以降
- チャットのみならメモリ 32 GB、画像・動画には 64 GB を推奨
- フルインストールで約 100 GB の空き容量（チャットのみなら約 25 GB）
- [Homebrew](https://brew.sh) と Xcode コマンドラインツール

## クイックスタート

```sh
git clone https://github.com/SpectreMercury/friday.git
cd friday
./scripts/install.sh            # フルインストール。チャットのみなら --chat-only
```

次に `.env` に Telegram ボットのトークンとユーザー ID を記入し、サービスを起動します。

```sh
.venv/bin/python scripts/service.py install
```

ボットに `/start` を送れば利用開始です。すべてのオプションを含む完全な手順は **[docs/INSTALL.ja.md](docs/INSTALL.ja.md)** を参照してください。AI コーディングアシスタントは [AGENTS.md](AGENTS.md) に従ってインストールできます。

## 設定

すべての設定は `.env`（[`.env.example`](.env.example) から作成、パーミッション 600、コミット対象外）にあります。

| グループ | 変数 | 用途 |
| --- | --- | --- |
| Telegram | `TELEGRAM_BOT_TOKEN`、`TELEGRAM_ADMIN_USER_ID`、`TELEGRAM_ALLOWED_USER_IDS`、`TELEGRAM_PROXY` | ボット（トークンと管理者 ID は必須） |
| エージェント | `AGENT_V2`、`FRIDAY_SKILLS_DIR`、`TTS_SPEAKER` | エージェントのバージョン、スキルライブラリ、声 |
| スマートフォン / PWA | `MOBILE_ENABLED`、`MOBILE_PORT`、`MOBILE_PUBLIC_URL`、`PWA_ENABLED`、`MOBILE_NATIVE_*` | Web と iPhone からのアクセス |
| Google | `GOOGLE_CLIENT_ID`、`GOOGLE_CLIENT_SECRET`、`GOOGLE_REDIRECT_URI`、`GOOGLE_PICKER_*` | Drive / Docs ツール |
| プッシュ通知 | `APNS_TEAM_ID`、`APNS_KEY_ID`、`APNS_BUNDLE_ID`、`APNS_KEY_FILE` | iPhone の通知 |

各変数の意味と取得方法は [設定](docs/INSTALL.ja.md#3-設定env) を参照してください。

## 使い方

普通にメッセージを送るとチャットになり、必要に応じてエージェントがツールを使います。コマンド：

| コマンド | 動作 |
| --- | --- |
| `/image <プロンプト>` | 画像を生成 |
| `/video <プロンプト>` | 音声付きの縦長 15 秒動画を生成。画像を添付すると最初のフレームに使用 |
| `/video_hd <プロンプト>` | 高解像度の動画（480×864） |
| `/status` | モデル、キュー、タスクの状態 |
| `/cancel [タスク番号]` | タスクを取り消す（既定は直近の待機中タスク） |
| `/reset` | チャットの文脈を消去（ファイルとタスク記録は保持） |
| `/allow <ユーザーID>` · `/deny <ユーザーID>` | 管理者：アクセスの許可・取り消し |

## ディレクトリ構成

| パス | 内容 |
| --- | --- |
| `local_ai/` | サービス本体：Telegram ボット、モバイル API、エージェントループ、ツール、タスクストア、モデルエンジン、PWA |
| `pi/` | [Pi](https://github.com/earendil-works/pi) エージェントランタイムとのブリッジ（Node） |
| `ios/` | ネイティブ iPhone アプリ（`Friday`）と SwiftUI クライアント（`Harness`） |
| `assistant-skills/` | ユーザーのスキルライブラリにコピーされる組み込みスキル |
| `scripts/` | インストーラー、モデルのダウンロード、サービス管理、管理ツール、ベンチマーク |
| `workflows/` | 画像・動画用の ComfyUI ワークフロー |
| `tests/`、`evals/` | 単体テストとエージェント評価ケース |
| `deploy/` | ゲートウェイ設定例（Caddy、frp、launchd） |
| `*.lock*` | 固定された依存関係、上流ソース、モデルのチェックサム |

## 開発とテスト

```sh
uv sync --frozen                                      # Python 環境
.venv/bin/python scripts/setup_pi.py --node "$(command -v node)" --pnpm "$(command -v pnpm)"
.venv/bin/python -m pytest -q                         # Python 単体テスト
node --test tests/*.test.mjs pi/*.test.mjs            # PWA と Pi ブリッジのテスト
./ios/check.sh                                        # iOS 契約チェック
./ios/Friday/check.sh                                 # iPhone アプリのフルビルド（Xcode が必要）
```

テストは一時データとモデルのスタブを使うため、モデルをダウンロードしなくても実行できます。同じテストがプッシュのたびに CI で実行されます。ご自身の Mac でチャットバックエンドを比較するには `scripts/benchmark_chat_backends.py` を使ってください。

## セキュリティとプライバシー

- 推論は Mac 上でのみ行われ、すべてのモデルサーバーは `127.0.0.1` のみで待ち受けます。
- ボットはプライベートチャットのみに応答し、許可リストが空なら全員を拒否します。
- PWA は固定 origin に紐づくパスキーを使い、公開登録なし、CSRF 対策、厳格な Cookie 設定を採用しています。詳細は [PWA セキュリティ](docs/pwa-security.md)（中国語）。
- コンピューター/ターミナル操作は管理者の承認が必要で、macOS アカウントの権限で実行されます。**サンドボックスではありません。**
- チャット、記憶、ファイル、ログは Mac の `runtime/` に保存されます。Telegram 経由のメッセージは Telegram のサーバーを経由します。
- テレメトリは一切収集しません。

脆弱性は公開 issue ではなく、[GitHub セキュリティアドバイザリ](https://github.com/SpectreMercury/friday/security/advisories/new) から非公開で報告してください。

## ライセンス

Friday のソースコードは **[MIT ライセンス](LICENSE)** で公開されています。

インストーラーがダウンロードするサードパーティのソフトウェアとモデルの重みは、このライセンスの対象**外**です。それぞれ固有の条件で使用されます。

| コンポーネント | ライセンス |
| --- | --- |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) | MIT |
| [ComfyUI](https://github.com/Comfy-Org/ComfyUI) | GPL-3.0 |
| [ComfyUI-GGUF](https://github.com/leejet/ComfyUI-GGUF) | Apache-2.0 |
| [ComfyUI-AppleSilicon-FP8](https://github.com/pawel-mazurkiewicz/ComfyUI-AppleSilicon-FP8)、[ComfyUI-PlagueKind-Nodes](https://github.com/PlagueKind/ComfyUI-PlagueKind-Nodes) | MIT |
| [comfyui-obvpm](https://github.com/obvpm/comfyui-obvpm) | GPL-3.0 |
| [minimax-h3-mac](https://github.com/Bambushu/minimax-h3-mac) ワークフロー | MIT |
| [MLX](https://github.com/ml-explore/mlx)、[mlx-audio](https://github.com/Blaizzy/mlx-audio)、[Pi](https://github.com/earendil-works/pi) | MIT |

| モデル | ライセンス |
| --- | --- |
| Qwen3.8 27B（AEON ファインチューン、GGUF） | Apache-2.0 |
| Qwen Image 2.1（量子化ファインチューン） | Qwen Research License — **商用利用不可** |
| MiniMax H3（動画、VAE） | MiniMax H3 Community License |
| MiniMax H3 テキストエンコーダー（Qwen3-VL 32B、GGUF） | Apache-2.0 |
| H3 LoRA（PlagueKind） | MIT |
| Qwen3-ASR / Qwen3-TTS（MLX） | Apache-2.0 |

既定のチャット・画像・テキストエンコーダーの重みは、安全フィルターが弱められたコミュニティ版（"uncensored / abliterated"）です。使用前に各モデルのライセンスとモデルカードを確認してください。利用方法の責任は利用者にあります。正確な配布元とリビジョンは [`models.lock.json`](models.lock.json) と [`speech-models.lock.json`](speech-models.lock.json) に固定されています。

## 謝辞

[llama.cpp](https://github.com/ggml-org/llama.cpp)、[ComfyUI](https://github.com/Comfy-Org/ComfyUI)、[MLX](https://github.com/ml-explore/mlx)、[Pi](https://github.com/earendil-works/pi)、[Qwen](https://github.com/QwenLM) と [MiniMax](https://huggingface.co/MiniMaxAI) のモデル群、そして [MacMax H3 ワークフロー](https://github.com/Bambushu/minimax-h3-mac) の上に構築されています。
