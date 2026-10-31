import AVFoundation
import Foundation
import SwiftUI

@MainActor
final class VoiceController: ObservableObject {
    @Published private(set) var sessionID: String?
    @Published private(set) var sessionName = ""
    @Published private(set) var state = "未开始"
    @Published private(set) var transcript = ""
    @Published private(set) var isStarting = false
    @Published var error: String?
    private var lease: VoiceLease?
    private var api: APIClient?
    private var socket: URLSessionWebSocketTask?
    private var receiveTask: Task<Void, Never>?
    private var sendTask: Task<Void, Never>?
    private var generation = UUID()
    private var microphoneLease: UUID?
    private var packetGate: VoicePacketGate?
    private var audio: VoiceAudio?
    private var audioStream: AsyncStream<Data>.Continuation?
    private var observers: [NSObjectProtocol] = []
    var isActive: Bool { sessionID != nil }

    func start(session: Workspace, api: APIClient) async {
        guard !isStarting, !isActive else { return }
        let instance = UUID(); generation = instance
        guard MicrophoneLease.claim(instance) else { error = "麦克风正在录制语音留言，请先停止录音。"; return }
        microphoneLease = instance
        isStarting = true; error = nil
        defer {
            isStarting = false
            if sessionID == nil, let microphoneLease { MicrophoneLease.release(microphoneLease); self.microphoneLease = nil }
        }
        let allowed = await withCheckedContinuation { continuation in
            AVAudioApplication.requestRecordPermission(completionHandler: { continuation.resume(returning: $0) })
        }
        guard generation == instance else { return }
        guard allowed else { error = "请在 iPhone 设置中允许 Harness 使用麦克风。"; return }
        sessionID = session.id; sessionName = session.name; transcript = ""; state = "正在预热本地语音"
        do {
            let lease: VoiceLease = try await api.value("/v1/sessions/\(APIClient.segment(session.id))/voice", method: "POST")
            guard generation == instance else { try? await api.perform("/v1/voice/\(APIClient.segment(lease.id))", method: "DELETE"); return }
            self.api = api; self.lease = lease
            packetGate = VoicePacketGate(sessionID: session.id, epoch: lease.epoch ?? 0)
            sessionID = session.id; sessionName = session.name; transcript = ""; state = "连接中"
            var request = try api.request(lease.streamPath ?? "/v1/voice/\(APIClient.segment(lease.id))/stream")
            var url = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!
            url.scheme = url.scheme == "https" ? "wss" : "ws"; request.url = url.url
            let socket = URLSession.shared.webSocketTask(with: request); self.socket = socket; socket.resume()
            let (stream, continuation) = AsyncStream<Data>.makeStream(bufferingPolicy: .bufferingNewest(8)); audioStream = continuation
            let voiceAudio = try VoiceAudio { [weak self] data in
                if case .dropped = continuation.yield(data) {
                    Task { @MainActor [weak self] in
                        guard self?.generation == instance else { return }
                        self?.error = "音频上传持续阻塞，通话已结束。请检查网络后重试。"; await self?.stop()
                    }
                }
            }
            audio = voiceAudio
            sendTask = Task { [weak self] in
                do {
                    for await bytes in stream {
                        guard !Task.isCancelled, self?.generation == instance else { return }
                        try await socket.send(.data(bytes))
                    }
                } catch { await self?.failed(error, generation: instance) }
            }
            receiveTask = Task { [weak self] in
                do {
                    while !Task.isCancelled {
                        let message = try await socket.receive()
                        guard let self, self.generation == instance else { return }
                        try self.receive(message, generation: instance)
                    }
                } catch { await self?.failed(error, generation: instance) }
            }
            try voiceAudio.start(); observeInterruptions(instance)
        } catch { guard generation == instance else { return }; self.error = error.localizedDescription; await stop() }
    }
    func stop() async {
        generation = UUID()
        audioStream?.finish(); audioStream = nil
        sendTask?.cancel(); receiveTask?.cancel(); sendTask = nil; receiveTask = nil
        audio?.stop(); audio = nil
        if let microphoneLease {
            try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
            MicrophoneLease.release(microphoneLease); self.microphoneLease = nil
        }
        socket?.cancel(with: .normalClosure, reason: nil); socket = nil
        observers.forEach(NotificationCenter.default.removeObserver); observers = []
        let oldLease = lease; let oldAPI = api
        lease = nil; api = nil; packetGate = nil; sessionID = nil; sessionName = ""; state = "未开始"
        if let oldLease, let oldAPI { try? await oldAPI.perform("/v1/voice/\(APIClient.segment(oldLease.id))", method: "DELETE") }
    }
    func interrupt() async {
        audio?.clearPlayback(); packetGate?.interrupt(); state = "正在听"
        guard let socket else { return }
        let message = "{\"type\":\"interrupt\",\"epoch\":\(packetGate?.epoch ?? 0)}"
        do { try await socket.send(.string(message)) } catch { self.error = error.localizedDescription; await stop() }
    }
    private func receive(_ message: URLSessionWebSocketTask.Message, generation: UUID) throws {
        switch message {
        case .data(let data):
            guard packetGate?.canPlayPacket == true else { return }
            try audio?.play(data)
        case .string(let text):
            let event = try APIClient.decoder.decode(VoiceEvent.self, from: Data(text.utf8))
            guard packetGate?.accept(event) == true else { return }
            switch event.type {
            case "ready": state = "正在听"
            case "recognized": transcript = event.text ?? ""
            case "state":
                state = ["listening": "正在听", "thinking": "正在思考", "speaking": "正在回答"][event.state ?? ""] ?? "连接中"
            case "clear_playback": audio?.clearPlayback()
            case "audio_format":
                guard event.channels == nil || event.channels == 1, event.encoding == nil || event.encoding == "pcm_s16le", let rate = event.sampleRate, (8000...96000).contains(rate) else {
                    throw APIError(status: 0, message: "服务端返回了不支持的音频格式。")
                }
                audio?.outputSampleRate = Double(rate)
            case "error": throw APIError(status: 0, message: event.message ?? "语音连接出现错误。")
            default: break
            }
        @unknown default: break
        }
    }
    private func failed(_ error: Error, generation instance: UUID) async {
        guard generation == instance, !Task.isCancelled else { return }
        self.error = "语音连接已结束：\(error.localizedDescription)"; await stop()
    }
    private func observeInterruptions(_ instance: UUID) {
        observers.append(NotificationCenter.default.addObserver(forName: AVAudioSession.interruptionNotification, object: nil, queue: .main) { [weak self] notification in
            guard let type = notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt, type == AVAudioSession.InterruptionType.began.rawValue else { return }
            Task { @MainActor in guard self?.generation == instance else { return }; self?.error = "系统音频被打断，通话已结束。任务会继续执行。"; await self?.stop() }
        })
        observers.append(NotificationCenter.default.addObserver(forName: AVAudioSession.routeChangeNotification, object: nil, queue: .main) { [weak self] notification in
            guard let reason = notification.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt, reason == AVAudioSession.RouteChangeReason.oldDeviceUnavailable.rawValue else { return }
            Task { @MainActor in guard self?.generation == instance else { return }; self?.error = "音频设备已断开，请重新开始通话。"; await self?.stop() }
        })
    }
}

