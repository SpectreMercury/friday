# 全本地语音优化（2026-09-26）

## 已实现

- `scripts/resident_speech_worker.py`：常驻 Qwen3-ASR 1.7B + Qwen3-TTS 1.7B MLX 进程，模型离线加载，启动时预热；JSONL 私有管道，不监听公网。
- `local_ai/voice_runtime.py`：异步请求、PCM16 音频块、取消与协议排空、进程关闭；专用通话会话可同时保留本地 Qwen 聊天和语音模型。
- `local_ai/voice_audio.py`：WebRTC VAD，16 kHz 单声道输入、20 ms 帧、约 60 ms 起声检测、400 ms 静音断句、最长 12 秒分段；播放缓冲按代次清空，拒收被打断回复的迟到音频。
- `local_ai/voice_loop.py`：将分段识别、语言选择、注入的本地回复函数、流式发声、插话清空串联。尚未连接电话传输层。
- `local_ai/voice_language.py`：跟随 ASR 语言标签；粤语文字提示仅为辅助，需要澄清时保留标记；未知语言不冒充支持。
- Telegram 的 `Speech` 已使用同一常驻进程。STT、聊天、TTS 之间不会再反复卸载聊天模型；生成图片/视频前和空闲释放时关闭语音模型，归还内存。长回复仍分段朗读，语音长度保留原有上限。
- 依赖增加 `webrtcvad-wheels`，已写入项目与锁文件。

## 语言与发声后端

| 语言 | 识别 | 发声 |
| --- | --- | --- |
| 普通话 | 本地 Qwen3-ASR | 本地 Qwen3-TTS，Serena |
| 英语 | 本地 Qwen3-ASR | 本地 Qwen3-TTS，Serena |
| 日语 | 本地 Qwen3-ASR | 本地 Qwen3-TTS，Ono_Anna |
| 粤语 | 本地 Qwen3-ASR | 本机已安装的 macOS Sinji 女声 |

Qwen CustomVoice 粤语提示词测试出现明显转写偏差，因此不把它作为粤语后端。Sinji 是 macOS 内置本地声音，不是开源 Qwen 模型，也没有使用云端 TTS；当前版本先完成整句本地合成，再分块输出。四种语言之外还未接入，不能承诺任何语言都能处理。

## 实测结果

以下都是本机合成短句和模拟餐厅问答，未拨打电话、未向 Telegram 发送测试消息：

| 语言 | 预热后 TTS 首音频块 | ASR + 本地 Qwen + TTS 首块 |
| --- | --- | --- |
| 普通话 | 约 0.10–0.11 秒 | 约 1.92 秒 |
| 英语 | 约 0.11–0.12 秒 | 约 2.02 秒 |
| 日语 | 约 0.10–0.12 秒 | 约 1.67 秒 |
| 粤语 | 约 0.55–0.57 秒 | 约 2.39 秒 |

上表不含餐厅说话的时长、400 ms 断句等待、电话网络和传输播放缓冲。并非生产延迟保证、统计分位数或真实电话测试。首次模型加载/预热另外计时；完全冷启动还可能触发 MLX 首次编译。

- MLX 语音进程峰值分配约 6.58 GB，不包含独立的 llama.cpp 聊天进程。
- 测试合成速度快于音频播放速度，所测短句没有生成端供给不足造成的播放缺口。
- 取消当前合成后约 0.083 秒收到取消完成确认；恢复下一句话成功。真正电话插话还需要传输层同时清除服务商播放缓冲，不能把该数据当作线路端停止发声的时间。
- 8 kHz μ-law 编码后还原为 16 kHz 的测试句可以被识别、分段；日语样本仍有“何名様”的转写错误。粤语示例的“幾多位”在更换后端后保留正确。
- 这些是同机合成音频回读，不代替真实餐厅噪声、口音、英语夹中文以及连续语言切换的测试。日期、人数和姓名需在预订对话中复述核对。
- Telegram 集成测试：短中文回复合成加 OGG 编码约 0.52 秒，下一句约 0.19 秒；确认 ASR→Pi→TTS 期间语音 PID 复用、聊天 PID 不变，最终关闭无残留。

测试报告：`runtime/resident-voice-test/report.json`、`bot-integration-report.json`。
旧版一次性语音的 32 秒样本与本次短句内容不同，因此不宣称精确倍数提升。

## 运行与边界

独立基准测试会独占当前推理会话，运行前先停止 Bot，结束后恢复：

```sh
.venv/bin/python scripts/service.py stop
.venv/bin/python scripts/benchmark_resident_voice.py
.venv/bin/python scripts/test_resident_bot_voice.py
.venv/bin/python scripts/service.py start
```

正常 Telegram 使用不需要执行这些命令。通话语音层尚未有电话账号、外呼工具、音频 WebSocket 或完整预订状态机，不能在 Telegram 声称已经替用户打电话。拨号必须等电话线路接入后，再用用户指定测试号码验证。

软件回归：79 项测试通过，覆盖协议取消与恢复、跨进程锁、常驻/释放、长文本分段、VAD 断句、插话缓冲清空、语言路由及现有功能。
