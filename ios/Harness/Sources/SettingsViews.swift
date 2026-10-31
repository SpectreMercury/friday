import SwiftUI

@MainActor
struct SessionSettingsView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL
    @Environment(\.scenePhase) private var phase
    @State private var session: Workspace
    @State private var name: String
    @State private var selected: Set<String>
    @State private var resources: [String: String] = [:]
    @State private var bound: Set<String> = []
    @State private var creationAllowed: Set<String> = []
    @State private var awaitingPicker = false
    @State private var bindingRevision = 0
    @State private var management: Bool
    @State private var busy = false
    @State private var diagnostics: String?
    init(session: Workspace) {
        _session = State(initialValue: session); _name = State(initialValue: session.name)
        _selected = State(initialValue: Set(session.skillNames)); _management = State(initialValue: session.management)
    }
    var body: some View {
        NavigationStack {
            Form {
                Section("这个工作空间") {
                    TextField("会话名称", text: $name)
                    if store.isAdmin { Toggle("允许 Mac 管理工具", isOn: $management) }
                    Button("保存会话设置") { Task { await saveSession() } }
                    if store.isAdmin && session.management {
                        Button("查看 Mac 诊断状态") { Task { await diagnose() } }
                        if let diagnostics { Text(diagnostics).font(.caption.monospaced()).textSelection(.enabled) }
                    }
                }
                Section {
                    if store.skills.isEmpty { Text("Mac 技能库中还没有技能。").foregroundStyle(.secondary) }
                    ForEach(store.skills) { skill in
                        Toggle(isOn: Binding(get: { selected.contains(skill.name) }, set: { if $0 { selected.insert(skill.name) } else { selected.remove(skill.name) } })) {
                            VStack(alignment: .leading, spacing: 4) { Text(skill.name); Text(skill.description).font(.caption).foregroundStyle(.secondary); Text("版本 \(skill.version.prefix(12))").font(.caption2).foregroundStyle(.tertiary) }
                        }
                    }
                    ForEach(Array(selected.subtracting(Set(store.skills.map(\.name)))).sorted(), id: \.self) { missing in
                        Toggle("\(missing)（已从技能库移除）", isOn: Binding(get: { selected.contains(missing) }, set: { if !$0 { selected.remove(missing) } }))
                    }
                    Button("同步技能选择") { Task { await saveSkills() } }
                } header: { Text("启用的 Skills") } footer: { Text("只向当前会话提供已启用技能。新任务使用最新版本，正在执行和恢复的任务保留原快照。") }
                Section {
                    if store.connections.isEmpty { Text("先到「设置」连接 Google 账号，再回到这里选择资源。").foregroundStyle(.secondary) }
                    ForEach(store.connections) { connection in
                        VStack(alignment: .leading, spacing: 8) {
                            Toggle(connection.displayName, isOn: Binding(get: { bound.contains(connection.id) }, set: { if $0 { bound.insert(connection.id) } else { bound.remove(connection.id) } }))
                            if bound.contains(connection.id) {
                                Toggle("允许在此会话创建新文档", isOn: Binding(get: { creationAllowed.contains(connection.id) }, set: { if $0 { creationAllowed.insert(connection.id) } else { creationAllowed.remove(connection.id) } }))
                                TextField("允许的文件或文档 ID，每行一个", text: Binding(get: { resources[connection.id] ?? "" }, set: { resources[connection.id] = $0 }), axis: .vertical)
                                    .font(.callout.monospaced()).lineLimit(2...6).textInputAutocapitalization(.never).autocorrectionDisabled()
                            }
                            if connection.provider == "google" && connection.status == "connected" {
                                Button("从 Google Drive 选择文件", systemImage: "doc.badge.plus") { Task { await pickFiles(connection) } }
                            }
                        }
                    }
                    Button("保存资源范围") { Task { await saveResources() } }.disabled(store.connections.isEmpty)
                } header: { Text("可访问资源") } footer: { Text("账号授权与会话范围分别管理。空的资源列表不授予现有文件访问；使用 Google 文件选择器授予文件访问后，也可编辑具体 ID。同一文档被多个会话明确绑定后，修改会同步到原文档。") }
                Section {
                    Button("重新加载服务端配置") { Task { await reload() } }
                    Button(session.archived ? "恢复此会话" : "归档此会话") {
                        Task { if await store.update(session, fields: ["archived": .bool(!session.archived)]) { dismiss() } }
                    }
                } footer: { Text("配置修订号 \(session.revision)。归档不会取消任务或删除文件。") }
            }
            .disabled(busy)
            .navigationTitle("会话设置").navigationBarTitleDisplayMode(.inline)
            .toolbar { Button("完成") { dismiss() } }
            .task { await loadBindings() }
            .onChange(of: phase) { _, value in
                if value == .active && awaitingPicker { awaitingPicker = false; Task { await reload() } }
            }
        }
    }
    private func pickFiles(_ connection: Connection) async {
        guard let api = store.api else { return }
        do {
            let response: BrowserURL = try await api.value("/v1/connectors/\(APIClient.segment(connection.id))/picker", method: "POST", body: ["session_id": .string(session.id), "revision": .number(Double(bindingRevision))])
            guard response.url.scheme == "https", response.url.host == api.baseURL.host else { throw URLError(.badURL) }
            awaitingPicker = true; openURL(response.url)
        } catch { store.error = error.localizedDescription }
    }
    private func diagnose() async {
        guard let api = store.api else { return }
        do { let result: JSONValue = try await api.value("/v1/sessions/\(APIClient.segment(session.id))/host/status"); diagnostics = result.displayText }
        catch { store.error = error.localizedDescription }
    }
    private func saveSession() async {
        busy = true; defer { busy = false }
        let newName = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !newName.isEmpty else { store.error = "会话名称不能为空。"; return }
        if await store.update(session, fields: ["name": .string(newName), "management": .bool(management)]) { await reload() }
    }
    private func saveSkills() async {
        busy = true; defer { busy = false }
        if await store.setSkills(selected, session: session) { await reload() }
    }
    private func loadBindings() async {
        guard let api = store.api else { return }
        do {
            let result: ConnectorBindings = try await api.value("/v1/sessions/\(APIClient.segment(session.id))/connectors")
            bound = Set(result.items.map(\.connectionId)); resources = Dictionary(uniqueKeysWithValues: result.items.map { ($0.connectionId, $0.resourceIds.joined(separator: "\n")) }); bindingRevision = result.revision
            creationAllowed = Set(result.items.filter { $0.canCreate == true }.map(\.connectionId))
        } catch { store.error = error.localizedDescription }
    }
    private func reload() async {
        await store.refresh()
        if let latest = store.sessions.first(where: { $0.id == session.id }) {
            session = latest; name = latest.name; selected = Set(latest.skillNames); management = latest.management
        }
        await loadBindings()
    }
    private func saveResources() async {
        guard let api = store.api else { return }; busy = true; defer { busy = false }
        let bindings = bound.sorted().map { id -> JSONValue in
            let ids = (resources[id] ?? "").split(whereSeparator: { $0.isNewline || $0 == "," }).map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
            return .object(["connection_id": .string(id), "resource_ids": .strings(Array(Set(ids)).sorted()), "can_create": .bool(creationAllowed.contains(id))])
        }
        do {
            try await api.perform("/v1/sessions/\(APIClient.segment(session.id))/connectors", method: "PUT", body: ["revision": .number(Double(bindingRevision)), "bindings": .array(bindings)])
            await reload()
        } catch { store.error = error.localizedDescription; await store.refresh() }
    }
}

