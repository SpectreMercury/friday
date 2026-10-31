from __future__ import annotations

import asyncio
import fcntl
import json
import math
import os
import secrets
import signal
import time
from contextlib import asynccontextmanager
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
COMFY = ROOT / "vendor/ComfyUI"
RUNTIME = ROOT / "runtime"
LLAMA_URL = "http://127.0.0.1:8085"
COMFY_URL = "http://127.0.0.1:8288"
MODEL_KEYS = {
    "chat": {"chat"},
    "image": {"image", "image_encoder", "image_vae"},
    "video": {"video", "video_encoder", "video_lora", "video_vae", "audio_vae"},
}


def model_status(kind: str) -> list[str]:
    lock = ROOT / "models.lock.json"
    if not lock.exists():
        return ["模型清单尚未生成"]
    missing = []
    for entry in json.loads(lock.read_text()):
        if entry["key"] not in MODEL_KEYS[kind]:
            continue
        path = ROOT / entry["path"]
        marker = path.with_suffix(path.suffix + ".verified.json")
        verified = False
        if path.exists() and marker.exists():
            try:
                checked = json.loads(marker.read_text())
                stat = path.stat()
                verified = (stat.st_size == entry["size"] and checked.get("sha256") == entry["sha256"]
                            and checked.get("mtime_ns") == stat.st_mtime_ns)
            except (OSError, ValueError):
                pass
        if not verified:
            partial = path.with_suffix(path.suffix + ".part")
            size = path.stat().st_size if path.exists() else partial.stat().st_size if partial.exists() else 0
            chunks = path.with_suffix(path.suffix + ".chunks")
            if not path.exists() and (chunks / "plan.json").exists():
                prefix = json.loads((chunks / "plan.json").read_text())["prefix"]
                size = prefix + sum(p.stat().st_size for p in chunks.iterdir() if p.suffix in {".chunk", ".tmp"})
            progress = min(100, size / entry["size"] * 100)
            missing.append(f'{entry["key"]}: 校验中' if progress >= 100 else f'{entry["key"]}: {progress:.0f}%')
    return missing


def build_workflow(kind: str, prompt: str, job_id: str, *, image: str | None = None,
                   hd: bool = False, smoke: bool = False, seed: int | None = None,
                   duration_seconds: int | None = None) -> dict:
    graph = json.loads((ROOT / "workflows" / f"{kind}.json").read_text())
    graph["text"]["inputs"]["prompt"] = prompt
    graph["save"]["inputs"]["filename_prefix"] = f"telegram/{job_id}/{kind}"
    seed = secrets.randbelow(2**32) if seed is None else seed
    if kind == "image":
        graph["sample"]["inputs"]["seed"] = seed
        if smoke:
            graph["latent"]["inputs"].update(width=512, height=512)
            graph["sample"]["inputs"]["steps"] = 4
    else:
        graph["noise"]["inputs"]["noise_seed"] = seed
        if duration_seconds is not None:
            if isinstance(duration_seconds,bool) or not isinstance(duration_seconds,int) or not 4<=duration_seconds<=15:
                raise ValueError('单个 H3 镜头时长须为4–15秒。')
            graph['text']['inputs']['length'] = 5 + 17 * math.ceil((duration_seconds * 24 - 5) / 17)
        if hd:
            graph["text"]["inputs"].update(width=480, height=864)
        if smoke:
            graph["text"]["inputs"].update(width=256, height=416, length=73)
        if image:
            graph["input_image"] = {"class_type": "LoadImage", "inputs": {"image": image}}
            graph["text"]["inputs"]["first_frame"] = ["input_image", 0]
    return graph


class OutputTruncated(RuntimeError):
    """The model hit max_tokens before finishing a structured (JSON) answer."""


