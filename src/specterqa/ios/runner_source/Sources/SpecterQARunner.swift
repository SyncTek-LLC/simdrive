import XCTest
import Foundation

/// SpecterQARunner — Main XCTest entry point.
///
/// Merged v1 (SpecterQARunner.swift) + v2 (SpecterQARunnerTest.swift / PoolIQ).
///
/// Architecture:
///   xcodebuild test-without-building  →  SpecterQARunnerTests/testServe()
///   HTTP server listens on :8222 (overridable via SPECTERQA_PORT)
///   Python client  →  POST /tap  →  XCUICoordinate.tap()
///
/// v2 additions retained:
///   - Alert dismisser registration (6 permission types + generic fallback)
///   - PID file management (/tmp/specterqa_runner.pid)
///   - Orphan process cleanup on startup
///   - Stop sentinel file (/tmp/specterqa_runner_stop)
///   - v2 subsystems wired: SpecterQAScreenshot, SpecterQAElementQuery
///
/// v1 retained:
///   - CFRunLoopRunInMode loop (required for runOnMain() CFRunLoop dispatch)
///   - resolveBundleId() from SPECTERQA_BUNDLE_ID env var
///   - Multi-attempt app launch with retry
///
class SpecterQARunnerTests: XCTestCase {

    // MARK: - Configuration

    private func resolvePort() -> UInt16 {
        if let raw = ProcessInfo.processInfo.environment["SPECTERQA_PORT"],
           let value = UInt16(raw) { return value }
        return 8222
    }

    private func resolveBundleId() -> String {
        return ProcessInfo.processInfo.environment["SPECTERQA_BUNDLE_ID"]
            ?? "com.example.app"
    }

    private let pidFilePath       = "/tmp/specterqa_runner.pid"
    private let stopSentinelPath  = "/tmp/specterqa_runner_stop"
    private let maxDuration: TimeInterval = 3600   // 1 hour
    private let tapInterval: TimeInterval = 2.0

    // MARK: - Entry point

    /// The single long-running test. xcodebuild keeps this alive until the
    /// HTTP server receives POST /shutdown or /stop, the stop sentinel file
    /// appears, or the process is killed externally.
    func testServe() throws {
        let port     = resolvePort()
        let bundleId = resolveBundleId()

        NSLog("[SpecterQA] ============================================")
        NSLog("[SpecterQA] SpecterQA HTTP Runner starting up")
        NSLog("[SpecterQA] Port: \(port)  Bundle: \(bundleId)  Max: \(Int(maxDuration))s")
        NSLog("[SpecterQA] PID file: \(pidFilePath)  Sentinel: \(stopSentinelPath)")
        NSLog("[SpecterQA] ============================================")

        // Step 0: Apply WDA-proven XCTest crash mitigations.
        // Without these, the runner crashes (SIGABRT) when the app fires
        // rapid NotificationCenter posts during borrow/download/sheet
        // presentations. The crash is in XCTRunnerIDESession.logDebugMessage:
        // → NSKeyedArchiver trying to serialize a message with a deallocated
        // AX element pointer. Three mitigations eliminate all known crash vectors.
        applyCrashMitigations()

        // Step 1: Clean up any orphaned previous runner
        cleanupOrphanProcess()

        // Step 2: Register alert interruption monitors before launch
        registerAlertDismissers()

        let injector = TouchInjector(bundleId: bundleId)

        // Step 3: Launch the app with retries (Springboard may not be ready immediately)
        var launchAttempts = 0
        let maxAttempts = 3
        while injector.app.state != .runningForeground && launchAttempts < maxAttempts {
            launchAttempts += 1
            NSLog("[SpecterQA] Launching app '\(bundleId)' (attempt \(launchAttempts)/\(maxAttempts))...")
            injector.app.launch()
            let launched = injector.app.wait(for: .runningForeground, timeout: 10)
            if launched {
                NSLog("[SpecterQA] App launched successfully on attempt \(launchAttempts)")
                break
            }
            if launchAttempts < maxAttempts {
                NSLog("[SpecterQA] Waiting before retry...")
                Thread.sleep(forTimeInterval: 3)
            }
        }
        if injector.app.state != .runningForeground {
            NSLog("[SpecterQA] WARNING: App failed to reach foreground after \(maxAttempts) attempts (state=\(injector.app.state.rawValue))")
        }

        // Step 4: Build server with v2 subsystems
        let server = HTTPServer(port: port, injector: injector)
        server.screenshotCapture = SpecterQAScreenshot()
        server.elementQuery      = SpecterQAElementQuery(app: injector.app)

        try server.start()

        // Step 5: Write PID file
        writePIDFile()

        NSLog("[SpecterQA] Runner listening on port \(port) targeting '\(bundleId)' (app state=\(injector.app.state.rawValue))")
        NSLog("[SpecterQA] Endpoints: GET /health /source /screenshot /elements  POST /tap /swipe /type /key /press_button /scroll /wait /launch /terminate /dismiss-alert /shutdown /stop")

        // Step 6: Spin the RunLoop with CFRunLoopRunInMode.
        //
        // MUST use CFRunLoopRunInMode here — not RunLoop.current.run() or Thread.sleep.
        // The runOnMain() helper in HTTPServer.swift uses CFRunLoopPerformBlock +
        // CFRunLoopWakeUp to dispatch XCUITest calls to the main thread.
        // CFRunLoopRunInMode processes those blocks; a plain RunLoop.current.run()
        // call exits after the first dispatched block, and Thread.sleep blocks
        // the main thread entirely (deadlocking runOnMain).
        //
        // Use a timed mode loop so we can check the stop sentinel periodically.

        NSLog("[SpecterQA] Entering CFRunLoopRunInMode loop...")

        // Watch the server's stop semaphore on a background thread
        var serverStopped = false
        let stopWatcher = DispatchQueue(label: "com.specterqa.runner.stopwatcher")
        stopWatcher.async {
            server.stopSemaphore.wait()
            serverStopped = true
        }

        let deadline = Date().addingTimeInterval(maxDuration)
        let fileManager = FileManager.default

        while server.isRunning && !serverStopped && Date() < deadline {
            // Check sentinel file
            if fileManager.fileExists(atPath: stopSentinelPath) {
                NSLog("[SpecterQA] Stop sentinel detected — exiting.")
                try? fileManager.removeItem(atPath: stopSentinelPath)
                break
            }

            // Run the loop for tapInterval seconds, processing any pending blocks
            CFRunLoopRunInMode(.defaultMode, tapInterval, false)
        }

        // Step 7: Clean shutdown
        NSLog("[SpecterQA] Runner stopped — cleaning up.")
        server.stop()
        cleanupPIDFile()
        NSLog("[SpecterQA] Clean shutdown complete.")
    }

