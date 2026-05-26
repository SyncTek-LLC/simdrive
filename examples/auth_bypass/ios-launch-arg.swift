// SimDrive auth_bypass recipe — iOS launch-arg integration snippet
//
// Drop a variant of this into your AppDelegate / App entry point. The
// pattern: at launch, look for a sentinel argument (here: `SimDriveAuthInject`)
// and a fixture-user identifier; if both present, hydrate the session with
// a deterministic fake user instead of prompting for real auth.
//
// CRITICAL — release-build gating
// ────────────────────────────────
// This code path MUST be excluded from App Store builds. The two options:
//
//   (a) Wrap the entire bypass branch in `#if DEBUG` — relies on Xcode's
//       per-configuration `SWIFT_ACTIVE_COMPILATION_CONDITIONS`. Release
//       configurations strip the branch at compile time.
//
//   (b) Wrap in `#if BYPASS_AUTH` — a custom flag set only on a dedicated
//       `Recording` build configuration. Useful if you want to record
//       against a release-like binary (optimized, no DEBUG asserts) without
//       letting bypass code into actual App Store builds.
//
// `#if DEBUG` is the safer default. Use the custom flag only if you have
// CI that asserts the flag is absent from the App Store archive.
//
// One more belt-and-braces safety: the snippet also verifies the bundle
// identifier ends in a `.dev` / `.recording` suffix. A release build
// somehow shipped with DEBUG on would still refuse to hydrate the bypass
// because the bundle ID won't match.

import Foundation
import UIKit

#if DEBUG

/// Returns the fixture-user id from launch args if SimDriveAuthInject is set.
/// Returns nil when the bypass is not requested OR the bundle ID looks like
/// a production build (defense in depth — DEBUG should already gate this).
func fixtureUserIdFromLaunchArgs() -> String? {
    let args = CommandLine.arguments
    guard args.contains("SimDriveAuthInject") else { return nil }

    // Defense in depth — refuse to hydrate against a non-dev bundle.
    let bundleID = Bundle.main.bundleIdentifier ?? ""
    let allowedSuffixes = [".dev", ".recording", ".local"]
    guard allowedSuffixes.contains(where: { bundleID.hasSuffix($0) }) else {
        assertionFailure(
            "SimDriveAuthInject seen on non-dev bundle \(bundleID); refusing."
        )
        return nil
    }

    // Pull the fixture user id that follows `--fixture-user <id>`.
    guard let flagIdx = args.firstIndex(of: "--fixture-user"),
          flagIdx + 1 < args.count else {
        // No id specified — default to a known seed user.
        return "u-fixture-001"
    }
    return args[flagIdx + 1]
}

#endif

// MARK: - Wiring into your auth boot path

/// Call this from your app's auth-state initialization (AppDelegate
/// didFinishLaunching, App.init, AuthCoordinator.start, whatever your
/// architecture calls it). In release builds the function compiles away
/// to `return false` and the live auth flow runs unchanged.
@discardableResult
func tryHydrateBypassSession(authSession: AuthSession) -> Bool {
    #if DEBUG
    guard let userID = fixtureUserIdFromLaunchArgs() else { return false }

    // Load the fixture user shipped alongside the test build. The shape
    // here must match what your auth code paths expect — adapt as needed.
    let fixture = SeedUserLoader.load(id: userID)

    // Hydrate session in-process so the rest of the app never sees the
    // login screen. The dev-token here is matched server-side by the
    // backend bypass code path (see docker-compose.recording.yml).
    authSession.hydrate(
        user: fixture,
        accessToken: ProcessInfo.processInfo.environment["AUTH_BYPASS_TOKEN"]
            ?? "local-recording-only-not-for-prod"
    )
    NSLog("[SimDriveAuthInject] hydrated fixture user \(userID)")
    return true
    #else
    return false
    #endif
}

// MARK: - Type placeholders so the snippet type-checks in isolation
//
// Delete these and substitute your real types when wiring this into a
// real app. They exist here so a fresh reader can read the recipe without
// SwiftUI / your domain types in scope.

struct FixtureUser {
    let id: String
    let email: String
    let displayName: String
    let role: String
}

protocol AuthSession {
    func hydrate(user: FixtureUser, accessToken: String)
}

enum SeedUserLoader {
    static func load(id: String) -> FixtureUser {
        // Real implementation: read seed-dev-users.json from the bundle,
        // pick the entry matching `id`, decode into FixtureUser. Stubbed
        // here so the snippet stands alone.
        FixtureUser(
            id: id,
            email: "\(id)@fixtures.local",
            displayName: "Fixture User",
            role: "member"
        )
    }
}
