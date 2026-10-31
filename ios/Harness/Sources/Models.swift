import Foundation

struct Account: Codable, Sendable { let id: String; let isAdmin: Bool }
struct Pairing: Codable, Sendable { let token: String; let account: Account; let deviceId: String }
struct Credentials: Codable, Sendable { let baseURL: URL; let pairing: Pairing }
struct Items<T: Decodable>: Decodable { let items: [T] }

struct Workspace: Codable, Identifiable, Hashable, Sendable {
    let id: String
    var name: String
    var revision: Int
    var skillNames: [String]
    var management: Bool
    var archived: Bool
    var created: Double?
}
struct Skill: Codable, Identifiable, Sendable {
    var id: String { name }
    let name: String
    let description: String
    let version: String
}
struct Job: Decodable, Identifiable, Sendable {
    let id: String
    let sessionId: String
    let prompt: String?
    let result: JSONValue?
    let error: String?
    let status: String
    let outcome: String?
    let kind: String?
    let artifactUrl: String?
    let artifactFilename: String?
    let created: Double?
    var resultText: String { result?.displayText ?? "" }
    var canCancel: Bool { ["queued", "pending", "running", "waiting", "waiting_approval", "awaiting_approval"].contains(status) }
    var isActive: Bool { canCancel || outcome == "needs_input" || outcome == "blocked" || outcome == "pending" }
    var statusText: String {
        if !canCancel, let outcome {
            if outcome == "needs_input" { return "等待你" }
            if outcome == "blocked" { return "等待处理" }
            if outcome == "partial" { return "部分完成" }
            if outcome == "pending" { return "已提交，等待结果" }
            if outcome == "continued" { return "已补充" }
        }
        switch status {
        case "queued", "pending": return "排队中"
        case "running": return "执行中"
        case "completed", "succeeded", "done": return "已完成"
        case "failed": return "失败"
        case "cancelled", "canceled": return "已取消"
        case "waiting", "waiting_approval", "awaiting_approval", "needs_input": return "等待你"
        case "blocked": return "等待处理"
        default: return status
        }
    }
}
struct WorkspaceFile: Codable, Identifiable, Hashable, Sendable {
    var id: String { name }; let name: String; let size: Int
    var sizeText: String { ByteCountFormatter.string(fromByteCount: Int64(size), countStyle: .file) }
}
struct Approval: Decodable, Identifiable, Sendable {
    var id: String { token }; let token: String; let description: String; let scope: String?; let status: String
}
struct Connection: Decodable, Identifiable, Sendable {
    let id: String; let provider: String; let displayName: String; let status: String
}
struct ConnectorBinding: Codable, Identifiable, Sendable {
    var id: String { connectionId }; let connectionId: String; var resourceIds: [String]; var canCreate: Bool?
}
struct ConnectorBindings: Decodable { let items: [ConnectorBinding]; let revision: Int }
struct VoiceLease: Decodable, Sendable { let id: String; let sessionId: String; let sampleRate: Int; let epoch: Int?; let streamPath: String? }
struct VoiceEvent: Decodable {
    let type: String
    let sessionId: String?
    let epoch: Int?
    let text: String?
    let state: String?
    let message: String?
    let sampleRate: Int?
    let channels: Int?
    let encoding: String?
}
struct SyncEvent: Decodable { let sequence: Int; let sessionId: String?; let kind: String; let payload: JSONValue? }
struct EventPage: Decodable { let items: [SyncEvent]; let cursor: Int }
struct AuthorizationURL: Decodable { let authUrl: URL }
struct BrowserURL: Decodable { let url: URL }
struct RenewedToken: Decodable { let token: String }

enum JSONValue: Codable, Sendable {
    case string(String), number(Double), bool(Bool), array([JSONValue]), object([String: JSONValue]), null
    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let v = try? c.decode(Bool.self) { self = .bool(v) }
        else if let v = try? c.decode(String.self) { self = .string(v) }
        else if let v = try? c.decode(Double.self) { self = .number(v) }
        else if let v = try? c.decode([JSONValue].self) { self = .array(v) }
        else { self = .object(try c.decode([String: JSONValue].self)) }
    }
    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .string(let v): try c.encode(v)
        case .number(let v): try c.encode(v)
        case .bool(let v): try c.encode(v)
        case .array(let v): try c.encode(v)
        case .object(let v): try c.encode(v)
        case .null: try c.encodeNil()
        }
    }
    var displayText: String {
        switch self {
        case .string(let v): return v
        case .null: return ""
        case .object(let v):
            if let text = v["text"] ?? v["message"] { return text.displayText }
            fallthrough
        default:
            let encoder = JSONEncoder(); encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            return (try? encoder.encode(self)).flatMap { String(data: $0, encoding: .utf8) } ?? ""
        }
    }
    static func strings(_ values: [String]) -> JSONValue { .array(values.map(JSONValue.string)) }
}

// A late format header explicitly invalidates the following binary packet. Browsing UI cannot mutate this binding.
struct VoicePacketGate {
    let sessionID: String
    private(set) var epoch: Int
    private var packetEpoch = -1
    private var minimumPlaybackEpoch: Int
    init(sessionID: String, epoch: Int) { self.sessionID = sessionID; self.epoch = epoch; minimumPlaybackEpoch = epoch }
    var canPlayPacket: Bool { packetEpoch == epoch && packetEpoch >= minimumPlaybackEpoch }
    mutating func interrupt() { minimumPlaybackEpoch = epoch + 1 }
    mutating func accept(_ event: VoiceEvent) -> Bool {
        if let owner = event.sessionId, owner != sessionID {
            if event.type == "audio_format" { packetEpoch = -1 }
            return false
        }
        if event.type == "audio_format" { packetEpoch = event.epoch ?? -1 }
        if let incoming = event.epoch { guard incoming >= epoch else { return false }; epoch = incoming }
        if event.type == "clear_playback" { minimumPlaybackEpoch = max(minimumPlaybackEpoch, epoch) }
        return true
    }
}


// The destination and idempotency key are immutable for the lifetime of one stopped recording.
struct VoiceMessageDraft: Sendable {
    static let maxBytes = 20 * 1024 * 1024
    static var temporaryDirectory: URL { FileManager.default.temporaryDirectory.appendingPathComponent("harness-voice-messages", isDirectory: true) }
    static func clearAbandonedFiles() { try? FileManager.default.removeItem(at: temporaryDirectory) }
    let sessionID: String
    let file: URL
    let requestID: UUID
    init(sessionID: String, file: URL, requestID: UUID = UUID()) {
        self.sessionID = sessionID; self.file = file; self.requestID = requestID
    }
    func validateFile() throws {
        let values = try file.resourceValues(forKeys: [.fileSizeKey, .isRegularFileKey, .isSymbolicLinkKey])
        guard file.isFileURL, values.isRegularFile == true, values.isSymbolicLink != true,
              let bytes = values.fileSize, bytes > 0, bytes <= Self.maxBytes else {
            throw APIError(status: 413, message: "语音留言为空、不可读或超过 20 MB。")
        }
    }
}


// Both capture paths must hold this process-local lease before asking AVAudioSession to record.
@MainActor
enum MicrophoneLease {
    private static var owner: UUID?
    static func claim(_ candidate: UUID) -> Bool {
        guard owner == nil else { return false }; owner = candidate; return true
    }
    static func release(_ candidate: UUID) { if owner == candidate { owner = nil } }
}