    // MARK: - WDA-Proven Crash Mitigations

    /// Apply three mitigations that prevent XCTest runner SIGABRT crashes.
    ///
    /// These are proven in production by WebDriverAgent (Appium) which ships
    /// them in `UITestingUITests +setUp`. The crash mechanism:
    ///   1. App fires rapid NotificationCenter posts (borrow, download, sheet)
    ///   2. XCTest alert monitor fires, tries to log a debug message
    ///   3. `XCTRunnerIDESession.logDebugMessage:` serializes via NSKeyedArchiver
    ///   4. Archiver hits a deallocated AX element pointer → SIGABRT
    ///
    /// Mitigation 1: Replace XCTest's debug logger to bypass NSKeyedArchiver
    /// Mitigation 2: Disable remote query evaluation (secondary hang vector)
    /// Mitigation 3: Disable automatic screenshot/recording (race condition)
    private func applyCrashMitigations() {
        NSLog("[SpecterQA] Applying XCTest crash mitigations...")

        // Mitigation 1: Replace the XCTest debug logger.
        // XCSetDebugLogger is a private symbol in XCTest.framework.
        // We load it via dlsym and replace the default logger with our safe one.
        if let xcTestBundle = Bundle(identifier: "com.apple.dt.XCTest") ?? Bundle(for: XCTestCase.self) as Bundle?,
           let execPath = xcTestBundle.executablePath {
            if let handle = dlopen(execPath, RTLD_NOW | RTLD_NOLOAD) {
                defer { dlclose(handle) }
                typealias SetLoggerFn = @convention(c) (AnyObject?) -> Void
                if let sym = dlsym(handle, "XCSetDebugLogger") {
                    let setLogger = unsafeBitCast(sym, to: SetLoggerFn.self)
                    setLogger(SpecterQASafeDebugLogger())
                    NSLog("[SpecterQA] ✓ Debug logger replaced (NSKeyedArchiver crash prevented)")
                } else {
                    NSLog("[SpecterQA] ⚠ XCSetDebugLogger symbol not found — crash mitigation partial")
                }
            }
        }

        // Mitigation 2: Disable remote query evaluation.
        // Prevents XCTest from sending element queries back to the IDE
        // for additional filtering — a secondary crash/hang vector.
        UserDefaults.standard.set(true, forKey: "XCTDisableRemoteQueryEvaluation")
        NSLog("[SpecterQA] ✓ Remote query evaluation disabled")

        // Mitigation 3: Disable automatic diagnostic recordings.
        // XCTest captures screenshots on assertion failures. During
        // notification cascades, this races with the debug logger → SIGABRT.
        UserDefaults.standard.set(true, forKey: "DisableDiagnosticScreenRecordings")
        UserDefaults.standard.set(true, forKey: "DisableScreenshots")
        NSLog("[SpecterQA] ✓ Diagnostic recordings disabled")
    }

    // MARK: - Orphan cleanup

