import XCTest
import Foundation

/// SpecterQARunner — Main XCTest entry point.
///
/// This XCTestCase subclass boots an HTTP server inside the iOS Simulator
/// and keeps the test process alive indefinitely. External clients (Python,
/// shell scripts) send POST/GET requests to inject touches, type text, and
/// capture screenshots without any CGEvent or Accessibility permission.
///
/// Architecture mirrors WebDriverAgent / Maestro:
///   xcodebuild test-without-building  →  SpecterQARunnerTests/testServe()
///   HTTP server listens on :8222
///   Python client  →  POST /tap  →  XCUICoordinate.tap()
///
class SpecterQARunnerTests: XCTestCase {

    // MARK: - Entry point

    /// The single long-running test. xcodebuild keeps this alive until the
    /// HTTP server receives POST /shutdown or the process is killed.
    func testServe() throws {
        let port = resolvePort()
        let bundleId = resolveBundleId()

        let injector = TouchInjector(bundleId: bundleId)
        let server   = HTTPServer(port: port, injector: injector)

        try server.start()

        NSLog("[SpecterQA] Runner listening on port \(port) targeting bundle '\(bundleId)'")
        NSLog("[SpecterQA] Endpoints: GET /health  GET /source  GET /screenshot  POST /tap  POST /swipe  POST /type  POST /key  POST /press_button  POST /shutdown")

        // Block the test thread until the server signals shutdown.
        server.waitUntilStopped()

        NSLog("[SpecterQA] Runner stopped — test exiting cleanly.")
    }

    // MARK: - Configuration helpers

    /// Port resolution order:
    ///   1. SPECTERQA_PORT env var (set by launch.sh via xcodebuild)
    ///   2. Hard-coded default 8222
    private func resolvePort() -> UInt16 {
        if let raw = ProcessInfo.processInfo.environment["SPECTERQA_PORT"],
           let value = UInt16(raw) {
            return value
        }
        return 8222
    }

    /// Bundle ID of the app-under-test resolution order:
    ///   1. SPECTERQA_BUNDLE_ID env var
    ///   2. Hard-coded sentinel — callers should always supply this
    private func resolveBundleId() -> String {
        return ProcessInfo.processInfo.environment["SPECTERQA_BUNDLE_ID"]
            ?? "com.example.app"
    }
}
