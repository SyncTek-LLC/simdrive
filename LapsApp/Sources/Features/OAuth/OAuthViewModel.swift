import Foundation
import Observation

// OAuthViewModel — mock authentication flow for cycle-2 LapsApp.
//
// WHY MOCKED: Real OAuth (Sign in with Apple + Google ASWebAuthenticationSession) requires
// a provisioning profile, entitlements, and real redirect URIs — all of which make CI builds
// fragile and require TestFlight provisioning before first run. The mock faithfully simulates
// the user-visible flow (button tap → loading state → simulated Safari sheet → success/failure)
// without real tokens. Real OAuth wiring is deferred to a future cycle.
//
// Mock credential marker: all fake tokens use the prefix "mock_" to make it impossible to
// confuse them with production credentials in logs or state dumps.

@Observable
@MainActor
final class OAuthViewModel {

    // MARK: - Auth state

    enum AuthState: Equatable {
        case idle
        case loading(provider: Provider)
        case simulatingExternalSheet(provider: Provider) // represents out-of-process Safari sheet
        case authenticated(provider: Provider, mockToken: String)
        case failed(provider: Provider, reason: String)
    }

    enum Provider: String, Equatable {
        case apple  = "Apple"
        case google = "Google"
        case email  = "Email"
    }

    var authState: AuthState = .idle
    var emailInput: String = ""
    var passwordInput: String = ""

    // MARK: - Actions

    // Simulates Sign in with Apple — stays in-process (real Apple sheet is in-process).
    // Adds a 600 ms delay to mimic the sheet presentation + biometric confirmation.
    func signInWithApple() async {
        authState = .loading(provider: .apple)
        try? await Task.sleep(for: .milliseconds(300))
        authState = .simulatingExternalSheet(provider: .apple)
        try? await Task.sleep(for: .milliseconds(600))
        authState = .authenticated(
            provider: .apple,
            mockToken: "mock_apple_id_\(UUID().uuidString.prefix(8).lowercased())"
        )
    }

    // Simulates Google OAuth — models the out-of-process ASWebAuthenticationSession that
    // opens a Safari sheet. The 1.2 s delay represents the Safari sheet lifecycle.
    // SimDrive's `oauth-google-happy` journey exercises cross-process observe + tap here.
    func signInWithGoogle() async {
        authState = .loading(provider: .google)
        try? await Task.sleep(for: .milliseconds(200))
        authState = .simulatingExternalSheet(provider: .google)
        // The Safari-sheet simulation window: SimDrive observes + taps the mock "Allow" button
        try? await Task.sleep(for: .milliseconds(1200))
        authState = .authenticated(
            provider: .google,
            mockToken: "mock_google_oauth2_\(UUID().uuidString.prefix(8).lowercased())"
        )
    }

    // Simulates cancelling mid-flow (used by `oauth-google-cancel` regression journey).
    func cancelGoogleAuth() {
        guard case .simulatingExternalSheet(let provider) = authState else { return }
        authState = .failed(provider: provider, reason: "User cancelled")
    }

    // Email/password login — exercises UITextField focus + HID path per spec §3 §2.
    func signInWithEmail() async {
        guard !emailInput.isEmpty, !passwordInput.isEmpty else {
            authState = .failed(provider: .email, reason: "Email or password missing")
            return
        }
        authState = .loading(provider: .email)
        // 400 ms simulates an async auth network call (in-process timer — no real network)
        try? await Task.sleep(for: .milliseconds(400))
        authState = .authenticated(
            provider: .email,
            mockToken: "mock_email_\(UUID().uuidString.prefix(8).lowercased())"
        )
    }

    func signOut() {
        authState = .idle
        emailInput = ""
        passwordInput = ""
    }
}
