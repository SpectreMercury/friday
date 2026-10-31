import Foundation
import Security

struct APIError: LocalizedError {
    let status: Int
    let message: String
    let code: String?
    init(status: Int, message: String, code: String? = nil) { self.status = status; self.message = message; self.code = code }
    var errorDescription: String? {
        if code == "revision_conflict" { return "配置已在另一端更新，请刷新后重试。\n\(message)" }
        if status == 401 { return "设备凭据已失效，请重新配对。" }
        return message
    }
}

struct APIClient: Sendable {
    let baseURL: URL
    let token: String?
    static let decoder: JSONDecoder = {
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase; return decoder
    }()
    func url(_ path: String) throws -> URL {
        guard var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else { throw URLError(.badURL) }
        let pieces = path.split(separator: "?", maxSplits: 1, omittingEmptySubsequences: false)
        // All routes are same-origin. Never forward a device token to an artifact redirect or a foreign URL.
        guard path.hasPrefix("/v1/"), !path.hasPrefix("//") else { throw URLError(.badURL) }
        components.percentEncodedPath = String(pieces[0])
        components.percentEncodedQuery = pieces.count > 1 ? String(pieces[1]) : nil
        guard let url = components.url else { throw URLError(.badURL) }; return url
    }
    func request(_ path: String, method: String = "GET", body: [String: JSONValue]? = nil) throws -> URLRequest {
        var request = URLRequest(url: try url(path)); request.httpMethod = method
        request.timeoutInterval = path.hasSuffix("/voice") && method == "POST" ? 135 : 30
        if let token { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        if let body {
            request.httpBody = try JSONEncoder().encode(body)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return request
    }
    func value<T: Decodable>(_ path: String, method: String = "GET", body: [String: JSONValue]? = nil) async throws -> T {
        let data = try await data(for: request(path, method: method, body: body))
        return try Self.decoder.decode(T.self, from: data)
    }
    func perform(_ path: String, method: String = "POST", body: [String: JSONValue]? = nil) async throws {
        _ = try await data(for: request(path, method: method, body: body))
    }
    func data(for request: URLRequest) async throws -> Data {
        let (data, response) = try await URLSession.shared.data(for: request, delegate: SameOriginRedirectDelegate())
        guard let response = response as? HTTPURLResponse else { throw URLError(.badServerResponse) }
        guard 200..<300 ~= response.statusCode else {
            let content = try? JSONDecoder().decode([String: JSONValue].self, from: data)
            let message = content?["error"]?.displayText ?? content?["detail"]?.displayText ?? content?["message"]?.displayText ?? "服务器返回 HTTP \(response.statusCode)"
            let code: String?
            if case .object(let problem) = content?["error"], case .string(let value) = problem["code"] { code = value } else { code = nil }
            throw APIError(status: response.statusCode, message: message, code: code)
        }
        return data
    }
    func upload(_ file: URL, sessionID: String) async throws {
        let access = file.startAccessingSecurityScopedResource(); defer { if access { file.stopAccessingSecurityScopedResource() } }
        let attributes = try file.resourceValues(forKeys: [.fileSizeKey])
        guard (attributes.fileSize ?? 0) <= 20 * 1024 * 1024 else { throw APIError(status: 413, message: "附件不能超过 20 MB。") }
        let bytes = try Data(contentsOf: file)
        let boundary = "Harness-\(UUID().uuidString)"
        let filename = file.lastPathComponent.replacingOccurrences(of: "\"", with: "_").replacingOccurrences(of: "\r", with: "_").replacingOccurrences(of: "\n", with: "_")
        var body = Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\nContent-Type: application/octet-stream\r\n\r\n".utf8)
        body.append(bytes); body.append(Data("\r\n--\(boundary)--\r\n".utf8))
        var request = try request("/v1/sessions/\(Self.segment(sessionID))/files", method: "POST")
        request.httpBody = body; request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        _ = try await data(for: request)
    }
    func voiceMessageRequest(_ draft: VoiceMessageDraft) throws -> URLRequest {
        try draft.validateFile()
        var request = try request("/v1/sessions/\(Self.segment(draft.sessionID))/voice-messages", method: "POST")
        request.httpBody = try Data(contentsOf: draft.file, options: .mappedIfSafe)
        guard let body = request.httpBody, !body.isEmpty, body.count <= VoiceMessageDraft.maxBytes else {
            throw APIError(status: 413, message: "语音留言为空或超过 20 MB。")
        }
        request.setValue("audio/mp4", forHTTPHeaderField: "Content-Type")
        request.setValue(draft.requestID.uuidString, forHTTPHeaderField: "Idempotency-Key")
        return request
    }
    func sendVoiceMessage(_ draft: VoiceMessageDraft) async throws -> Job {
        let data = try await data(for: voiceMessageRequest(draft))
        return try Self.decoder.decode(Job.self, from: data)
    }
    func download(_ path: String, filename: String) async throws -> URL {
        let generation = await PreviewCache.shared.marker()
        let bytes = try await data(for: request(path))
        return try await PreviewCache.shared.save(bytes, filename: filename, generation: generation)
    }
    static func segment(_ value: String) -> String {
        value.addingPercentEncoding(withAllowedCharacters: CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-_.~"))) ?? ""
    }
}

final class SameOriginRedirectDelegate: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        guard let source = task.originalRequest?.url, let target = request.url,
              source.scheme == target.scheme, source.host == target.host, source.port == target.port else {
            completionHandler(nil); return
        }
        completionHandler(request)
    }
}

enum SecureCredentials {
    private static let service = "app.personal.harness.credentials"
    static func load() -> Credentials? {
        var query = base; query[kSecReturnData] = true; query[kSecMatchLimit] = kSecMatchLimitOne
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess, let data = result as? Data else { return nil }
        return try? JSONDecoder().decode(Credentials.self, from: data)
    }
    static func save(_ credentials: Credentials) throws {
        let data = try JSONEncoder().encode(credentials)
        let attributes: [CFString: Any] = [kSecValueData: data, kSecAttrAccessible: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly]
        let update = SecItemUpdate(base as CFDictionary, attributes as CFDictionary)
        if update == errSecItemNotFound {
            let result = SecItemAdd(base.merging(attributes) { _, new in new } as CFDictionary, nil)
            guard result == errSecSuccess else { throw APIError(status: 0, message: "无法安全保存设备凭据（\(result)）。") }
        } else if update != errSecSuccess { throw APIError(status: 0, message: "无法更新设备凭据（\(update)）。") }
    }
    static func delete() { SecItemDelete(base as CFDictionary) }
    private static var base: [CFString: Any] { [kSecClass: kSecClassGenericPassword, kSecAttrService: service, kSecAttrAccount: "primary"] }
}

// Cache cleanup invalidates in-flight downloads before deleting plaintext previews.
actor PreviewCache {
    static let shared = PreviewCache()
    private var generation = UUID()
    private let root = FileManager.default.temporaryDirectory.appendingPathComponent("harness-previews", isDirectory: true)
    func marker() -> UUID { generation }
    func save(_ bytes: Data, filename: String, generation marker: UUID) throws -> URL {
        guard marker == generation else { throw CancellationError() }
        let directory = root.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let target = directory.appendingPathComponent(URL(fileURLWithPath: filename).lastPathComponent)
        try bytes.write(to: target, options: [.atomic, .completeFileProtection]); return target
    }
    func clear() {
        generation = UUID()
        try? FileManager.default.removeItem(at: root)
    }
}
