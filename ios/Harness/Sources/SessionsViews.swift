	import SwiftUI
import UniformTypeIdentifiers
import QuickLook

@MainActor
struct SessionsView: View {
    @EnvironmentObject private var store: AppStore
    @State private var showArchived = false
    @State private var showingCreate = false
    @State private var name = ""
    private var visible: [Workspace] { store.sessions.filter { $0.archived == showArchived } }
    var body: some View {
        List {
            Section {
                Text("把不同工作放进不同会话。技能、文件与上下文分别保存。").font(.subheadline).foregroundStyle(.secondary)
            }
            if visible.isEmpty {
                ContentUnavailableView(showArchived ? "没有归档会话" : "开启一个工作空间", systemImage: "bubble.left.and.bubble.right", description: Text("每个会话都从空白上下文开始。"))
            }
            ForEach(visible) { session in
                NavigationLink(value: session.id) {
                    HStack(spacing: 12) {
                        Image(systemName: session.management ? "desktopcomputer" : "square.stack.3d.up").foregroundStyle(.indigo).font(.title2).frame(width: 32)
                        VStack(alignment: .leading, spacing: 5) {
                            Text(session.name).font(.headline)
                            Text(session.skillNames.isEmpty ? "尚未启用技能" : session.skillNames.joined(separator: " · ")).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                        }
                        Spacer()
                        if store.tasks.contains(where: { $0.sessionId == session.id && $0.isActive }) { ProgressView().controlSize(.small) }
                    }.padding(.vertical, 4)
                }.swipeActions {
                    Button(showArchived ? "恢复" : "归档", systemImage: showArchived ? "tray.and.arrow.up" : "archivebox") {
                        Task { _ = await store.update(session, fields: ["archived": .bool(!session.archived)]) }
                    }.tint(.indigo)
                }
            }
            Section { Label(store.syncLabel, systemImage: "arrow.triangle.2.circlepath").font(.caption).foregroundStyle(.secondary) }
        }
        .navigationTitle(showArchived ? "归档会话" : "会话")
        .navigationDestination(for: String.self) { id in SessionDetailView(sessionID: id) }
        .toolbar {
            ToolbarItem(placement: .topBarLeading) { Button(showArchived ? "返回会话" : "归档", systemImage: "archivebox") { showArchived.toggle() } }
            ToolbarItem(placement: .topBarTrailing) { Button("新建会话", systemImage: "plus") { showingCreate = true } }
        }
        .alert("新建会话", isPresented: $showingCreate) {
            TextField("名称，例如：工作研究", text: $name)
            Button("取消", role: .cancel) { name = "" }
            Button("创建") { let value = name.trimmingCharacters(in: .whitespacesAndNewlines); name = ""; Task { _ = await store.createSession(name: value.isEmpty ? "新会话" : value) } }
        } message: { Text("新会话从空白开始，独立选择技能和可访问资源。") }
        .refreshable { await store.refresh() }
    }
}

