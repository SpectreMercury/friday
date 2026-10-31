// Local-only document text and screenshot OCR. No networking or model inference.
import Foundation
import AppKit
import PDFKit
import Vision

func recognize(_ image: CGImage) throws -> [[String: Any]] {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"]
    request.usesLanguageCorrection = true
    try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
    return (request.results ?? []).compactMap { observation in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        return ["text": candidate.string, "confidence": candidate.confidence]
    }
}

do {
    guard CommandLine.arguments.count == 2 else { throw NSError(domain: "argument", code: 1) }
    let url = URL(fileURLWithPath: CommandLine.arguments[1])
    var result: [String: Any]
    if url.pathExtension.lowercased() == "pdf" {
        guard let document = PDFDocument(url: url), !document.isLocked else {
            throw NSError(domain: "unreadable_or_locked_pdf", code: 2)
        }
        var pages: [[String: Any]] = []
        for index in 0..<min(document.pageCount, 60) {
            guard let page = document.page(at: index) else { continue }
            let text = page.string ?? ""
            if !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                pages.append(["page": index + 1, "text": text, "method": "pdf_text"])
            } else {
                let thumbnail = page.thumbnail(of: NSSize(width: 1400, height: 1800), for: .mediaBox)
                var rect = CGRect(origin: .zero, size: thumbnail.size)
                if let image = thumbnail.cgImage(forProposedRect: &rect, context: nil, hints: nil) {
                    pages.append(["page": index + 1, "lines": try recognize(image), "method": "ocr"])
                } else {
                    pages.append(["page": index + 1, "text": "", "method": "unreadable"])
                }
            }
        }
        result = ["pages": pages, "page_count": document.pageCount, "truncated": document.pageCount > pages.count]
    } else {
        guard let image = NSImage(contentsOf: url) else { throw NSError(domain: "unreadable_image", code: 3) }
        var rect = CGRect(origin: .zero, size: image.size)
        guard let cg = image.cgImage(forProposedRect: &rect, context: nil, hints: nil) else {
            throw NSError(domain: "unreadable_image", code: 3)
        }
        result = ["lines": try recognize(cg), "method": "ocr", "width": cg.width, "height": cg.height]
    }
    let data = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
    FileHandle.standardOutput.write(data)
} catch {
    FileHandle.standardError.write(Data("Attachment extraction failed: \((error as NSError).domain)\n".utf8))
    exit(1)
}
