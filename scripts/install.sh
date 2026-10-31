#!/bin/zsh
# One-command installer for Friday (Apple Silicon Mac). Safe to re-run: finished steps are skipped.
#
#   ./scripts/install.sh                 # full: dependencies + chat/image/video/speech + all models
#   ./scripts/install.sh --no-models     # environments only; download models later
#   ./scripts/install.sh --chat-only     # chat only (no ComfyUI or speech; chat model only)
#   ./scripts/install.sh --no-speech     # skip local speech
#
# Never changes existing values in .env and never starts or registers the service.
set -euo pipefail

ROOT="${0:A:h:h}"
cd "$ROOT"

MODELS=1 MEDIA=1 SPEECH=1
for arg in "$@"; do
  case "$arg" in
    --no-models) MODELS=0 ;;
    --chat-only) MEDIA=0 SPEECH=0 ;;
    --no-media) MEDIA=0 ;;
    --no-speech) SPEECH=0 ;;
    -h|--help) sed -n 2,9p "$0"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

step() { print -P "\n%F{cyan}==> $1%f"; }
fail() { print -P "%F{red}Error: $1%f" >&2; exit 1; }
src() { /usr/bin/python3 -c "import json,sys;d=json.load(open('sources.lock.json'));print(d['llama_cpp'] if sys.argv[1]=='llama' else d['repositories'][sys.argv[1]])" "$1"; }

# Check out an upstream repository at the commit pinned in sources.lock.json.
checkout() {
  local dir=$1 url=$2 rev
  rev=$(src "$dir")
  if [[ ! -d $dir/.git ]]; then
    git clone --filter=blob:none "$url" "$dir"
  fi
  if [[ $(git -C "$dir" rev-parse HEAD) != "$rev" ]]; then
    git -C "$dir" fetch --quiet origin "$rev" || git -C "$dir" fetch --quiet origin
    git -C "$dir" checkout --quiet --detach "$rev"
  fi
  echo "$dir @ ${rev:0:10}"
}

step "Checking system"
[[ $(uname -s) == Darwin && $(uname -m) == arm64 ]] || fail "Only Apple Silicon (arm64) macOS is supported."
mem_gb=$(( $(sysctl -n hw.memsize) / 1073741824 ))
echo "macOS $(sw_vers -productVersion), ${mem_gb} GB memory"
(( mem_gb >= 32 )) || fail "At least 32 GB of memory is needed for the 27B chat model."
(( mem_gb >= 64 )) || print -P "%F{yellow}Note: below 64 GB, image/video generation may fail; consider --chat-only.%f"
xcode-select -p >/dev/null 2>&1 || fail "Xcode Command Line Tools are missing. Run: xcode-select --install"

step "Homebrew dependencies (uv, ffmpeg, Node >=22.19, pnpm, git; existing ones are reused)"
command -v brew >/dev/null || fail "Homebrew is not installed: https://brew.sh"
[[ $(brew --prefix) == /opt/homebrew ]] || fail "Homebrew must live in /opt/homebrew (the service uses ffmpeg from that path)."
# Reuse tools that already exist, however they were installed; install only what is missing.
command -v uv >/dev/null || brew install uv
[[ -x /opt/homebrew/bin/ffmpeg ]] || brew install ffmpeg
command -v git >/dev/null || brew install git
NODE=$(command -v node || true)
if [[ -z $NODE ]] || (( $("$NODE" -p 'process.versions.node.split(".").map(Number).reduce((a,b,i)=>i<2?a*1000+b:a,0)') < 22019 )); then
  brew list --formula node@22 >/dev/null 2>&1 || brew install node@22
  NODE="$(brew --prefix node@22)/bin/node"
fi
PNPM=$(command -v pnpm || true)
if [[ -z $PNPM ]]; then
  brew install pnpm
  PNPM="$(brew --prefix)/bin/pnpm"
fi

step "Python environment (.venv, Python 3.12)"
uv python install 3.12
uv sync --python 3.12 --frozen

step "llama.cpp $(src llama)"
LLAMA="vendor/llama/llama-$(src llama)"
if [[ ! -x $LLAMA/llama-server ]]; then
  mkdir -p vendor/llama
  tmp=$(mktemp -d)
  curl -fL --retry 3 -o "$tmp/llama.tar.gz" \
    "https://github.com/ggml-org/llama.cpp/releases/download/$(src llama)/llama-$(src llama)-bin-macos-arm64.tar.gz"
  tar -xzf "$tmp/llama.tar.gz" -C vendor/llama
  rm -rf "$tmp"
  [[ -x $LLAMA/llama-server ]] || fail "$LLAMA/llama-server not found after extracting"
  xattr -dr com.apple.quarantine "$LLAMA" 2>/dev/null || true
fi
echo "$LLAMA/llama-server"

step "Pi Agent (Node $("$NODE" --version))"
.venv/bin/python scripts/setup_pi.py --node "$NODE" --pnpm "$PNPM"

if (( MEDIA )); then
  step "ComfyUI and custom nodes (image/video)"
  checkout vendor/ComfyUI https://github.com/Comfy-Org/ComfyUI.git
  checkout vendor/h3-mac https://github.com/Bambushu/minimax-h3-mac.git
  checkout vendor/ComfyUI/custom_nodes/ComfyUI-GGUF https://github.com/leejet/ComfyUI-GGUF.git
  checkout vendor/ComfyUI/custom_nodes/ComfyUI-AppleSilicon-FP8 https://github.com/pawel-mazurkiewicz/ComfyUI-AppleSilicon-FP8.git
  checkout vendor/ComfyUI/custom_nodes/ComfyUI-PlagueKind-Nodes https://github.com/PlagueKind/ComfyUI-PlagueKind-Nodes.git
  checkout vendor/ComfyUI/custom_nodes/comfyui-obvpm https://github.com/obvpm/comfyui-obvpm.git
  [[ -x vendor/ComfyUI/venv/bin/python ]] || uv venv vendor/ComfyUI/venv --python 3.12
  uv pip sync --python vendor/ComfyUI/venv/bin/python requirements-comfy.lock
fi

if (( SPEECH )); then
  step "Local speech environment (MLX)"
  [[ -x vendor/speech-venv/bin/python ]] || uv venv vendor/speech-venv --python 3.12
  uv pip sync --python vendor/speech-venv/bin/python requirements-speech.lock
fi

step "Skills library and configuration"
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example. Fill in TELEGRAM_* and the other settings."
fi
chmod 600 .env
skills_dir=$(grep -E '^FRIDAY_SKILLS_DIR=' .env | tail -1 | cut -d= -f2- || true)
FRIDAY_SKILLS_DIR="$skills_dir" .venv/bin/python scripts/install_skills.py
mkdir -p runtime && chmod 700 runtime

if (( MODELS )); then
  if (( MEDIA )); then
    step "Downloading and verifying models (~70 GB; re-run to resume)"
    .venv/bin/python scripts/download_ranges.py
  else
    step "Downloading and verifying the chat model (~17 GB)"
    .venv/bin/python scripts/download_ranges.py --only chat
  fi
  if (( SPEECH )); then
    step "Downloading and verifying speech models (~5.5 GB)"
    .venv/bin/python scripts/download_speech.py
  fi
fi

step "Self-check"
.venv/bin/python -c "import local_ai.bot" && echo "Python package imports"
vendor/node pi/runner-v2.mjs --check && echo "Pi runner OK"

print -P "\n%F{green}Installation complete.%f Next: see the Configuration and Start sections of docs/INSTALL.md."
