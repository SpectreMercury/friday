import Foundation
import SwiftUI

@MainActor
final class AppStore: ObservableObject {
    @Published private(set) var credentials = SecureCredentials.load()
    @Published var sessions: [Workspace] = []
    @Published var tasks: [Job] = []
    @Published var skills: [Skill] = []
    @Published var connections: [Connection] = []
    @Published var error: String?
    @Published var isBusy = false
    @Published var syncLabel = "正在连接"
    @Published var eventVersion = 0
    @Published var tab = 0
    @Published var sessionPath: [String] = []
    @Published var authGeneration = UUID()
    @Published var drafts: [String: String] = [:]
    private var requestIDs: [String: (String, String)] = [:]
    private var cursor = 0
    var api: APIClient? { credentials.map { APIClient(baseURL: $0.baseURL, token: $0.pairing.token) } }
    var isAdmin: Bool { credentials?.pairing.account.isAdmin == true }
    var accountID: String? { credentials?.pairing.account.id }

    func pair(baseURL: String, code: String) async {
        isBusy = true; defer { isBusy = false }
        do {
            guard let url = URL(string: baseURL.trimmingCharacters(in: .whitespacesAndNewlines)),
                  let host = url.host, !host.isEmpty, url.user == nil, url.password == nil,
                  url.query == nil, url.fragment == nil, url.path == "" || url.path == "/",
                  url.scheme == "https" || (url.scheme == "http" && ["localhost", "127.0.0.1", "::1"].contains(host)) else {
                throw APIError(status: 0, message: "请输入 HTTPS 服务地址，例如 https://harness.example.com；地址不应包含路径。")
            }
            let client = APIClient(baseURL: url, token: nil)
            let pair: Pairing = try await client.value("/v1/pair", method: "POST", body: ["code": .string(code.trimmingCharacters(in: .whitespacesAndNewlines)), "device_name": .string(UIDevice.current.name)])
            let value = Credentials(baseURL: url, pairing: pair)
            await PreviewCache.shared.clear()
            try SecureCredentials.save(value); credentials = value; cursor = 0; authGeneration = UUID()
            await refresh()
        } catch { self.error = error.localizedDescription }
    }
    func refresh() async {
        guard let api else { return }
        let deviceID = credentials?.pairing.deviceId
        do {
            async let sessionPage: Items<Workspace> = api.value("/v1/sessions")
            async let jobPage: Items<Job> = api.value("/v1/tasks")
            async let skillPage: Items<Skill> = api.value("/v1/skills")
            async let connectionPage: Items<Connection> = api.value("/v1/connectors")
            let (s, j, k, c) = try await (sessionPage, jobPage, skillPage, connectionPage)
            guard credentials?.pairing.deviceId == deviceID, !Task.isCancelled else { return }
            sessions = s.items; tasks = j.items; skills = k.items; connections = c.items
            syncLabel = "已同步"
        } catch is CancellationError {} catch { syncLabel = "连接中断 · 稍后自动重连"; self.error = error.localizedDescription }
    }
    func poll() async {
        guard api != nil else { return }
        let deviceID = credentials?.pairing.deviceId
        await refresh()
        while !Task.isCancelled, let api {
            do {
                let page: EventPage = try await api.value("/v1/events?after=\(cursor)")
                guard credentials?.pairing.deviceId == deviceID, !Task.isCancelled else { return }
                cursor = page.cursor
                if !page.items.isEmpty { eventVersion += 1; await refresh() }
                syncLabel = "已同步"
            } catch is CancellationError { return } catch { syncLabel = "连接中断 · 稍后自动重连" }
            do { try await Task.sleep(for: .seconds(3)) } catch { return }
        }
    }
    func createSession(name: String) async -> Workspace? {
        guard let api else { return nil }
        do {
            let workspace: Workspace = try await api.value("/v1/sessions", method: "POST", body: ["name": .string(name)])
            await refresh(); return workspace
        } catch { self.error = error.localizedDescription; return nil }
    }
    func update(_ session: Workspace, fields: [String: JSONValue]) async -> Bool {
        guard let api else { return false }
        do {
            var body = fields; body["revision"] = .number(Double(session.revision))
            try await api.perform("/v1/sessions/\(APIClient.segment(session.id))", method: "PATCH", body: body)
            await refresh(); return true
        } catch { self.error = error.localizedDescription; await refresh(); return false }
    }
    func setSkills(_ selected: Set<String>, session: Workspace) async -> Bool {
        guard let api else { return false }
        do {
            try await api.perform("/v1/sessions/\(APIClient.segment(session.id))/skills", method: "PUT", body: ["revision": .number(Double(session.revision)), "skill_names": .strings(selected.sorted())])
            await refresh(); return true
        } catch { self.error = error.localizedDescription; await refresh(); return false }
    }
    func send(_ text: String, to sessionID: String) async -> Bool {
        guard let api else { return false }
        let requestID: String
        if let pending = requestIDs[sessionID], pending.0 == text { requestID = pending.1 }
        else { requestID = UUID().uuidString; requestIDs[sessionID] = (text, requestID) }
        do {
            try await api.perform("/v1/sessions/\(APIClient.segment(sessionID))/messages", body: ["text": .string(text), "request_id": .string(requestID)])
            if drafts[sessionID]?.trimmingCharacters(in: .whitespacesAndNewlines) == text { drafts[sessionID] = "" }
            requestIDs.removeValue(forKey: sessionID); await refresh(); return true
        } catch { self.error = error.localizedDescription; return false }
    }
    func cancel(_ job: Job) async {
        guard let api else { return }
        do { try await api.perform("/v1/sessions/\(APIClient.segment(job.sessionId))/tasks/\(APIClient.segment(job.id))/cancel"); await refresh() }
        catch { self.error = error.localizedDescription }
    }
    func renew() async {
        guard let api, let existing = credentials else { return }
        do {
            let response: RenewedToken = try await api.value("/v1/auth/renew", method: "POST")
            guard credentials?.pairing.token == existing.pairing.token else { return }
            let updated = Credentials(baseURL: existing.baseURL, pairing: Pairing(token: response.token, account: existing.pairing.account, deviceId: existing.pairing.deviceId))
            try SecureCredentials.save(updated); credentials = updated; authGeneration = UUID()
        } catch { self.error = error.localizedDescription }
    }
    func openPendingNotification() async {
        guard let api, let id = UserDefaults.standard.string(forKey: "HarnessNotificationSession") else { return }
        UserDefaults.standard.removeObject(forKey: "HarnessNotificationSession")
        let deviceID = credentials?.pairing.deviceId
        do {
            let session: Workspace = try await api.value("/v1/sessions/\(APIClient.segment(id))")
            guard credentials?.pairing.deviceId == deviceID else { return }
            if !sessions.contains(where: { $0.id == id }) { sessions.append(session) }
            tab = 0; sessionPath = [id]
        } catch { self.error = error.localizedDescription }
    }
    func disconnect() async {
        SecureCredentials.delete(); credentials = nil; sessions = []; tasks = []; skills = []; connections = []
        drafts = [:]; requestIDs = [:]; cursor = 0; error = nil; sessionPath = []; authGeneration = UUID()
        UserDefaults.standard.removeObject(forKey: "HarnessNotificationSession")
        await PreviewCache.shared.clear()
    }
}