// The tap converts only the current input buffer and never accesses UI state.
final class MicrophoneConverter: @unchecked Sendable {
    let converter: AVAudioConverter
    let outputFormat: AVAudioFormat
    let output: @Sendable (Data) -> Void
    init(input: AVAudioFormat, output: @escaping @Sendable (Data) -> Void) throws {
        guard let format = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000, channels: 1, interleaved: false), let converter = AVAudioConverter(from: input, to: format) else { throw APIError(status: 0, message: "无法初始化麦克风音频转换。") }
        self.outputFormat = format; self.converter = converter; self.output = output
    }
    func accept(_ input: AVAudioPCMBuffer) {
        let capacity = AVAudioFrameCount(Double(input.frameLength) * 16000 / input.format.sampleRate + 64)
        guard let buffer = AVAudioPCMBuffer(pcmFormat: outputFormat, frameCapacity: capacity) else { return }
        var supplied = false; var conversionError: NSError?
        converter.convert(to: buffer, error: &conversionError) { _, status in
            if supplied { status.pointee = .noDataNow; return nil }
            supplied = true; status.pointee = .haveData; return input
        }
        guard conversionError == nil, buffer.frameLength > 0, let pointer = buffer.int16ChannelData?[0] else { return }
        let bytes = Data(bytes: pointer, count: Int(buffer.frameLength) * 2)
        // Server limits mic frames to 32,000 bytes; usually each tap is under 1,500 bytes.
        for offset in stride(from: 0, to: bytes.count, by: 32000) { output(bytes.subdata(in: offset..<min(bytes.count, offset + 32000))) }
    }
}

