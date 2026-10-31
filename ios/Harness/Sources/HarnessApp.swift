import SwiftUI

@main
@MainActor
struct HarnessApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var store = AppStore()
    @StateObject private var voice = VoiceController()
    var body: some Scene {
        WindowGroup {
            RootView().environmentObject(store).environmentObject(voice)
                .tint(.indigo)
        }
    }
}

@MainActor
struct RootView: View {
    @EnvironmentObject private var store: AppStore
    @EnvironmentObject private var voice: VoiceController
    @Environment(\.scenePhase) private var phase
    private var syncIdentity: String { "\(store.authGeneration.uuidString)-\(phase == .active)" }
    var body: some View {
        Group {
            if store.credentials == nil { PairingView() }
            else {
                TabView(selection: $store.tab) {
                    NavigationStack(path: $store.sessionPath) { SessionsView() }.tabItem { Label("会话", systemImage: "bubble.left.and.bubble.right") }.tag(0)
                    NavigationStack { TasksView() }.tabItem { Label("任务", systemImage: "checklist") }.tag(1)
                    NavigationStack { SettingsView() }.tabItem { Label("设置", systemImage: "gearshape") }.tag(2)
                }
                .safeAreaInset(edge: .top, spacing: 0) {
                    if voice.isActive { VoiceBanner() }
                }
            }
        }
        .task(id: syncIdentity) { if phase == .active { await PushNotifications.registerIfAuthorized(); await store.openPendingNotification(); await store.poll() } }
        .onReceive(NotificationCenter.default.publisher(for: .harnessNotificationRoute)) { _ in Task { await store.openPendingNotification() } }
        .onReceive(NotificationCenter.default.publisher(for: .harnessPushToken)) { event in
            if let token = event.object as? String { Task { await PushNotifications.sync(token: token, store: store) } }
        }
        .alert("需要留意", isPresented: Binding(get: { store.error != nil || voice.error != nil }, set: { if !$0 { store.error = nil; voice.error = nil } })) {
            Button("知道了") { store.error = nil; voice.error = nil }
        } message: { Text(store.error ?? voice.error ?? "") }
    }
}

@MainActor
struct PairingView: View {
    @EnvironmentObject private var store: AppStore
    @State private var server = ""
    @State private var code = ""
    var body: some View {
        NavigationStack {
            Form {
                Section {
                    VStack(alignment: .leading, spacing: 16) {
                        Image("FridayLogo").resizable().scaledToFit().frame(width: 72, height: 72)
                            .clipShape(RoundedRectangle(cornerRadius: 16)).accessibilityLabel("Friday AI 助理")
                        Text("你的助手，\n各自独立的工作空间。").font(.largeTitle.bold())
                        Text("连接 Mac 上的 Harness，随时继续对话、处理任务和语音交流。").foregroundStyle(.secondary)
                    }.padding(.vertical, 20)
                }.listRowBackground(Color.clear)
                Section("配对这台 iPhone") {
                    TextField("服务地址 · https://…", text: $server).keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                    SecureField("一次性邀请码", text: $code).textInputAutocapitalization(.never).autocorrectionDisabled()
                    Button { Task { await store.pair(baseURL: server, code: code) } } label: {
                        HStack { Text("连接 Harness"); Spacer(); if store.isBusy { ProgressView() } else { Image(systemName: "arrow.right") } }
                    }.disabled(store.isBusy || code.isEmpty || server.isEmpty)
                }
                Section { Text("从 Mac 管理端取得服务地址与邀请码。设备凭据保存在 iPhone 钥匙串中。").font(.footnote).foregroundStyle(.secondary) }
            }.navigationTitle("Harness").navigationBarTitleDisplayMode(.inline)
        }
    }
}

@MainActor
struct VoiceBanner: View {
    @EnvironmentObject private var voice: VoiceController
    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "waveform").symbolEffect(.variableColor.iterative, options: .repeating).foregroundStyle(.indigo)
            VStack(alignment: .leading, spacing: 2) {
                Text("通话中 · \(voice.sessionName)").font(.subheadline.bold()).lineLimit(1)
                Text(voice.state).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Button { Task { await voice.interrupt() } } label: { Image(systemName: "speaker.slash.fill").padding(8) }.accessibilityLabel("停止播报，继续聆听")
            Button(role: .destructive) { Task { await voice.stop() } } label: { Image(systemName: "phone.down.fill").padding(8) }.accessibilityLabel("结束语音")
        }.padding(.horizontal).padding(.vertical, 8).background(.regularMaterial)
    }
}
