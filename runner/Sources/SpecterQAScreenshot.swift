//
//  SpecterQAScreenshot.swift
//  SpecterQA Runner
//
//  Screenshot capture with scale and compression support.
//  Copied from PoolIQ v2 (SpecterQAScreenshot.swift). No changes.
//
//  Strategy:
//    - Capture via XCUIScreen.main.screenshot() — reliable, works in Simulator
//    - Downsample to requested scale (0.25x, 0.5x, 1.0x)
//    - Encode as JPEG (default, smaller) or PNG
//    - Return base64-encoded string suitable for JSON embedding
//

import XCTest
import UIKit

// MARK: - Screenshot Options

struct ScreenshotOptions {
    /// Scale factor applied to the screenshot dimensions. Default 0.5.
    var scale: CGFloat = 0.5
    /// Output format. Default .jpeg.
    var format: ScreenshotFormat = .jpeg
    /// JPEG quality 0.0–1.0. Default 0.8 (80%).
    var quality: CGFloat = 0.8

    static let `default` = ScreenshotOptions()

    /// Parse from HTTP query string parameters.
    static func from(query: [String: String]) -> ScreenshotOptions {
        var opts = ScreenshotOptions()
        if let s = query["scale"], let f = Double(s) {
            opts.scale = CGFloat(max(0.1, min(1.0, f)))
        }
        if let fmt = query["format"] {
            opts.format = ScreenshotFormat(rawValue: fmt.lowercased()) ?? .jpeg
        }
        if let q = query["quality"], let qf = Double(q) {
            let normalized = qf > 1.0 ? qf / 100.0 : qf
            opts.quality = CGFloat(max(0.1, min(1.0, normalized)))
        }
        return opts
    }
}

enum ScreenshotFormat: String {
    case jpeg = "jpeg"
    case png  = "png"
}

// MARK: - SpecterQAScreenshot

final class SpecterQAScreenshot {

    // MARK: - Capture

    /// Captures the main screen, applies options, and returns base64-encoded image data.
    func capture(options: ScreenshotOptions = .default) throws -> String {
        // Must be called on main thread (XCUIScreen.main.screenshot() requirement)
        let screenshot = XCUIScreen.main.screenshot()
        guard let originalImage = screenshot.image as? UIImage else {
            throw ScreenshotError.captureFailed("XCUIScreen.main.screenshot() returned non-UIImage type")
        }

        let processedImage: UIImage
        if options.scale < 1.0 {
            let newSize = CGSize(
                width: originalImage.size.width * options.scale,
                height: originalImage.size.height * options.scale
            )
            processedImage = try resize(image: originalImage, to: newSize)
        } else {
            processedImage = originalImage
        }

        let imageData: Data
        switch options.format {
        case .jpeg:
            guard let data = processedImage.jpegData(compressionQuality: options.quality) else {
                throw ScreenshotError.encodingFailed("JPEG encoding returned nil")
            }
            imageData = data
        case .png:
            guard let data = processedImage.pngData() else {
                throw ScreenshotError.encodingFailed("PNG encoding returned nil")
            }
            imageData = data
        }

        let base64 = imageData.base64EncodedString()
        print("[SpecterQA-Runner] Screenshot: \(originalImage.size) → scale=\(options.scale) format=\(options.format.rawValue) bytes=\(imageData.count) base64_len=\(base64.count)")
        return base64
    }

    /// Convenience: returns a response dict with base64 image and metadata.
    func captureToDict(options: ScreenshotOptions = .default) -> [String: Any] {
        do {
            let base64 = try capture(options: options)
            return [
                "success": true,
                "result": [
                    "data": base64,
                    "format": options.format.rawValue,
                    "scale": options.scale,
                    "encoding": "base64"
                ]
            ]
        } catch {
            return [
                "success": false,
                "error": error.localizedDescription
            ]
        }
    }

    // MARK: - Resize

    private func resize(image: UIImage, to size: CGSize) throws -> UIImage {
        let renderer = UIGraphicsImageRenderer(size: size)
        return renderer.image { _ in
            image.draw(in: CGRect(origin: .zero, size: size))
        }
    }
}

// MARK: - Errors

enum ScreenshotError: Error {
    case captureFailed(String)
    case encodingFailed(String)

    var localizedDescription: String {
        switch self {
        case .captureFailed(let m):  return "Screenshot capture failed: \(m)"
        case .encodingFailed(let m): return "Screenshot encoding failed: \(m)"
        }
    }
}