@MainActor
struct ForkSessionView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dismiss) private var dismiss
    let session: Workspace
    let messages: [Job]
    let files: [WorkspaceFile]
    @State private var name = ""
    @State private var messageIDs: Set<String> = []
    @State private var fileNames: Set<String> = []
    @State private var busy = false
    var body: some View {
        NavigationStack {
            Form {
                Section { TextField("新会话名称", text: $name) }
                Section {
                    ForEach(messages.filter { !$0.id.hasPrefix("import:") }) { job in
                        Toggle(isOn: selection(job.id, in: $messageIDs)) { Text(job.prompt ?? job.resultText).lineLimit(3) }
                    }
                } header: { Text("带入的对话") } footer: { Text("仅复制选中的内容作为输入材料，不继续原任务。") }
                Section("带入的文件") {
                    ForEach(files) { file in Toggle(file.name, isOn: selection(file.name, in: $fileNames)) }
                }
                Section { Text("复制当前技能选择。连接器资源范围、Mac 管理权限、审批和语音连接需要在新会话中重新设置。").font(.footnote).foregroundStyle(.secondary) }
            }
            .navigationTitle("从这里新建").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) { Button("创建") { Task { await create() } }.disabled(busy || name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty) }
            }
        }.onAppear { name = session.name + " · 分支" }
    }
    private func selection(_ value: String, in set: Binding<Set<String>>) -> Binding<Bool> {
        Binding(get: { set.wrappedValue.contains(value) }, set: { if $0 { set.wrappedValue.insert(value) } else { set.wrappedValue.remove(value) } })
    }
    private func create() async {
        guard let api = store.api else { return }; busy = true; defer { busy = false }
        do {
            try await api.perform("/v1/sessions/\(APIClient.segment(session.id))/fork", body: ["name": .string(name), "message_ids": .strings(messageIDs.sorted()), "file_names": .strings(fileNames.sorted())])
            await store.refresh(); dismiss()
        } catch { store.error = error.localizedDescription }
    }
}