@MainActor
struct SessionDetailView: View {
    @EnvironmentObject private var store: AppStore
    @EnvironmentObject private var voice: VoiceController
    let sessionID: String
    @State private var messages: [Job] = []
    @State private var files: [WorkspaceFile] = []
    @State private var approvals: [Approval] = []
    @State private var sending = false
    @State private var loading = true
    @State private var settings = false
    @State private var fork = false
    @State private var importFile = false
    @State private var showFiles = false
    @State private var showVoiceMessage = false
    @State private var switchVoice = false
    @State private var preview: URL?
    var session: Workspace? { store.sessions.first { $0.id == sessionID } }
    private var draft: Binding<String> { Binding(get: { store.drafts[sessionID] ?? "" }, set: { store.drafts[sessionID] = $0 }) }
    private var base: String { "/v1/sessions/\(APIClient.segment(sessionID))" }
    var body: some View {
        VStack(spacing: 0) {
            if session?.archived == true { Label("此会话已归档，已有任务仍会继续", systemImage: "archivebox").font(.caption).padding(8).frame(maxWidth: .infinity).background(.quaternary) }
            ScrollViewReader { reader in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 20) {
                        if messages.isEmpty && !loading {
                            ContentUnavailableView("开始这段对话", systemImage: "sparkles", description: Text("这里的任务、资料和技能只属于这个会话。"))
                        }
                        ForEach(approvals.filter { $0.status == "pending" }) { approval in approvalCard(approval) }
                        ForEach(messages) { job in MessageCard(job: job, preview: $preview).id(job.id) }
                        if voice.sessionID == sessionID, !voice.transcript.isEmpty {
                            Label(voice.transcript, systemImage: "waveform").font(.subheadline).foregroundStyle(.secondary).padding()
                        }
                        Color.clear.frame(height: 1).id("bottom")
                    }.padding()
                }
                .refreshable { await load() }
                .onChange(of: messages.count) { _, _ in withAnimation { reader.scrollTo("bottom", anchor: .bottom) } }
            }
            composer
        }
        .navigationTitle(session?.name ?? "会话")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Button("技能与可访问资源", systemImage: "slider.horizontal.3") { settings = true }
                    Button("会话文件（\(files.count)）", systemImage: "folder") { showFiles = true }
                    Button("从这里新建会话", systemImage: "arrow.triangle.branch") { fork = true }
                } label: { Image(systemName: "ellipsis.circle") }
            }
        }
        .task(id: store.eventVersion) { await load() }
        .sheet(isPresented: $settings) { if let session { SessionSettingsView(session: session) } }
        .sheet(isPresented: $fork) { if let session { ForkSessionView(session: session, messages: messages, files: files) } }
        .sheet(isPresented: $showFiles) { SessionFilesView(sessionID: sessionID, files: files) }
        .sheet(isPresented: $showVoiceMessage) {
            if let deviceID = store.credentials?.pairing.deviceId {
                VoiceMessageView(sessionID: sessionID, sessionName: session?.name ?? "原会话", deviceID: deviceID)
            }
        }
        .quickLookPreview($preview)
        .fileImporter(isPresented: $importFile, allowedContentTypes: [.item]) { result in
            Task {
                do { guard let api = store.api else { return }; try await api.upload(result.get(), sessionID: sessionID); await load() }
                catch { store.error = error.localizedDescription }
            }
        }
        .confirmationDialog("切换语音到此会话？", isPresented: $switchVoice, titleVisibility: .visible) {
            Button("结束原通话并开始") { Task { await voice.stop(); if let session, let api = store.api { await voice.start(session: session, api: api) } } }
        } message: { Text("当前通话绑定在「\(voice.sessionName)」。已有任务会留在原会话继续执行。") }
    }
    private var composer: some View {
        HStack(alignment: .bottom, spacing: 10) {
            Menu {
                Button("添加文件", systemImage: "doc.badge.plus") { importFile = true }
                Button("录制语音留言", systemImage: "mic.badge.plus") { showVoiceMessage = true }
            } label: { Image(systemName: "plus.circle").font(.title2) }.accessibilityLabel("添加文件或语音留言").padding(.bottom, 7)
            TextField("输入要求或补充任务…", text: draft, axis: .vertical).lineLimit(1...6).padding(10).background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 16))
            if !(store.drafts[sessionID] ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                Button {
                    let text = (store.drafts[sessionID] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                    sending = true
                    Task { _ = await store.send(text, to: sessionID); sending = false; await load() }
                } label: { if sending { ProgressView() } else { Image(systemName: "arrow.up.circle.fill").font(.title) } }
                .disabled(sending).accessibilityLabel("发送").padding(.bottom, 5)
            } else {
                Button {
                    if voice.sessionID == sessionID { Task { await voice.stop() } }
                    else if voice.isActive { switchVoice = true }
                    else if let session, let api = store.api { Task { await voice.start(session: session, api: api) } }
                } label: { Image(systemName: voice.sessionID == sessionID ? "phone.down.circle.fill" : "waveform.circle.fill").font(.title).foregroundStyle(voice.sessionID == sessionID ? .red : .indigo) }
                .accessibilityLabel(voice.sessionID == sessionID ? "结束语音" : "开始语音").disabled(voice.isStarting).padding(.bottom, 5)
            }
        }.padding(.horizontal).padding(.vertical, 10).background(.bar)
    }
    private func load() async {
        guard let api = store.api else { return }
        do {
            async let jobs: Items<Job> = api.value(base + "/messages")
            async let items: Items<WorkspaceFile> = api.value(base + "/files")
            async let pending: Items<Approval> = api.value(base + "/approvals")
            let (j, f, a) = try await (jobs, items, pending); messages = j.items; files = f.items; approvals = a.items
        } catch is CancellationError {} catch { store.error = error.localizedDescription }
        loading = false
    }
    private func approvalCard(_ approval: Approval) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("等待你的决定", systemImage: "hand.raised.fill").font(.headline).foregroundStyle(.orange)
            Text(approval.description).textSelection(.enabled)
            if let scope = approval.scope { Text(scope).font(.caption).foregroundStyle(.secondary) }
            HStack {
                Button("拒绝", role: .destructive) { decide(approval, "denied") }.buttonStyle(.bordered)
                Button("允许这次操作") { decide(approval, "approved") }.buttonStyle(.borderedProminent)
            }
        }.padding().frame(maxWidth: .infinity, alignment: .leading).background(Color.orange.opacity(0.09), in: RoundedRectangle(cornerRadius: 18))
    }
    private func decide(_ approval: Approval, _ decision: String) {
        Task {
            do { try await store.api?.perform(base + "/approvals/\(APIClient.segment(approval.token))", body: ["decision": .string(decision)]); await load() }
            catch { store.error = error.localizedDescription }
        }
    }
}

