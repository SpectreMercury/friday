from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image
from telegram import BotCommand, Update
from telegram.ext import CallbackQueryHandler, Application, CommandHandler, ContextTypes, MessageHandler, filters

from .engine import COMFY, ROOT, RUNTIME, Engine, model_status
from .store import Store
from .pi_agent import PiAgent
from .speech import Speech, MAX_VOICE_SECONDS
from .approvals import Approvals
from .evidence import media_state
from .task_context import failure_reply
from .attachments import Attachments, classify, MAX_FILE_BYTES
from .tool_errors import ToolFailure
from .harness import HarnessService
from .media import send_telegram_media

HELP = ("本地 AI：聊天 · 生图 · 视频\n\n可直接要求联网搜索和读取网页；管理员可操作本机文件和终端。\n语音消息：中文语音识别，并用语音与文字回复（最长 3 分钟）\n"
        "直接发文字：Pi + Qwen3.8，可聊天、保存笔记、查询任务、提交生图和视频\n/image 描述：生成图片\n/video 描述：默认生成 15 秒视频\n/video_hd 描述：更高分辨率视频\n"
        "发送图片或文档并附问题：读取附件资料；图片说明用 /video 才生成视频\n/status：模型与自己的任务状态\n/cancel 任务号：取消自己的任务（省略则取消最近任务）\n"
        "/reset：清除自己的聊天历史\n\n短剧可直接发送剧本和目标时长；先估算24小时容量，逐镜头交付并合并。视频耗时取决于时长、分辨率和排队。")


class RedactFormatter(logging.Formatter):
    def format(self, record):
        return re.sub(r"\d{7,}:[A-Za-z0-9_-]{20,}", "[REDACTED]", super().format(record))