@MainActor
struct TasksView: View {
    @EnvironmentObject private var store: AppStore
    @State private var activeOnly = false
    var body: some View {
        List {
            Toggle("只看进行中", isOn: $activeOnly)
            ForEach(store.tasks.filter { !activeOnly || $0.isActive }) { job in
                NavigationLink { SessionDetailView(sessionID: job.sessionId) } label: {
                    VStack(alignment: .leading, spacing: 7) {
                        HStack { Text(store.sessions.first(where: { $0.id == job.sessionId })?.name ?? "原会话").font(.caption.bold()).foregroundStyle(.indigo); Spacer(); Text(job.statusText).font(.caption).foregroundStyle(.secondary) }
                        Text(job.prompt ?? "任务 \(job.id.prefix(8))").lineLimit(2)
                        if !job.resultText.isEmpty { Text(job.resultText).font(.caption).foregroundStyle(.secondary).lineLimit(2) }
                    }.padding(.vertical, 4)
                }
            }
            if store.tasks.isEmpty { ContentUnavailableView("还没有任务", systemImage: "checklist", description: Text("任务会在原会话中持续执行，离开页面也能稍后查看。")) }
        }.navigationTitle("任务").refreshable { await store.refresh() }
    }
}

@MainActor
struct SettingsView: View {
    @EnvironmentObject private var store: AppStore
    @EnvironmentObject private var voice: VoiceController
    @Environment(\.openURL) private var openURL
    @State private var connecting = false
    @State private var signOut = false
    @State private var notificationStatus = ""
    var body: some View {
        Form {
            Section("已连接的 Mac") {
                Label(store.credentials?.baseURL.host ?? "Harness", systemImage: "desktopcomputer")
                LabeledContent("身份", value: store.isAdmin ? "所有者" : "受邀用户")
                LabeledContent("同步", value: store.syncLabel)
                if let id = store.credentials?.pairing.deviceId { LabeledContent("设备", value: String(id.prefix(12))) }
                Button("续期设备授权") { Task { await store.renew() } }
            }
            Section {
                ForEach(store.connections) { connection in
                    HStack {
                        VStack(alignment: .leading, spacing: 4) { Text(connection.displayName); Text(connection.provider).font(.caption).foregroundStyle(.secondary) }
                        Spacer(); Text(connection.status).font(.caption).foregroundStyle(.secondary)
                    }.swipeActions {
                        Button("断开", role: .destructive) { Task {
                            do { try await store.api?.perform("/v1/connectors/\(APIClient.segment(connection.id))", method: "DELETE"); await store.refresh() }
                            catch { store.error = error.localizedDescription }
                        } }
                    }
                }
                Button { Task { await connectGoogle() } } label: { Label(connecting ? "正在打开…" : "连接 Google Drive / Docs", systemImage: "link.badge.plus") }.disabled(connecting)
                Button("刷新授权状态") { Task { await store.refresh() } }
            } header: { Text("账号连接") } footer: { Text("完成浏览器授权后返回这里刷新。每个会话还需单独选择可访问资源。") }
            Section("通知") {
                if PushNotifications.isAvailable {
                    Button("允许任务结果与待审批通知", systemImage: "bell.badge") {
                        Task { notificationStatus = await PushNotifications.requestAuthorization() }
                    }
                    if !notificationStatus.isEmpty { Text(notificationStatus).font(.caption).foregroundStyle(.secondary) }
                } else {
                    Text(PushNotifications.unavailableMessage).font(.caption).foregroundStyle(.secondary)
                }
            }
            Section("技能库") {
                ForEach(store.skills) { skill in VStack(alignment: .leading, spacing: 4) { Text(skill.name); Text(skill.description).font(.caption).foregroundStyle(.secondary) } }
                Text("在各会话的设置中启用技能。这里显示 Mac 上同步的统一技能库。").font(.footnote).foregroundStyle(.secondary)
            }
            if store.isAdmin {
                Section("Mac 管理") {
                    Text("在指定会话的设置中打开「允许 Mac 管理工具」，然后用文字或语音发起诊断和维护任务。").font(.subheadline)
                    Text("只有所有者能开启管理权限。网络完全断开时，恢复由 Mac 上预设的守护进程处理。").font(.caption).foregroundStyle(.secondary)
                }
            }
            Section { Button("退出此设备", role: .destructive) { signOut = true } }
        }
        .navigationTitle("设置")
        .confirmationDialog("退出此设备", isPresented: $signOut, titleVisibility: .visible) {
            Button("撤销设备授权并退出", role: .destructive) { Task { await revoke() } }
            Button("仅清除此 iPhone 上的凭据", role: .destructive) { Task { await voice.stop(); await store.disconnect() } }
        } message: { Text("撤销后这台 iPhone 需要重新配对。Mac 上的任务继续执行。") }
    }
    private func connectGoogle() async {
        guard let api = store.api else { return }; connecting = true; defer { connecting = false }
        do {
            let response: AuthorizationURL = try await api.value("/v1/connectors/google/authorize", method: "POST", body: [:])
            guard response.authUrl.scheme == "https" else { throw URLError(.badURL) }
            openURL(response.authUrl)
        } catch { store.error = error.localizedDescription }
    }
    private func revoke() async {
        guard let api = store.api, let id = store.credentials?.pairing.deviceId else { return }
        do { try await api.perform("/v1/devices/\(APIClient.segment(id))", method: "DELETE"); await voice.stop(); await store.disconnect() }
        catch { store.error = error.localizedDescription }
    }
}