@MainActor
final class VoiceAudio {
    private let engine = AVAudioEngine()
    private let player = AVAudioPlayerNode()
    private let playbackFormat = AVAudioFormat(standardFormatWithSampleRate: 48000, channels: 1)!
    private var microphone: MicrophoneConverter?
    private var playbackConverter: AVAudioConverter?
    private var converterRate = 0.0
    private var pendingFrames: AVAudioFrameCount = 0
    private var playbackGeneration = UUID()
    private var tapInstalled = false
    var outputSampleRate = 24000.0
    init(output: @escaping @Sendable (Data) -> Void) throws {
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playAndRecord, mode: .voiceChat, options: [.defaultToSpeaker, .allowBluetoothHFP])
        try session.setPreferredSampleRate(48000); try session.setPreferredIOBufferDuration(0.02); try session.setActive(true)
        try engine.inputNode.setVoiceProcessingEnabled(true)
        engine.attach(player); engine.connect(player, to: engine.mainMixerNode, format: playbackFormat)
        let input = engine.inputNode.outputFormat(forBus: 0)
        guard input.sampleRate > 0, input.channelCount > 0 else { throw APIError(status: 0, message: "麦克风不可用。") }
        let converter = try MicrophoneConverter(input: input, output: output); microphone = converter
        engine.inputNode.installTap(onBus: 0, bufferSize: 2048, format: input) { buffer, _ in converter.accept(buffer) }
        tapInstalled = true
    }
    func start() throws { engine.prepare(); try engine.start(); player.play() }
    func play(_ bytes: Data) throws {
        guard !bytes.isEmpty, bytes.count.isMultiple(of: 2), bytes.count <= 192000 else { throw APIError(status: 0, message: "收到无效音频帧。") }
        guard let source = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: outputSampleRate, channels: 1, interleaved: false),
              let input = AVAudioPCMBuffer(pcmFormat: source, frameCapacity: AVAudioFrameCount(bytes.count / 2)), let pointer = input.int16ChannelData?[0] else { return }
        input.frameLength = input.frameCapacity
        bytes.copyBytes(to: UnsafeMutableRawBufferPointer(start: pointer, count: bytes.count))
        if converterRate != outputSampleRate {
            playbackConverter = AVAudioConverter(from: source, to: playbackFormat); converterRate = outputSampleRate
        }
        guard let converter = playbackConverter,
              let output = AVAudioPCMBuffer(pcmFormat: playbackFormat, frameCapacity: AVAudioFrameCount(Double(input.frameLength) * 48000 / outputSampleRate + 64)) else { return }
        var supplied = false; var error: NSError?
        converter.convert(to: output, error: &error) { _, status in
            if supplied { status.pointee = .noDataNow; return nil }
            supplied = true; status.pointee = .haveData; return input
        }
        if let error { throw error }
        guard output.frameLength > 0 else { return }
        // Bounded playback prevents stale multi-minute output after a slow device/network.
        guard pendingFrames + output.frameLength <= 48000 * 8 else { throw APIError(status: 0, message: "播放缓冲已满，通话已结束。") }
        pendingFrames += output.frameLength
        let frames = output.frameLength; let instance = playbackGeneration
        player.scheduleBuffer(output, completionCallbackType: .dataPlayedBack) { [weak self] _ in
            Task { @MainActor in
                guard let self, self.playbackGeneration == instance else { return }
                self.pendingFrames = self.pendingFrames >= frames ? self.pendingFrames - frames : 0
            }
        }
        if !player.isPlaying { player.play() }
    }
    func clearPlayback() {
        playbackGeneration = UUID(); player.stop(); pendingFrames = 0; playbackConverter?.reset()
        if engine.isRunning { player.play() }
    }
    func stop() {
        clearPlayback(); player.stop()
        if tapInstalled { engine.inputNode.removeTap(onBus: 0); tapInstalled = false }
        engine.stop(); microphone = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }
}
