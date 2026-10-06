import SwiftUI
import UIKit

@main
struct TestKitApp: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
                .onAppear { CrashInjector.armIfRequested() }
        }
    }
}

/// Deliberate crash trigger for SimDrive's live replay crash cross-check
/// (FU-2026-071). Inert unless the app is launched with the environment
/// variable `SIMDRIVE_CRASH_ON_TAP=<N>` (from the host:
/// `SIMCTL_CHILD_SIMDRIVE_CRASH_ON_TAP=<N> xcrun simctl launch ...`, which
/// any simctl launch inherits), in which case the N-th tap anywhere in the
/// window since arming calls `fatalError`. A tap count, not a timer, so a
/// replay of a 15-tap recording crashes at tap N however fast or slow the
/// replay runs, provided the host waits for the arming marker
/// (`Library/Caches/simdrive-crash-armed` in the app's data container)
/// before the first tap. No `#if DEBUG` fence: this project
/// defines no DEBUG compilation condition, and TestKitApp is a test fixture
/// that never ships — the env-var gate is the only switch.
enum CrashInjector {
    static func armIfRequested() {
        guard
            let raw = ProcessInfo.processInfo.environment["SIMDRIVE_CRASH_ON_TAP"],
            let crashAt = Int(raw), crashAt > 0
        else { return }
        attach(crashAt: crashAt, attemptsLeft: 50)
    }

    private static var counter: TapCounter?

    /// Retries every 100 ms until the key window exists: on a slow CI
    /// simulator the root view can appear before the scene has a key window,
    /// and a counter attached late misses the first taps (seen on a GitHub
    /// runner: the crash landed on tap 14 instead of 10).
    private static func attach(crashAt: Int, attemptsLeft: Int) {
        guard counter == nil else { return }
        let window = UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap(\.windows)
            .first(where: \.isKeyWindow)
        guard let window else {
            if attemptsLeft > 0 {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) {
                    attach(crashAt: crashAt, attemptsLeft: attemptsLeft - 1)
                }
            }
            return
        }
        let c = TapCounter(crashAt: crashAt)
        window.addGestureRecognizer(c.recognizer)
        counter = c
        // Arming marker: the host waits for this file before tapping, so no
        // tap can land before the counter exists.
        let marker = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("simdrive-crash-armed")
        try? String(crashAt).write(to: marker, atomically: true, encoding: .utf8)
    }

    /// Counts every tap on the window without consuming it: the recognizer
    /// never cancels touches and recognizes alongside every other gesture,
    /// so the app under test behaves exactly as it would without it.
    private final class TapCounter: NSObject, UIGestureRecognizerDelegate {
        let crashAt: Int
        private(set) var taps = 0
        let recognizer = UITapGestureRecognizer()

        init(crashAt: Int) {
            self.crashAt = crashAt
            super.init()
            recognizer.cancelsTouchesInView = false
            recognizer.delaysTouchesEnded = false
            recognizer.delegate = self
            recognizer.addTarget(self, action: #selector(tapped))
        }

        @objc private func tapped() {
            taps += 1
            if taps >= crashAt {
                fatalError("SimDrive crash injection: tap \(taps) of SIMDRIVE_CRASH_ON_TAP=\(crashAt)")
            }
        }

        func gestureRecognizer(
            _ gestureRecognizer: UIGestureRecognizer,
            shouldRecognizeSimultaneouslyWith other: UIGestureRecognizer
        ) -> Bool { true }
    }
}
