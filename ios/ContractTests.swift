// Run with ./ios/check.sh. Uses Foundation only; no device or credentials required.
import Foundation

@main
struct ContractTests {
    @MainActor
    static func main() async throws {
        let sessionJSON = Data(#"{"id":"session-a","name":"工作","revision":3,"skill_names":["research"],"management":false,"archived":false,"created":1758940000.1}"#.utf8)
        let session = try APIClient.decoder.decode(Workspace.self, from: sessionJSON)
        precondition(session.id == "session-a" && session.skillNames == ["research"] && session.revision == 3)
        let jobJSON = Data(#"{"id":"job-1","session_id":"session-a","prompt":"你好","result":{"text":"完成"},"error":null,"status":"completed","outcome":"completed","kind":"chat","artifact_url":null,"created":1758940000}"#.utf8)
        let job = try APIClient.decoder.decode(Job.self, from: jobJSON)
        precondition(job.sessionId == session.id && job.resultText == "完成" && !job.isActive)
        let eventJSON = Data(#"{"type":"audio_format","epoch":4,"session_id":"session-a","sample_rate":24000,"channels":1,"encoding":"pcm_s16le"}"#.utf8)
        let event = try APIClient.decoder.decode(VoiceEvent.self, from: eventJSON)
        precondition(event.sampleRate == 24000 && event.epoch == 4)
        let api = APIClient(baseURL: URL(string: "https://harness.example.com")!, token: "test-token")
        let request = try api.request("/v1/sessions/session-a/voice", method: "POST")
        precondition(request.timeoutInterval == 135)
        precondition(request.value(forHTTPHeaderField: "Authorization") == "Bearer test-token")
        let filename = "资料 a?b#c.txt"
        let url = try api.url("/v1/sessions/session-a/files/" + APIClient.segment(filename))
        precondition(url.lastPathComponent == filename && url.query == nil && url.fragment == nil)
        for unsafe in ["https://other.example/secret", "//other.example/secret", "/admin"] {
            do { _ = try api.url(unsafe); fatalError("Accepted non-API or foreign URL") } catch is URLError {}
        }
        var gate = VoicePacketGate(sessionID: "session-a", epoch: 4)
        precondition(gate.accept(event) && gate.canPlayPacket)
        gate.interrupt(); precondition(!gate.canPlayPacket)
        let newEpoch = try APIClient.decoder.decode(VoiceEvent.self, from: Data(#"{"type":"clear_playback","epoch":5,"session_id":"session-a"}"#.utf8))
        precondition(gate.accept(newEpoch) && !gate.canPlayPacket)
        precondition(!gate.accept(event) && !gate.canPlayPacket)
        let wrongSession = try APIClient.decoder.decode(VoiceEvent.self, from: Data(#"{"type":"recognized","text":"late","epoch":6,"session_id":"session-b"}"#.utf8))
        precondition(!gate.accept(wrongSession) && gate.epoch == 5)
        let newFormat = try APIClient.decoder.decode(VoiceEvent.self, from: Data(#"{"type":"audio_format","epoch":5,"session_id":"session-a","sample_rate":24000}"#.utf8))
        precondition(gate.accept(newFormat) && gate.canPlayPacket)
        let bindings = try APIClient.decoder.decode(ConnectorBindings.self, from: Data(#"{"items":[{"connection_id":"google-1","resource_ids":["doc-1"]}],"revision":3}"#.utf8))
        precondition(bindings.items.first?.resourceIds == ["doc-1"])
        let error = try JSONDecoder().decode(JSONValue.self, from: Data(#"{"code":"voice_busy","message":"语音已占用"}"#.utf8))
        precondition(error.displayText == "语音已占用")
        let oldCacheMarker = await PreviewCache.shared.marker()
        await PreviewCache.shared.clear()
        do { _ = try await PreviewCache.shared.save(Data("old account".utf8), filename: "private.txt", generation: oldCacheMarker); fatalError("Stale account preview accepted") } catch is CancellationError {}
        let scratch = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: scratch, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: scratch) }
        let audioFile = scratch.appendingPathComponent("message.m4a")
        let audioBytes = Data([0, 0, 0, 24, 102, 116, 121, 112, 77, 52, 65, 32])
        try audioBytes.write(to: audioFile)
        let draft = VoiceMessageDraft(sessionID: "session-a", file: audioFile)
        let voiceRequest = try api.voiceMessageRequest(draft)
        let retryRequest = try api.voiceMessageRequest(draft)
        precondition(voiceRequest.url?.path == "/v1/sessions/session-a/voice-messages")
        precondition(voiceRequest.httpMethod == "POST" && voiceRequest.httpBody == audioBytes)
        precondition(voiceRequest.value(forHTTPHeaderField: "Content-Type") == "audio/mp4")
        precondition(voiceRequest.value(forHTTPHeaderField: "Idempotency-Key") == draft.requestID.uuidString)
        precondition(voiceRequest.value(forHTTPHeaderField: "Idempotency-Key") == retryRequest.value(forHTTPHeaderField: "Idempotency-Key"))
        precondition(voiceRequest.value(forHTTPHeaderField: "Authorization") == "Bearer test-token")
        precondition(VoiceMessageDraft(sessionID: "session-b", file: audioFile).requestID != draft.requestID)
        try Data().write(to: audioFile)
        do { _ = try api.voiceMessageRequest(draft); fatalError("Empty voice message accepted") } catch is APIError {}
        let large = try FileHandle(forWritingTo: audioFile)
        try large.truncate(atOffset: UInt64(VoiceMessageDraft.maxBytes + 1)); try large.close()
        do { _ = try api.voiceMessageRequest(draft); fatalError("Oversized voice message accepted") } catch is APIError {}
        let liveOwner = UUID(), noteOwner = UUID()
        precondition(MicrophoneLease.claim(liveOwner))
        precondition(!MicrophoneLease.claim(noteOwner))
        MicrophoneLease.release(noteOwner)
        precondition(!MicrophoneLease.claim(noteOwner))
        MicrophoneLease.release(liveOwner)
        precondition(MicrophoneLease.claim(noteOwner))
        MicrophoneLease.release(noteOwner)
        print("iOS Foundation contract checks passed (models, session IDs, errors, voice format/epoch isolation, timeout, preview isolation, voice-message destination/idempotency/limits, microphone exclusion, origin and path encoding).")
    }
}
