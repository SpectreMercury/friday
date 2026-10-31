// Friday's local desktop bridge. No network access or arbitrary code execution.
import AppKit
import ApplicationServices
import ScreenCaptureKit
import Vision

func emit(_ value: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) {
        FileHandle.standardOutput.write(data)
    }
}
func fail(_ code: String, _ message: String) -> Never {
    emit(["error": code, "message": message]); exit(1)
}
func permissions() -> [String: Any] {
    ["screen_recording": CGPreflightScreenCaptureAccess(), "accessibility": AXIsProcessTrusted(),
     "helper": Bundle.main.bundleURL.path]
}
func appInfo(_ app: NSRunningApplication) -> [String: Any] {
    ["name": app.localizedName ?? "", "bundle_id": app.bundleIdentifier ?? "",
     "pid": Int(app.processIdentifier), "active": app.isActive]
}
func installedApps() -> [[String: String]] {
    var installed: [[String: String]] = [], seen = Set<String>()
    for path in ["/Applications", "/System/Applications", NSHomeDirectory() + "/Applications"] {
        guard let walker = FileManager.default.enumerator(at: URL(fileURLWithPath: path), includingPropertiesForKeys: nil,
            options: [.skipsHiddenFiles, .skipsPackageDescendants]) else { continue }
        for case let url as URL in walker {
            guard installed.count < 150 else { break }
            if url.pathExtension == "app", let bundle = Bundle(url: url), let id = bundle.bundleIdentifier, seen.insert(id).inserted {
                installed.append(["name": String((bundle.object(forInfoDictionaryKey: "CFBundleDisplayName") as? String ?? url.deletingPathExtension().lastPathComponent).prefix(100)), "bundle_id": id])
            }
        }
    }
    return installed
}
func attribute(_ element: AXUIElement, _ key: CFString) -> CFTypeRef? {
    var value: CFTypeRef?
    return AXUIElementCopyAttributeValue(element, key, &value) == .success ? value : nil
}
func accessibilityText(_ app: NSRunningApplication?) -> [String: Any] {
    guard AXIsProcessTrusted(), let app else { return ["available": false] }
    let root = AXUIElementCreateApplication(app.processIdentifier)
    AXUIElementSetMessagingTimeout(root, 0.3)
    var rows: [[String: String]] = [], count = 0, remaining = 5000
    let deadline = Date().addingTimeInterval(4)
    func walk(_ node: AXUIElement, _ depth: Int) {
        guard depth < 8, count < 150, remaining > 0, Date() < deadline else { return }
        count += 1
        let role = attribute(node, kAXRoleAttribute as CFString) as? String ?? ""
        let subrole = attribute(node, kAXSubroleAttribute as CFString) as? String ?? ""
        if subrole == "AXSecureTextField" { return }
        var row = ["role": role]
        for (key, field) in [(kAXTitleAttribute, "title"), (kAXDescriptionAttribute, "description"), (kAXValueAttribute, "value")] {
            if let text = attribute(node, key as CFString) as? String, !text.isEmpty {
                let clipped = String(text.prefix(min(300, max(0, remaining))))
                row[field] = clipped; remaining -= clipped.count
            }
        }
        if row.count > 1 { rows.append(row) }
        if let children = attribute(node, kAXChildrenAttribute as CFString) as? [AXUIElement] {
            for child in children.prefix(40) { walk(child, depth + 1) }
        }
    }
    walk(root, 0)
    return ["available": true, "elements": rows, "bounded": true]
}

@main struct FridayDesktop {
    @MainActor static func main() async {
        let args = Array(CommandLine.arguments.dropFirst())
        guard let action = args.first else { fail("invalid_arguments", "缺少桌面操作。") }
        switch action {
        case "permissions":
            if args.count > 1 && args[1] == "screen_recording" { _ = CGRequestScreenCaptureAccess() }
            if args.count > 1 && args[1] == "accessibility" {
                _ = AXIsProcessTrustedWithOptions([kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary)
            }
            emit(permissions())
        case "apps":
            emit(["installed": installedApps(), "limit": 150,
                  "running": NSWorkspace.shared.runningApplications.filter { $0.activationPolicy == .regular }.prefix(60).map(appInfo)])
        case "open":
            guard args.count == 2, let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: args[1]) else {
                fail("app_not_found", "应用未安装；请先读取应用列表核对 bundle_id。")
            }
            do {
                let app = try await NSWorkspace.shared.openApplication(at: url, configuration: NSWorkspace.OpenConfiguration())
                emit(["opened": !app.isTerminated, "app": appInfo(app)])
            } catch { fail("app_open_failed", "系统未能打开指定应用。") }
        case "screen":
            guard args.count == 3, let index = Int(args[2]), index > 0 else { fail("invalid_arguments", "屏幕参数无效。") }
            guard CGPreflightScreenCaptureAccess() else {
                fail("screen_permission_required", "请在 Mac 系统设置 → 隐私与安全性 → 屏幕与系统音频录制，允许 Friday Desktop；如系统显示 Python，也需允许该运行进程。授权后重试。")
            }
            do {
                let front = NSWorkspace.shared.frontmostApplication
                let content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: true)
                let displays = content.displays.sorted { $0.displayID < $1.displayID }
                guard index <= displays.count else { fail("display_not_found", "没有这个显示器编号。") }
                let display = displays[index - 1]
                let filter = SCContentFilter(display: display, excludingWindows: [])
                let config = SCStreamConfiguration()
                let scale = min(1.0, 2400.0 / Double(max(display.width, display.height)))
                config.width = Int(Double(display.width) * scale); config.height = Int(Double(display.height) * scale)
                config.showsCursor = false
                let image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
                let bitmap = NSBitmapImageRep(cgImage: image)
                guard let png = bitmap.representation(using: .png, properties: [:]) else { fail("capture_failed", "无法编码截图。") }
                try png.write(to: URL(fileURLWithPath: args[1]), options: .atomic)
                try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: args[1])
                let request = VNRecognizeTextRequest()
                request.recognitionLevel = .accurate; request.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"]
                var ocrError = false
                do { try VNImageRequestHandler(cgImage: image, options: [:]).perform([request]) } catch { ocrError = true }
                let fullText = (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }.joined(separator: "\n")
                emit(["captured": true, "display": index, "display_count": displays.count,
                      "width": image.width, "height": image.height, "frontmost_app": front.map(appInfo) ?? [:],
                      "text": String(fullText.prefix(8000)), "ocr_failed": ocrError, "truncated": fullText.count > 8000,
                      "accessibility": accessibilityText(front), "untrusted_content": true,
                      "limitations": "模型获得屏幕OCR文字和可用的辅助功能信息；截图已保存。OCR可能漏字，不能据此声称理解所有图像、图标或布局。"])
            } catch { fail("capture_failed", "屏幕捕获失败，请检查屏幕录制权限并确认 Mac 已解锁。") }
        default: fail("invalid_arguments", "未知桌面操作。")
        }
    }
}