@MainActor
struct MessageCard: View {
    @EnvironmentObject private var store: AppStore
    let job: Job
    @Binding var preview: URL?
    @State private var downloading = false
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let prompt = job.prompt, !prompt.isEmpty {
                HStack { Spacer(minLength: 24); Text(prompt).textSelection(.enabled).padding(14).background(.indigo.opacity(0.10), in: RoundedRectangle(cornerRadius: 18)) }
            }
            if !job.resultText.isEmpty { Text(job.resultText).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }
            if let error = job.error, !error.isEmpty { Label(error, systemImage: "exclamationmark.circle").foregroundStyle(.red).font(.subheadline) }
            HStack {
                if job.isActive { ProgressView().controlSize(.mini) }
                Text(job.statusText).font(.caption).foregroundStyle(.secondary)
                if let artifact = job.artifactUrl {
                    Button { Task { await download(artifact) } } label: { Label(downloading ? "下载中" : "查看结果", systemImage: "arrow.down.doc") }.font(.caption).disabled(downloading)
                }
                Spacer()
                if job.canCancel { Button("取消任务", role: .destructive) { Task { await store.cancel(job) } }.font(.caption) }
            }
        }.padding(.vertical, 2)
    }
    private func download(_ path: String) async {
        guard let api = store.api else { return }; downloading = true; defer { downloading = false }
        do {
            let ext = ["image": "png", "video": "mp4", "voice": "wav"][job.kind ?? ""] ?? "dat"
            preview = try await api.download(path, filename: job.artifactFilename ?? "result-\(job.id).\(ext)")
        } catch { store.error = error.localizedDescription }
    }
}

@MainActor
struct SessionFilesView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dismiss) private var dismiss
    let sessionID: String
    let files: [WorkspaceFile]
    @State private var preview: URL?
    var body: some View {
        NavigationStack {
            List {
                if files.isEmpty { ContentUnavailableView("尚无文件", systemImage: "folder", description: Text("在会话中点加号上传附件。")) }
                ForEach(files) { file in
                    Button {
                        Task {
                            do { preview = try await store.api?.download("/v1/sessions/\(APIClient.segment(sessionID))/files/\(APIClient.segment(file.name))", filename: file.name) }
                            catch { store.error = error.localizedDescription }
                        }
                    } label: { HStack { Label(file.name, systemImage: "doc"); Spacer(); Text(file.sizeText).font(.caption).foregroundStyle(.secondary) } }
                }
            }.navigationTitle("会话文件").toolbar { Button("完成") { dismiss() } }.quickLookPreview($preview)
        }
    }
}
