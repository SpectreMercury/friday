"""Pinned, resumable model download with a SHA-256 check for every weight."""
import concurrent.futures
import fcntl
import hashlib
import json
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPECS = [
    ("chat", "vcruz305/Qwen3.8-27B-AEON-ULTIMATE-UNCENSORED-GGUF", "Qwen3.8-27B-AEON-ULTIMATE-UNCENSORED-Q4_K_M.gguf", "models/chat"),
    ("image", "abenzerps/Qwen-Image-2.1-Uncensored-Quantized", "qwen-image-2.1-UC-Q8_0.gguf", "vendor/ComfyUI/models/diffusion_models"),
    ("image_encoder", "abenzerps/Qwen-Image-2.1-Uncensored-Quantized", "text_encoders/qwen3vl_8b_int8_convrot.safetensors", "vendor/ComfyUI/models/text_encoders"),
    ("image_vae", "abenzerps/Qwen-Image-2.1-Uncensored-Quantized", "vae/qwen_image_2.1_vae_bf16.safetensors", "vendor/ComfyUI/models/vae"),
    ("video", "Abiray/MiniMax-H3-Pruned-GGUF", "MiniMax-H3-FL2VA-Pruned-Q5_K_M.gguf", "vendor/ComfyUI/models/diffusion_models"),
    ("video_encoder", "pottokao/MiniMax-H3-TextEncoder-Qwen3VL-32B-abliterated-GGUF", "MiniMax-H3-TextEncoder-Qwen3VL-32B-abliterated-Q3_K_S_vis.gguf", "vendor/ComfyUI/models/text_encoders"),
    ("video_lora", "Plaguekind/H3-Lora", "H3-PK-Parasyte-Turbo.safetensors", "vendor/ComfyUI/models/loras"),
    ("video_vae", "Comfy-Org/MiniMax-H3", "vae/minimax_h3_video_vae_fp16.safetensors", "vendor/ComfyUI/models/vae"),
    ("audio_vae", "Comfy-Org/MiniMax-H3", "vae/minimax_h3_audio_vae_fp32.safetensors", "vendor/ComfyUI/models/vae"),
]

def acquire_download_lock():
    (ROOT / "runtime").mkdir(exist_ok=True)
    lock = (ROOT / "runtime/download.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("A model downloader is already running.")
    return lock

def get_json(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)

def manifest():
    lock = ROOT / "models.lock.json"
    if lock.exists():
        return json.loads(lock.read_text())
    entries, revisions = [], {}
    for key, repo, filename, folder in SPECS:
        if repo not in revisions:
            revisions[repo] = get_json("https://huggingface.co/api/models/" + repo)["sha"]
        rev = revisions[repo]
        parent = str(Path(filename).parent)
        suffix = "" if parent == "." else "/" + parent
        files = get_json(f"https://huggingface.co/api/models/{repo}/tree/{rev}{suffix}")
        info = next(x for x in files if x["path"] == filename)
        entries.append(dict(key=key, repo=repo, revision=rev, filename=filename,
                            path=folder + "/" + Path(filename).name,
                            size=info["size"], sha256=info["lfs"]["oid"]))
    lock.write_text(json.dumps(entries, indent=2) + "\n")
    return entries

def download(entry):
    target = ROOT / entry["path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    marker = target.with_suffix(target.suffix + ".verified.json")
    if target.exists() and marker.exists():
        m = json.loads(marker.read_text())
        if target.stat().st_size == entry["size"] and m.get("sha256") == entry["sha256"] and m.get("mtime_ns") == target.stat().st_mtime_ns:
            print("VERIFIED " + entry["key"], flush=True)
            return
    partial = target.with_suffix(target.suffix + ".part")
    if not target.exists():
        url = f'https://huggingface.co/{entry["repo"]}/resolve/{entry["revision"]}/{entry["filename"]}'
        print("DOWNLOADING " + entry["key"] + " " + str(entry["size"]), flush=True)
        subprocess.run(["curl", "-fL", "--retry", "8", "--retry-delay", "5", "--connect-timeout", "30", "--speed-time", "120", "--speed-limit", "1024", "-sS", "-C", "-", url, "-o", str(partial)], check=True)
        if partial.stat().st_size != entry["size"]:
            raise RuntimeError("Incomplete download: " + entry["key"])
        partial.replace(target)
    digest = hashlib.sha256()
    with target.open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != entry["sha256"]:
        raise RuntimeError("Checksum mismatch: " + str(target))
    marker.write_text(json.dumps({"sha256": digest.hexdigest(), "mtime_ns": target.stat().st_mtime_ns}))
    print("VERIFIED " + entry["key"], flush=True)

if __name__ == "__main__":
    lock = acquire_download_lock()
    with concurrent.futures.ThreadPoolExecutor(max_workers=9) as pool:
        list(pool.map(download, manifest()))
