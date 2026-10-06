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
/// window since launch calls `fatalError`. A tap count, not a timer, so a
/// replay of a 15-tap recording crashes deterministically at tap N no matter
/// how fast or slow the replay runs. No `#if DEBUG` fence: this project
/// defines no DEBUG compilation condition, and TestKitApp is a test fixture
/// that never ships — the env-var gate is the only switch.
enum CrashInjector {
    static func armIfRequested() {
        guard
            let raw = ProcessInfo.processInfo.environment["SIMDRIVE_CRASH_ON_TAP"],
            let crashAt = Int(raw), crashAt > 0
        else { return }
        // The window exists by the time the root view appears, but attach on
        // the next runloop turn so the scene has finished connecting it.
        DispatchQueue.main.async { attach(crashAt: crashAt) }
    }

    private static var counter: TapCounter?

    private static func attach(crashAt: Int) {
        guard counter == nil else { return }
        let window = UIApplication.shared.connectedScenes
            .compactMap { ($0 as? UIWindowScene)?.windows.first }
            .first
        guard let window else { return }
        let c = TapCounter(crashAt: crashAt)
        window.addGestureRecognizer(c.recognizer)
        counter = c
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