class LocalBot(HarnessService):
    def __init__(self):
        load_dotenv(ROOT / ".env")
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.admin = int(os.getenv("TELEGRAM_ADMIN_USER_ID") or 0)
        self.allowed = {int(x.strip()) for x in os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").split(",") if x.strip()}
        if not self.token or not self.admin:
            raise SystemExit("请先在 .env 填写 TELEGRAM_BOT_TOKEN 和 TELEGRAM_ADMIN_USER_ID。")
        super().__init__(RUNTIME,admin=self.admin,configured=self.allowed)

    def is_allowed(self, uid):
        return self.store.allowed(uid, self.allowed, self.admin)

    async def auth(self, update):
        if update.effective_chat.type != "private":
            return False
        if not self.is_allowed(update.effective_user.id):
            if update.callback_query:
                await update.callback_query.answer("此账号未获授权。", show_alert=True)
            else:
                await update.effective_message.reply_text(f"此账号未获授权。你的用户 ID：{update.effective_user.id}。请管理员添加。")
            return False
        return True

    async def command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.auth(update):
            return
        await self.acknowledge(update)
        cmd = update.effective_message.text.split()[0].split('@')[0][1:]
        uid = update.effective_user.id
        sid = self.store.telegram_session(uid,update.effective_chat.id)['id']
        if cmd in {"start", "help"}:
            await update.effective_message.reply_text(HELP)
        elif cmd == "status":
            state = [f"{k}：" + ("可用" if not (m := model_status(k)) else "下载/校验中 " + "；".join(m)) for k in ("chat", "image", "video")]
            jobs = self.store.db.execute("SELECT id,kind,status,outcome FROM jobs WHERE session_id=? ORDER BY created DESC LIMIT 5", (sid,)).fetchall()
            state.extend(f"{j['id']} · {j['kind']} · {j['outcome'] or j['status']}" for j in jobs)
            for row in self.store.db.execute("SELECT * FROM jobs WHERE session_id=? AND kind IN ('image','video') ORDER BY created DESC LIMIT 3", (sid,)):
                checked=media_state(self.store,dict(row))
                receipt=checked['telegram_message_id']
                state.append(f"{row['id']}：文件{'存在' if checked['artifact_exists'] else '未找到'}；" +
                             (f"Telegram 回执 {receipt}（不代表已读）" if receipt else '没有可核验发送回执') +
                             (f"；发送错误 {checked['delivery_error']}" if checked['delivery_error'] else ''))
            active = self.active["kind"] if self.active else "空闲"
            await update.effective_message.reply_text("\n".join(state) + f"\nAgent：Pi\n当前引擎：{active}")
        elif cmd == "reset":
            count = self.store.db.execute("SELECT count(*) FROM jobs WHERE session_id=? AND kind='chat' AND status IN ('queued','running','waiting_approval')", (sid,)).fetchone()[0]
            if count:
                await update.effective_message.reply_text("请先 /cancel 待处理的聊天任务，再清除历史。")
            else:
                self.store.reset(uid,sid)
                await update.effective_message.reply_text("你的聊天历史已清除。")
        elif cmd in {"allow", "deny"}:
            if uid != self.admin:
                await update.effective_message.reply_text("只有管理员可以管理用户。")
                return
            if len(context.args) != 1 or not context.args[0].isdigit():
                await update.effective_message.reply_text(f"用法：/{cmd} 数字用户ID")
                return
            target = int(context.args[0])
            if target == self.admin:
                await update.effective_message.reply_text("管理员账号保持启用。")
                return
            self.store.set_access(target, cmd == "allow")
            if cmd == "deny":
                for row in self.store.db.execute("SELECT id FROM jobs WHERE user_id=? AND status IN ('queued','running','waiting_approval')", (target,)).fetchall():
                    await self.cancel_job(row['id'])
            await update.effective_message.reply_text(f"已{'授权' if cmd == 'allow' else '移除'}用户 {target}。")
        elif cmd == "cancel":
            if context.args:
                job = self.store.get(context.args[0])
            else:
                row = self.store.db.execute("SELECT * FROM jobs WHERE session_id=? AND status IN ('queued','running','waiting_approval') ORDER BY created DESC LIMIT 1", (sid,)).fetchone()
                job = dict(row) if row else None
            if not job or job['session_id']!=sid or job["user_id"] != uid or job["status"] not in {"queued", "running", "waiting_approval"}:
                await update.effective_message.reply_text("没有找到可取消的自己的任务。")
            else:
                await self.cancel_job(job["id"])
                await update.effective_message.reply_text("已取消 " + job["id"])
        else:
            text = update.effective_message.text.partition(" ")[2].strip()
            if not text:
                await update.effective_message.reply_text(f"用法：/{cmd} 你的描述")
                return
            kind = "video" if cmd in {"video", "video_hd"} else cmd
            await self.submit(update, kind, text, hd=cmd == "video_hd")


    async def message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.auth(update):
            return
        await self.acknowledge(update)
        msg = update.effective_message
        if getattr(msg, 'voice', None) or getattr(msg, 'audio', None):
            media = msg.voice or msg.audio
            if not self.speech.ready():
                await msg.reply_text("语音功能还在准备中，请先发文字。")
                return
            duration = media.duration
            if hasattr(duration, 'total_seconds'):
                duration = duration.total_seconds()
            if duration > MAX_VOICE_SECONDS or (media.file_size and media.file_size > 20 * 1024 * 1024):
                await msg.reply_text("请发送 3 分钟以内、小于 20 MB 的语音。")
                return
            with contextlib.suppress(Exception):
                await update.get_bot().send_chat_action(update.effective_chat.id, 'typing')
            incoming = RUNTIME / 'incoming'
            incoming.mkdir(exist_ok=True)
            path = incoming / (secrets.token_hex(16) + '.audio')
            accepted = False
            try:
                await (await media.get_file()).download_to_drive(path)
                accepted = await self.submit(update, 'chat', '', audio=str(path))
            finally:
                if not accepted:
                    path.unlink(missing_ok=True)
        elif msg.photo or msg.document:
            caption = (msg.caption or "").strip()
            explicit_video = caption.startswith(('/video ', '/video_hd '))
            if caption.startswith("/video_hd "):
                hd, caption = True, caption.partition(" ")[2]
            else:
                hd = False
                if caption.startswith("/video "):
                    caption = caption.partition(" ")[2]
            media = msg.photo[-1] if msg.photo else msg.document
            if media.file_size and media.file_size > 20 * 1024 * 1024:
                await msg.reply_text("请发送小于 20 MB 的附件。")
                return
            if explicit_video and model_status("video"):
                await msg.reply_text("视频模型还在下载/校验，请用 /status 查看进度。")
                return
            name = secrets.token_hex(16) + ".png"
            incoming = RUNTIME / "incoming"
            incoming.mkdir(exist_ok=True)
            tmp = incoming / (name + ".upload")
            try:
                await (await media.get_file()).download_to_drive(tmp)
                if tmp.stat().st_size > MAX_FILE_BYTES:
                    raise ValueError('附件超过20MB。')
                if not explicit_video:
                    filename = getattr(media, 'file_name', None) or ('photo.jpg' if msg.photo else 'attachment')
                    classify(tmp, filename)
                    await self.submit(update, 'chat', caption or '已上传附件。请先确认附件类型；若缺少处理要求，询问希望如何处理。',
                                      attachment=(tmp, filename))
                    return
                with Image.open(tmp) as im:
                    if im.width * im.height > 25_000_000:
                        raise ValueError("图片像素过大，请缩小后发送。")
                    im.load()
                    im = im.convert("RGB")
                    im.thumbnail((2048, 2048))
                    (COMFY / "input").mkdir(parents=True, exist_ok=True)
                    im.save(COMFY / "input" / name)
            except (ValueError, OSError):
                await msg.reply_text("无法接收此附件，请检查文件是否有效；视频首帧须为图片。")
                return
            finally:
                tmp.unlink(missing_ok=True)
            await self.submit(update, "video", caption, image=name, hd=hd)
        elif msg.text:
            await self.submit(update, "chat", msg.text)

    async def submit(self, update, kind, prompt, image=None, hd=False, audio=None, attachment=None):
        if len(prompt) > 6000:
            await update.effective_message.reply_text("单次输入请控制在 6000 字符以内。")
            return
        if model_status(kind):
            await update.effective_message.reply_text("该模型还在下载/校验，请用 /status 查看进度。")
            return
        try:
            job_id = self.store.enqueue(update.effective_user.id, update.effective_chat.id, kind, prompt, image, hd, audio=audio,update_id=update.update_id,message_id=getattr(update.effective_message,'message_id',None))
            reply = getattr(update.effective_message,'reply_to_message',None)
            if reply and isinstance(getattr(reply,'message_id',None),int):
                target = self.store.db.execute('SELECT job_id FROM delivery_receipts WHERE chat_id=? AND message_id=? UNION SELECT job_id FROM inbound_updates WHERE chat_id=? AND message_id=? LIMIT 1',(update.effective_chat.id,reply.message_id,update.effective_chat.id,reply.message_id)).fetchone()
                if target: self.store.link_task(self.store.get(job_id),target[0])
        except ValueError as e:
            await update.effective_message.reply_text(str(e))
            return
        if attachment:
            try:
                Attachments(self.store, self.store.get(job_id), RUNTIME).add(*attachment)
            except Exception:
                self.store.update(job_id, status='failed', result='附件未能保存，本次请求尚未执行。')
                raise
        with contextlib.suppress(Exception):
            await update.get_bot().send_chat_action(update.effective_chat.id, "typing")
        return True

    async def acknowledge(self, update):
        # Cosmetic API failures must not block a conversation.
        with contextlib.suppress(Exception):
            await update.effective_message.set_reaction("👀", read_timeout=3, write_timeout=3)

    async def refresh_typing(self, app):
        rows = self.store.db.execute(
            "SELECT DISTINCT user_id,chat_id FROM jobs WHERE channel='telegram' AND (status IN ('queued','running') "
            "OR (status IN ('succeeded','failed') AND delivered=0 "
            "AND delivery_state NOT IN ('uncertain','failed','suppressed')))").fetchall()
        async def send(row):
            if self.is_allowed(row["user_id"]):
                with contextlib.suppress(Exception):
                    await app.bot.send_chat_action(row["chat_id"], "typing", read_timeout=3, write_timeout=3)
        await asyncio.gather(*(send(row) for row in rows))

    async def presence(self, app):
        while True:
            await self.refresh_typing(app)
            await asyncio.sleep(3)



    async def send_artifact(self, chat_id, kind, path):
        return await send_telegram_media(self.channel,chat_id,kind,path)

    async def deliver(self, app, job):
        if not self.is_allowed(job["user_id"]):
            self.store.update(job["id"], delivered=1, delivery_state='suppressed', delivery_error='access_revoked_not_sent')
            return
        async def send(part, operation):
            if not self.store.admit_delivery(job['id'],part):
                return
            try:
                message = await operation()
                self.store.record_receipt(job['id'],part,job['chat_id'],message.message_id)
            except BaseException as exc:
                state=self.store.delivery_failed(job['id'],part,exc)
                if state=='uncertain':
                    self.store.update(job['id'],delivery_state='uncertain',delivery_error='delivery_receipt_unknown')
                    if isinstance(exc,Exception):raise ToolFailure('delivery_uncertain','发送结果不确定，已保留结果与发送记录。',uncertain=True) from exc
                raise

        self.store.update(job['id'], delivery_attempts=(job.get('delivery_attempts') or 0)+1,delivery_state='sending')
        try:
            if job['status'] == 'failed':
                reply = job.get('result') or failure_reply(self.store, job, job.get('error') or '执行中断')
                await send('error_notice', lambda: app.bot.send_message(job['chat_id'], reply[:3500]))
            elif job['kind'] == 'chat':
                if not job.get('text_delivered'):
                    for start in range(0,len(job['result']),3500):
                        await send(f'text:{start}', lambda start=start: app.bot.send_message(job['chat_id'],job['result'][start:start+3500]))
                    self.store.update(job['id'],text_delivered=1)
                if job.get('voice_result') and not self.store.receipt(job['id'],'voice'):
                    with Path(job['voice_result']).open('rb') as voice:
                        await send('voice', lambda: app.bot.send_voice(job['chat_id'],voice,read_timeout=60,write_timeout=60))
            elif not self.store.receipt(job['id'],'media'):
                path=Path(job['result'])
                if not path.is_file() or path.stat().st_size == 0:
                    raise FileNotFoundError('Generated artifact missing')
                project=self.store.video_projects.get(job['id'])
                if project and path.stat().st_size>49_000_000:
                    # Clips have independent durable deliveries. Do not upload
                    # an oversized film repeatedly or claim a text is media.
                    await send('project_summary',lambda: app.bot.send_message(job['chat_id'],
                        '全片已合并。合并文件超过 Telegram 单次发送大小，分镜视频逐段发送；完整文件可在 Friday 原会话查看。'))
                    self.store.update(job['id'],delivered=1,delivery_state='notice_sent',delivery_error=None,retry_after=0)
                    return
                if job['kind'] == 'image':
                    with Image.open(path) as image:
                        image.verify()
                await send('media', lambda: send_telegram_media(app.bot,job['chat_id'],job['kind'],path))
            self.store.update(job['id'],delivered=1,delivery_state='notice_sent' if job['status']=='failed' else 'delivered',delivery_error=None,retry_after=0)
        except Exception as exc:
            if isinstance(exc,ToolFailure) and exc.uncertain:
                self.store.update(job['id'],delivery_state='uncertain',delivery_error='delivery_receipt_unknown')
                raise
            # Persist failure and retry time; a generated file is not a sent file.
            attempts=(job.get('delivery_attempts') or 0)+1
            self.store.update(job['id'],delivery_error=type(exc).__name__,delivery_state='retrying',retry_after=time.time()+60)
            if isinstance(exc,FileNotFoundError) or attempts >= 3:
                # A terminal delivery failure must not masquerade as success.
                with contextlib.suppress(Exception):
                    await send('delivery_failure_notice', lambda: app.bot.send_message(
                        job['chat_id'],'这次结果没有成功交付，不能确认你已收到。可以让我检查发送记录。'))
                    self.store.update(job['id'],delivered=1,delivery_state='failed',retry_after=0)
            raise

    async def delivery_loop(self, app):
        """Channel I/O does not wait for inference, tools, or approvals."""
        running = {}
        async def deliver_one(job):
            try:
                await self.deliver(app, job)
            except Exception:
                logging.warning("Result delivery failed for job %s", job['id'])
        try:
            while True:
                for jid, (chat_id, task) in list(running.items()):
                    if task.done():
                        await task
                        del running[jid]
                occupied = {entry[0] for entry in running.values()}
                for job in self.store.pending_delivery():
                    if len(running) >= 4:
                        break
                    if job['id'] in running or job['chat_id'] in occupied or time.time() < (job.get('retry_after') or 0):
                        continue
                    running[job['id']] = (job['chat_id'], asyncio.create_task(deliver_one(job)))
                    occupied.add(job['chat_id'])
                await asyncio.sleep(.1)
        finally:
            for _, task in running.values():
                task.cancel()
            await asyncio.gather(*(task for _, task in running.values()), return_exceptions=True)



    async def post_init(self, app):
        self.channel = app.bot
        self.approvals.bot = app.bot
        if not self.agent:
            await self.pi.check_runtime()
        self.store.recover_interrupted()
        await app.bot.set_my_commands([BotCommand(n, d) for n, d in [
            ("chat", "Pi 助手：聊天与工具"), ("image", "生成图片"), ("video", "生成短视频"),
            ("video_hd", "生成更高分辨率视频"), ("status", "查看状态"), ("cancel", "取消任务"),
            ("reset", "清除聊天历史"), ("help", "使用说明")]])
        self.presence_task = asyncio.create_task(self.presence(app))
        self.worker_task = asyncio.create_task(self.worker(app))
        self.schedule_task = asyncio.create_task(self.schedule_loop())
        if os.getenv('MOBILE_ENABLED','0')=='1':
            await self.start_mobile(port=int(os.getenv('MOBILE_PORT','8765')))
        logging.info("Telegram Bot ready (build=%s)", self.build_id)

    async def post_stop(self, app):
        await self.stop_mobile()
        if self.presence_task:
            self.presence_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.presence_task
        if self.schedule_task:
            self.schedule_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.schedule_task
        if self.worker_task:
            self.worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.worker_task
        await self.engine.stop()

    async def error(self, update, context):
        logging.error("Telegram handler failed: %s", type(context.error).__name__)

    def run(self):
        builder = Application.builder().token(self.token).post_init(self.post_init).post_stop(self.post_stop)
        if proxy := os.getenv("TELEGRAM_PROXY"):
            builder = builder.proxy(proxy).get_updates_proxy(proxy)
        app = builder.build()
        app.add_handler(CommandHandler(["start", "help", "status", "cancel", "reset", "allow", "deny", "chat", "image", "video", "video_hd"], self.command))
        app.add_handler(MessageHandler((filters.TEXT & ~filters.COMMAND) | filters.PHOTO | filters.Document.ALL | filters.VOICE | filters.AUDIO, self.message))
        app.add_handler(CallbackQueryHandler(self.approvals.callback, pattern=r"^approve:[0-9a-f]{24}:(yes|no|scope)$"))
        app.add_error_handler(self.error)
        app.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=False)


def main():
    RUNTIME.mkdir(exist_ok=True)
    lock = (RUNTIME / "service.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("服务已经运行，请勿重复启动。")
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for handler in logging.getLogger().handlers:
        handler.setFormatter(RedactFormatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    LocalBot().run()


if __name__ == "__main__":
    main()