    private func cleanupOrphanProcess() {
        let fileManager = FileManager.default
        guard fileManager.fileExists(atPath: pidFilePath) else { return }

        guard let pidData = fileManager.contents(atPath: pidFilePath),
              let pidStr = String(data: pidData, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines),
              let pid = Int32(pidStr) else {
            NSLog("[SpecterQA] Stale PID file found but unreadable — removing.")
            try? fileManager.removeItem(atPath: pidFilePath)
            return
        }

        let result = kill(pid, 0)
        if result == 0 {
            NSLog("[SpecterQA] Killing orphaned runner process PID=\(pid)")
            kill(pid, SIGTERM)
            Thread.sleep(forTimeInterval: 0.5)
            kill(pid, SIGKILL)
        } else {
            NSLog("[SpecterQA] Stale PID file for dead process \(pid) — cleaning up.")
        }
        try? fileManager.removeItem(atPath: pidFilePath)
    }

    // MARK: - PID file

    private func writePIDFile() {
        let pid = ProcessInfo.processInfo.processIdentifier
        try? "\(pid)".write(toFile: pidFilePath, atomically: true, encoding: .utf8)
        NSLog("[SpecterQA] PID file written: \(pidFilePath) (PID=\(pid))")
    }

    private func cleanupPIDFile() {
        try? FileManager.default.removeItem(atPath: pidFilePath)
        NSLog("[SpecterQA] PID file cleaned up.")
    }

    // MARK: - Alert interruption monitors (v2, 7 monitors)

    /// Registers full set of iOS permission alert monitors.
    /// Must be called BEFORE app.launch() for monitors to intercept alerts.
    private func registerAlertDismissers() {
        // Notifications
        addUIInterruptionMonitor(withDescription: "Notifications permission") { alert in
            if alert.buttons["Allow"].exists {
                alert.buttons["Allow"].tap(); return true
            }
            return false
        }

        // Photos — full access
        addUIInterruptionMonitor(withDescription: "Photos permission") { alert in
            if alert.buttons["Allow Full Access"].exists {
                alert.buttons["Allow Full Access"].tap(); return true
            }
            if alert.buttons["Allow Access to All Photos"].exists {
                alert.buttons["Allow Access to All Photos"].tap(); return true
            }
            return false
        }

        // Camera
        addUIInterruptionMonitor(withDescription: "Camera permission") { alert in
            if alert.buttons["OK"].exists     { alert.buttons["OK"].tap();     return true }
            if alert.buttons["Allow"].exists  { alert.buttons["Allow"].tap();  return true }
            return false
        }

        // Location
        addUIInterruptionMonitor(withDescription: "Location permission") { alert in
            if alert.buttons["Allow While Using App"].exists {
                alert.buttons["Allow While Using App"].tap(); return true
            }
            if alert.buttons["Allow Once"].exists {
                alert.buttons["Allow Once"].tap(); return true
            }
            return false
        }

        // Microphone
        addUIInterruptionMonitor(withDescription: "Microphone permission") { alert in
            if alert.buttons["OK"].exists    { alert.buttons["OK"].tap();    return true }
            if alert.buttons["Allow"].exists { alert.buttons["Allow"].tap(); return true }
            return false
        }

        // Contacts
        addUIInterruptionMonitor(withDescription: "Contacts permission") { alert in
            if alert.buttons["OK"].exists    { alert.buttons["OK"].tap();    return true }
            if alert.buttons["Allow"].exists { alert.buttons["Allow"].tap(); return true }
            return false
        }

        // Generic fallback — handles any unknown system alert
        addUIInterruptionMonitor(withDescription: "Generic system alert") { alert in
            let preferred = ["Allow", "OK", "Continue", "Allow Full Access",
                             "Allow While Using App", "Allow Once", "Done"]
            for label in preferred {
                if alert.buttons[label].exists {
                    alert.buttons[label].tap(); return true
                }
            }
            let denials: Set<String> = ["Don't Allow", "Deny", "Cancel", "Not Now", "Never"]
            for btn in alert.buttons.allElementsBoundByIndex where !denials.contains(btn.label) {
                btn.tap(); return true
            }
            return false
        }

        NSLog("[SpecterQA] Registered 7 interruption monitors.")
    }
}

// MARK: - Safe Debug Logger

/// Replaces XCTest's default debug logger to prevent NSKeyedArchiver SIGABRT.
///
/// The default XCTest logger (`XCTDefaultDebugLogHandler`) serializes debug
/// messages via NSKeyedArchiver. When the message contains a reference to
/// a deallocated AX element (common during notification cascades), the
/// archiver hits a bad pointer and crashes with SIGABRT.
///
/// This logger routes messages to NSLog instead — no archival, no crash.
/// This is the same approach used by WebDriverAgent in production.
@objc class SpecterQASafeDebugLogger: NSObject {
    @objc func logDebugMessage(_ message: String) {
        // Route to NSLog (safe) instead of NSKeyedArchiver (crashes)
        NSLog("[SpecterQA-XCTDebug] %@", message)
    }
}
