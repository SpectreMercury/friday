import AVFoundation
import SwiftUI

@MainActor
final class VoiceMessageRecorder: NSObject, ObservableObject, AVAudioRecorderDelegate {
    enum State { case idle, requestingPermission, recording, ready }
    @Published private(set) var state: State = .idle
    @Published private(set) var elapsed: TimeInterval = 0
    @Published private(set) var draft: VoiceMessageDraft?
    @Published var error: String?
    private var recorder: AVAudioRecorder?
    private var timer: Task<Void, Never>?
    private var directory: URL?
    private var generation = UUID()
    private var microphoneLease: UUID?
    private var interruptionObserver: NSObjectProtocol?
    private var routeObserver: NSObjectProtocol?
    let sessionID: String

    init(sessionID: String) { self.sessionID = sessionID; super.init() }
    var isRecording: Bool { state == .recording }
    var isPreparing: Bool { state == .requestingPermission }
    var timeLabel: String { String(format: "%02d:%02d", Int(elapsed) / 60, Int(elapsed) % 60) }

    func start(allowed: @escaping @MainActor () -> Bool) async {
        guard state != .recording, state != .requestingPermission else { return }
        guard allowed() else { error = "请先结束实时通话，再录制语音留言。"; return }
        discard()
        let instance = UUID(); generation = instance
        guard MicrophoneLease.claim(instance) else { error = "麦克风正在使用，请先结束实时通话。"; return }
        microphoneLease = instance; state = .requestingPermission
        let authorized = await withCheckedContinuation { continuation in
            AVAudioApplication.requestRecordPermission(completionHandler: { continuation.resume(returning: $0) })
        }
        guard generation == instance else { return }
        guard authorized, allowed() else {
            discard(); error = authorized ? "当前不能录音，请先结束实时通话。" : "请在 iPhone 设置中允许 Harness 使用麦克风。"; return
        }
        do {
            let parent = VoiceMessageDraft.temporaryDirectory
            let directory = parent.appendingPathComponent(instance.uuidString, isDirectory: true)
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                attributes: [.protectionKey: FileProtectionType.complete])
            self.directory = directory
            let file = directory.appendingPathComponent("message.m4a")
            let audio = AVAudioSession.sharedInstance()
            try audio.setCategory(.record, mode: .default, options: [.allowBluetoothHFP])
            try audio.setActive(true)
            let settings: [String: Any] = [
                AVFormatIDKey: kAudioFormatMPEG4AAC,
                AVSampleRateKey: 24000.0,
                AVNumberOfChannelsKey: 1,
                AVEncoderBitRateKey: 64000,
                AVEncoderAudioQualityKey: AVAudioQuality.medium.rawValue,
            ]
            let recorder = try AVAudioRecorder(url: file, settings: settings)
            recorder.delegate = self; self.recorder = recorder
            guard recorder.prepareToRecord(), recorder.record(forDuration: 180) else { throw APIError(status: 0, message: "无法开始录音，请检查麦克风。") }
            state = .recording; elapsed = 0
            interruptionObserver = NotificationCenter.default.addObserver(forName: AVAudioSession.interruptionNotification, object: nil, queue: .main) { [weak self] notification in
                guard let type = notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt, type == AVAudioSession.InterruptionType.began.rawValue else { return }
                Task { @MainActor in
                    guard let self, self.generation == instance else { return }
                    self.discard(); self.error = "录音被系统打断，已停止采音并清除草稿，请重新录制。"
                }
            }
            routeObserver = NotificationCenter.default.addObserver(forName: AVAudioSession.routeChangeNotification, object: nil, queue: .main) { [weak self] notification in
                guard let reason = notification.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt, reason == AVAudioSession.RouteChangeReason.oldDeviceUnavailable.rawValue else { return }
                Task { @MainActor in
                    guard let self, self.generation == instance else { return }
                    self.discard(); self.error = "录音设备已断开，已停止采音并清除草稿。"
                }
            }
            timer = Task { [weak self] in
                while !Task.isCancelled {
                    do { try await Task.sleep(for: .milliseconds(200)) } catch { return }
                    guard let self, self.generation == instance, self.state == .recording, let recorder = self.recorder else { return }
                    self.elapsed = min(recorder.currentTime, 180)
                    let size = (try? file.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0
                    if size > VoiceMessageDraft.maxBytes {
                        self.discard(); self.error = "录音超过 20 MB，已停止并清除，请缩短留言。"; return
                    }
                    if self.elapsed >= 180 { self.stop(); return }
                }
            }
        } catch { discard(); self.error = error.localizedDescription }
    }
    func stop() {
        guard state == .recording, let recorder else { return }
        elapsed = min(max(elapsed, recorder.currentTime), 180)
        recorder.stop(); finish(recorder, successful: true)
    }
    func discard() {
        generation = UUID()
        timer?.cancel(); timer = nil
        recorder?.delegate = nil; recorder?.stop(); recorder = nil
        releaseAudio()
        if let directory { try? FileManager.default.removeItem(at: directory) }
        directory = nil; draft = nil; elapsed = 0; state = .idle; error = nil
    }
    func interruptForLeaving() {
        guard isRecording || isPreparing else { return }
        discard(); error = "录音已停止，草稿已清除。返回后可重新开始。"
    }
    private func finish(_ source: AVAudioRecorder, successful: Bool) {
        guard recorder === source, state == .recording else { return }
        timer?.cancel(); timer = nil
        source.delegate = nil; recorder = nil; releaseAudio()
        guard successful else { discard(); error = "录音未完成，请重新录制。"; return }
        do {
            let audioFile = try AVAudioFile(forReading: source.url)
            let duration = Double(audioFile.length) / audioFile.processingFormat.sampleRate
            guard duration.isFinite, duration >= 0.2, duration <= 180.5 else { throw APIError(status: 0, message: "录音过短或超过 3 分钟，请重新录制。") }
            elapsed = min(duration, 180)
            let draft = VoiceMessageDraft(sessionID: sessionID, file: source.url)
            try draft.validateFile()
            self.draft = draft; state = .ready
        } catch { discard(); self.error = error.localizedDescription }
    }
    private func releaseAudio() {
        if let observer = interruptionObserver { NotificationCenter.default.removeObserver(observer); interruptionObserver = nil }
        if let observer = routeObserver { NotificationCenter.default.removeObserver(observer); routeObserver = nil }
        if let microphoneLease {
            try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
            MicrophoneLease.release(microphoneLease); self.microphoneLease = nil
        }
    }
    nonisolated func audioRecorderDidFinishRecording(_ recorder: AVAudioRecorder, successfully flag: Bool) {
        Task { @MainActor [weak self] in self?.finish(recorder, successful: flag) }
    }
    nonisolated func audioRecorderEncodeErrorDidOccur(_ recorder: AVAudioRecorder, error: Error?) {
        Task { @MainActor [weak self] in
            guard let self, self.recorder === recorder else { return }
            self.discard(); self.error = "录音编码失败，草稿已清除，请重试。"
        }
    }
}