class Engine:
    """Owned chat plus resident speech; image/video rendering uses memory exclusively."""

    def __init__(self):
        self.process: asyncio.subprocess.Process | None = None
        self.kind: str | None = None
        self.log = None
        self.process_lock = None
        self.lock = asyncio.Lock()
        self.speech_worker = None
        self._voice_owner: str | None = None
        self._voice_lock = asyncio.Lock()
        self._voice_idle = asyncio.Event()
        self._voice_idle.set()
        self._media_running = False

    @property
    def voice_reserved(self) -> bool:
        return self._voice_owner is not None

    async def reserve_voice(self, owner: str):
        """Keep chat resident; leave ``lock`` available to each harness inference."""
        async with self._voice_lock:
            if self._voice_owner is not None:
                raise RuntimeError('本机已有实时语音通话，请稍后重试。')
            if self._media_running or (self.lock.locked() and self.kind in ('image', 'video')):
                raise RuntimeError('媒体任务正在生成，请在完成后开始通话。')
            self._voice_owner = owner
            self._voice_idle.clear()
            try:
                async with self.lock:
                    await self.start('chat')
            except BaseException:
                self._voice_owner = None
                self._voice_idle.set()
                raise

    async def release_voice(self, owner: str):
        async with self._voice_lock:
            if self._voice_owner == owner:
                self._voice_owner = None
                self._voice_idle.set()

    @asynccontextmanager
    async def _media_slot(self):
        while True:
            # Voice can begin while a media prompt is being rewritten by chat.
            # Wait outside the inference lock so that call can keep using chat.
            await self._voice_idle.wait()
            async with self.lock:
                if self.voice_reserved:
                    continue
                self._media_running = True
                try:
                    yield
                finally:
                    self._media_running = False
                return

    async def stop(self, *, keep_speech=False):
        if not keep_speech and self.speech_worker:
            await self.speech_worker.close()
        proc, self.process = self.process, None
        self.kind = None
        if proc and proc.returncode is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                await asyncio.wait_for(proc.wait(), 15)
            except asyncio.TimeoutError:
                os.killpg(proc.pid, signal.SIGKILL)
                await proc.wait()
            except ProcessLookupError:
                pass
        if self.log:
            self.log.close()
            self.log = None
        if self.process_lock and not keep_speech:
            self.process_lock.close()
            self.process_lock = None

    def claim(self):
        if self.process_lock:
            return
        RUNTIME.mkdir(exist_ok=True)
        self.process_lock = (RUNTIME / "engine.lock").open("w")
        try:
            fcntl.flock(self.process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.process_lock.close()
            self.process_lock = None
            raise RuntimeError("本机正在进行另一个推理任务，请稍后重试。")

    def running(self, kind: str) -> bool:
        return self.kind == kind and self.process is not None and self.process.returncode is None

    async def start(self, kind: str):
        if kind != 'chat' and self.voice_reserved:
            raise RuntimeError('实时语音通话期间，媒体生成需要排队。')
        missing = model_status(kind)
        if missing:
            raise RuntimeError("模型仍在下载或校验：" + "；".join(missing))
        if self.running(kind):
            return
        # Both media workflows use the same Comfy process and exclusive GPU
        # lease. Let Comfy replace its models instead of restarting Python and
        # importing every custom node again between image and video jobs.
        if kind in ('image','video') and self.kind in ('image','video') and self.process and self.process.returncode is None:
            self.kind = kind
            return
        await self.stop(keep_speech=(kind == 'chat'))
        env = os.environ.copy()
        env["PATH"] = "/opt/homebrew/bin:" + env.get("PATH", "")
        # Credentials belong to the harness, never to inference workers.
        for k in tuple(env):
            if k.startswith(("TELEGRAM_", "GOOGLE_", "APNS_", "PROVIDER_", "MOBILE_")):
                del env[k]
        if kind == "chat":
            binary = next((ROOT / "vendor/llama").glob("*/llama-server"))
            model = next((ROOT / "models/chat").glob("*.gguf"))
            args = [str(binary), "-m", str(model), "--host", "127.0.0.1", "--port", "8085",
                    "--ctx-size", "32768", "--parallel", "1", "--jinja", "--reasoning-format", "deepseek",
                    "-ngl", "99", "-fa", "on", "--alias", "qwen38",
                    # The model ships a multi-token-prediction head: drafting 3 tokens with it
                    # measured ~1.3-1.6x faster replies on this Mac (creative text slightly less).
                    "--spec-type", "draft-mtp", "--spec-draft-n-max", "3",
                    # Hybrid (DeltaNet) layers cannot rewind, so reuse depends on saved states.
                    # Fewer checkpoints per prompt keep each saved prompt ~2 GB instead of ~5 GB,
                    # and a larger cache keeps several conversations (and side calls) warm.
                    "--ctx-checkpoints", "16", "--cache-ram", "16384"]
            cwd, health = ROOT, LLAMA_URL + "/health"
        else:
            env["ASFP8_INT8_EXT"] = "1"
            args = [str(COMFY / "venv/bin/python"), "main.py", "--listen", "127.0.0.1", "--port", "8288",
                    "--reserve-vram", "10", "--cache-none", "--disable-smart-memory", "--disable-auto-launch",
                    "--disable-metadata"]
            cwd, health = COMFY, COMFY_URL + "/system_stats"
        # Refuse to adopt an unrelated server already listening on our port.
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as session:
            try:
                async with session.get(health):
                    raise RuntimeError("推理端口已被其他进程占用，请关闭重复服务。")
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass
        RUNTIME.mkdir(exist_ok=True)
        self.claim()
        try:
            self.log = (RUNTIME / f"{kind}.log").open("ab")
            self.process = await asyncio.create_subprocess_exec(*args, cwd=cwd, env=env,
                                                               stdout=self.log, stderr=self.log, start_new_session=True)
            self.kind = kind
            deadline = time.monotonic() + 300
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                while time.monotonic() < deadline:
                    if self.process.returncode is not None:
                        raise RuntimeError(f"{kind} 服务启动失败，详情见 runtime/{kind}.log")
                    try:
                        async with session.get(health) as r:
                            if r.status == 200:
                                return
                    except (aiohttp.ClientError, asyncio.TimeoutError):
                        pass
                    await asyncio.sleep(2)
            raise RuntimeError("模型加载超过 5 分钟，请查看服务日志。")
        except BaseException:
            await self.stop()
            raise

    async def chat(self, messages: list[dict], *, schema=None, max_tokens=2048) -> str:
        async with self.lock:
            await self.start("chat")
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=900)) as s:
                    async with s.post(LLAMA_URL + "/v1/chat/completions", json={
                        "model": "qwen38", "messages": messages, "temperature": 0.2,
                        **({"response_format":{"type":"json_object","schema":schema}} if schema else {}),
                        "max_tokens": max_tokens, "stream": False,
                        "chat_template_kwargs": {"enable_thinking": False},
                    }) as r:
                        try:
                            body = await r.json()
                        except (ValueError, aiohttp.ContentTypeError):
                            body = {"error": f"HTTP {r.status}，返回内容不是 JSON"}
                        if r.status != 200:
                            raise RuntimeError("聊天推理失败：" + str(body.get("error", "unknown"))[:250])
                        choice = body["choices"][0]
                        answer = choice["message"].get("content")
                        if not answer:
                            raise RuntimeError("模型没有返回正文，请缩短问题后重试。")
                        if schema and choice.get("finish_reason") == "length":
                            raise OutputTruncated("模型输出超出长度上限，JSON 不完整。")
                        return answer
            except (aiohttp.ClientError, asyncio.TimeoutError):
                # Only a dead or hung server is worth a restart; a cancelled request or a bad
                # answer leaves it healthy, and reloading costs a cold start of the whole prompt.
                await self.stop()
                raise

    async def render(self, kind: str, prompt: str, job_id: str, *, image: str | None = None,
                     hd: bool = False, smoke: bool = False, seed: int | None = None,
                     workflow: dict | None = None) -> Path:
        async with self._media_slot():
            try:
                await self.start(kind)
                graph = workflow if workflow is not None else build_workflow(kind, prompt, job_id, image=image, hd=hd, smoke=smoke, seed=seed)
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
                    async with s.post(COMFY_URL + "/prompt", json={"prompt": graph, "client_id": job_id}) as r:
                        data = await r.json()
                        if r.status != 200:
                            (RUNTIME / f"{job_id}-workflow-error.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))
                            raise RuntimeError("工作流验证失败，详情已保存到本机日志。")
                        prompt_id = data["prompt_id"]
                    deadline = time.monotonic() + 5400
                    while time.monotonic() < deadline:
                        if self.process.returncode is not None:
                            raise RuntimeError("生成进程退出，请查看本机日志。")
                        async with s.get(COMFY_URL + f"/history/{prompt_id}") as r:
                            history = (await r.json()).get(prompt_id)
                        if history:
                            (RUNTIME / f"{job_id}-history.json").write_text(json.dumps(history, ensure_ascii=False, indent=2))
                            status = history.get("status", {})
                            if status.get("status_str") == "error":
                                raise RuntimeError("生成失败，错误详情已保存在本机任务日志。")
                            if status.get("completed"):
                                result = history.get("outputs", {}).get("save", {})
                                for value in result.values():
                                    if not isinstance(value, list):
                                        continue
                                    for item in value:
                                        if isinstance(item, dict) and "filename" in item:
                                            path = (COMFY / "output" / item.get("subfolder", "") / item["filename"]).resolve()
                                            if path.is_relative_to((COMFY / "output").resolve()) and path.is_file():
                                                return path
                                raise RuntimeError("生成完成，但未找到输出文件。")
                        await asyncio.sleep(3)
                    raise RuntimeError("生成超过 90 分钟，已停止以释放内存。")
            except BaseException:
                await self.stop()
                raise
