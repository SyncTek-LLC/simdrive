import SwiftUI
import WebKit

// WebViewWrapper — UIViewRepresentable wrapper for WKWebView.
//
// WHY UIKit bridge: WKWebView is not natively supported in SwiftUI.
// This is the standard pattern for embedding WebKit content.
// The view is intentionally XCTest-blind on iOS — this is the test surface SimDrive's
// vision-first observe exists to navigate. Per spec §3 §3.
struct WebViewWrapper: UIViewRepresentable {
    let htmlContent: String

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.dataDetectorTypes = []
        let webView = WKWebView(frame: .zero, configuration: config)
        webView.scrollView.showsVerticalScrollIndicator = true
        webView.accessibilityIdentifier = "blog_webview"
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        // Only reload when content actually changes to avoid flash on every SwiftUI render cycle
        let currentURL = webView.url
        if currentURL == nil || htmlContent != context.coordinator.loadedContent {
            webView.loadHTMLString(htmlContent, baseURL: nil)
            context.coordinator.loadedContent = htmlContent
        }
    }

    func makeCoordinator() -> Coordinator {
        Coordinator()
    }

    final class Coordinator: NSObject {
        var loadedContent: String = ""
    }
}