@MainActor
struct VoiceMessageView: View {
    @EnvironmentObject private var store: AppStore
    @EnvironmentObject private var voice: VoiceController
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var phase
    @StateObject private var recorder: VoiceMessageRecorder
    @State private var uploadTask: Task<Void, Never>?
    @State private var uploading = false
    @State private var appeared = true
    let sessionName: String
    let deviceID: String
    init(sessionID: String, sessionName: String, deviceID: String) {
        _recorder = StateObject(wrappedValue: VoiceMessageRecorder(sessionID: sessionID))
        self.sessionName = sessionName; self.deviceID = deviceID
    }
    private var canRecord: Bool { !voice.isActive && !voice.isStarting && store.credentials?.pairing.deviceId == deviceID }
    var body: some View {
        NavigationStack {
            VStack(spacing: 24) {
                VStack(spacing: 6) {
                    Text("发送到「\(sessionName)」").font(.headline)
                    Text("录音最长 3 分钟。发送后由 Mac 转录并执行，任务保留在此会话。").font(.subheadline).foregroundStyle(.secondary).multilineTextAlignment(.center)
                }
                Image(systemName: recorder.isRecording ? "mic.circle.fill" : "waveform.circle")
                    .font(.system(size: 80)).foregroundStyle(recorder.isRecording ? Color.red : Color.indigo)
                    .accessibilityLabel(recorder.isRecording ? "正在录音" : "语音留言")
                Text(recorder.timeLabel).font(.system(.largeTitle, design: .monospaced)).monospacedDigit()
                if voice.isActive || voice.isStarting {
                    Label("请先结束实时通话，再录制留言。", systemImage: "phone.fill").font(.subheadline).foregroundStyle(.orange)
                }
                if let error = recorder.error { Text(error).font(.subheadline).foregroundStyle(.red).multilineTextAlignment(.center) }
                if recorder.isRecording {
                    Button("停止录音", systemImage: "stop.fill") { recorder.stop() }.buttonStyle(.borderedProminent).tint(.red)
                } else if recorder.draft != nil {
                    Button {
                        send()
                    } label: { HStack { if uploading { ProgressView() }; Label(uploading ? "正在发送…" : "发送语音留言", systemImage: "arrow.up.circle.fill") } }
                    .buttonStyle(.borderedProminent).disabled(uploading)
                    Button("重新录制", role: .destructive) { Task { await start() } }.disabled(uploading || !canRecord)
                    Text(uploading ? "关闭页面会停止本机上传；服务器已收到的任务仍可能继续，可在任务列表核对。" : "发送失败时可直接重试，同一段录音不会重复创建任务。").font(.caption).foregroundStyle(.secondary).multilineTextAlignment(.center)
                } else {
                    Button(recorder.isPreparing ? "等待麦克风授权…" : "开始录音", systemImage: "mic.fill") { Task { await start() } }
                        .buttonStyle(.borderedProminent).disabled(recorder.isPreparing || !canRecord)
                }
                Spacer(minLength: 0)
            }
            .padding(24).navigationTitle("语音留言").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("取消") { leave(); dismiss() } } }
        }
        .onAppear { appeared = true }
        .onDisappear { leave() }
        .onChange(of: phase) { _, next in
            // Permission prompts make the app inactive; only interrupt an actual capture in that case.
            if next == .background { leave(); dismiss() }
            else if next == .inactive && recorder.isRecording { recorder.interruptForLeaving() }
        }
        .onChange(of: voice.isActive) { _, active in if active { recorder.interruptForLeaving() } }
        .onChange(of: store.credentials?.pairing.deviceId) { _, current in if current != deviceID { leave(); dismiss() } }
    }
    private func start() async {
        guard appeared else { return }
        await recorder.start { appeared && canRecord }
    }
    private func send() {
        guard !uploading, let draft = recorder.draft, let api = store.api, store.credentials?.pairing.deviceId == deviceID else { return }
        uploading = true; recorder.error = nil
        uploadTask = Task {
            defer { uploading = false }
            do {
                let _: Job = try await api.sendVoiceMessage(draft)
                guard !Task.isCancelled, appeared, store.credentials?.pairing.deviceId == deviceID else { return }
                recorder.discard(); await store.refresh(); store.eventVersion += 1; dismiss()
            } catch is CancellationError {} catch {
                guard appeared, !Task.isCancelled else { return }
                recorder.error = "发送未确认：\(error.localizedDescription) 可使用同一录音重试，或在任务列表核对是否已收到。"
            }
        }
    }
    private func leave() {
        appeared = false; uploadTask?.cancel(); uploadTask = nil; recorder.discard()
    }
}
