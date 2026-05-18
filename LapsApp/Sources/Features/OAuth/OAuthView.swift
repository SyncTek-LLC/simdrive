import SwiftUI

// OAuthView — feature area 2 from spec §3 §2.
// Presents Apple, Google, and email/password sign-in buttons.
// All identifiers follow spec naming: auth_<element>_<action>.
//
// The mock sheet overlay simulates the visual cue of an out-of-process Safari sheet
// so SimDrive's `oauth-google-happy` journey has something meaningful to observe.
struct OAuthView: View {
    @State private var viewModel = OAuthViewModel()

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                switch viewModel.authState {
                case .authenticated(let provider, let token):
                    authenticatedView(provider: provider, token: token)
                default:
                    loginForm
                }
            }
            .navigationTitle("Sign In")
            .accessibilityIdentifier("auth_screen")
        }
    }

    // MARK: - Login form

    private var loginForm: some View {
        ScrollView {
            VStack(spacing: 24) {
                Spacer(minLength: 40)

                Image(systemName: "figure.run.circle.fill")
                    .font(.system(size: 80))
                    .foregroundStyle(.blue)
                    .accessibilityIdentifier("auth_logo")

                Text("Welcome to LapsApp")
                    .font(.title2.bold())

                // Email/password section
                VStack(spacing: 12) {
                    TextField("Email", text: $viewModel.emailInput)
                        .textContentType(.emailAddress)
                        .keyboardType(.emailAddress)
                        .autocapitalization(.none)
                        .padding()
                        .background(Color(.systemGray6))
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                        .accessibilityIdentifier("auth_email_field")

                    SecureField("Password", text: $viewModel.passwordInput)
                        .textContentType(.password)
                        .padding()
                        .background(Color(.systemGray6))
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                        .accessibilityIdentifier("auth_password_field")

                    Button {
                        Task { await viewModel.signInWithEmail() }
                    } label: {
                        signInButtonLabel(text: "Sign In", isLoading: isLoadingEmail)
                    }
                    .disabled(isLoadingAny)
                    .accessibilityIdentifier("auth_email_signin_button")
                }
                .padding(.horizontal)

                // Divider
                HStack {
                    Rectangle().frame(height: 1).foregroundStyle(Color(.separator))
                    Text("or").foregroundStyle(.secondary).padding(.horizontal, 8)
                    Rectangle().frame(height: 1).foregroundStyle(Color(.separator))
                }
                .padding(.horizontal)

                // Social sign-in buttons
                VStack(spacing: 12) {
                    Button {
                        Task { await viewModel.signInWithApple() }
                    } label: {
                        HStack {
                            Image(systemName: "applelogo")
                            Text(isLoadingApple ? "Signing in…" : "Sign in with Apple")
                            if isLoadingApple { ProgressView().tint(.white).padding(.leading, 4) }
                        }
                        .frame(maxWidth: .infinity)
                        .padding()
                        .background(Color.black)
                        .foregroundStyle(.white)
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                    }
                    .disabled(isLoadingAny)
                    .accessibilityIdentifier("auth_apple_button")

                    Button {
                        Task { await viewModel.signInWithGoogle() }
                    } label: {
                        HStack {
                            Image(systemName: "globe")
                            Text(isLoadingGoogle ? "Opening browser…" : "Sign in with Google")
                            if isLoadingGoogle { ProgressView().padding(.leading, 4) }
                        }
                        .frame(maxWidth: .infinity)
                        .padding()
                        .background(Color(.systemGray6))
                        .foregroundStyle(.primary)
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                        .overlay(
                            RoundedRectangle(cornerRadius: 10)
                                .stroke(Color(.separator), lineWidth: 1)
                        )
                    }
                    .disabled(isLoadingAny)
                    .accessibilityIdentifier("auth_google_button")
                }
                .padding(.horizontal)

                // Error display
                if case .failed(_, let reason) = viewModel.authState {
                    Text(reason)
                        .foregroundStyle(.red)
                        .font(.footnote)
                        .accessibilityIdentifier("auth_error_label")
                }

                Spacer(minLength: 40)
            }
            // Mock Safari-sheet overlay for Google OAuth simulation
            .overlay(alignment: .bottom) {
                if case .simulatingExternalSheet(let provider) = viewModel.authState {
                    mockSafariSheet(provider: provider)
                        .transition(.move(edge: .bottom))
                        .animation(.easeInOut(duration: 0.3), value: viewModel.authState)
                }
            }
        }
    }

    // MARK: - Authenticated view

    private func authenticatedView(provider: OAuthViewModel.Provider, token: String) -> some View {
        VStack(spacing: 24) {
            Spacer()
            Image(systemName: "checkmark.seal.fill")
                .font(.system(size: 80))
                .foregroundStyle(.green)
            Text("Signed in via \(provider.rawValue)")
                .font(.title2.bold())
                .accessibilityIdentifier("auth_success_label")
            // Token display — mock value, clearly prefixed with "mock_"
            Text(token)
                .font(.caption.monospaced())
                .foregroundStyle(.secondary)
                .accessibilityIdentifier("auth_mock_token_label")
            Button("Sign Out") { viewModel.signOut() }
                .buttonStyle(.bordered)
                .accessibilityIdentifier("auth_signout_button")
            Spacer()
        }
    }

    // MARK: - Mock Safari-sheet overlay
    // WHY: SimDrive's `oauth-google-happy` journey needs a visual target to tap ("Allow") during
    // the simulated out-of-process sheet phase. This overlay provides that surface.

    private func mockSafariSheet(provider: OAuthViewModel.Provider) -> some View {
        VStack(spacing: 16) {
            Capsule()
                .frame(width: 36, height: 4)
                .foregroundStyle(Color(.separator))

            Text("[\(provider.rawValue) OAuth — Simulated Browser Sheet]")
                .font(.headline)
                .accessibilityIdentifier("auth_mock_sheet_title")

            Text("In production this opens an out-of-process Safari sheet.\nSimDrive observes and taps the Allow button here.")
                .font(.caption)
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)

            HStack(spacing: 16) {
                Button("Cancel") { viewModel.cancelGoogleAuth() }
                    .buttonStyle(.bordered)
                    .foregroundStyle(.red)
                    .accessibilityIdentifier("auth_mock_sheet_cancel")

                Button("Allow") {
                    // "Allow" completes the simulated Google flow
                    Task { await viewModel.signInWithGoogle() }
                }
                .buttonStyle(.borderedProminent)
                .accessibilityIdentifier("auth_mock_sheet_allow")
            }
        }
        .padding()
        .background(
            RoundedRectangle(cornerRadius: 20)
                .fill(Color(.systemBackground))
                .shadow(radius: 20)
        )
        .padding(.horizontal)
        .padding(.bottom, 8)
    }

    // MARK: - Helpers

    private func signInButtonLabel(text: String, isLoading: Bool) -> some View {
        HStack {
            Text(isLoading ? "Signing in…" : text)
            if isLoading { ProgressView().tint(.white).padding(.leading, 4) }
        }
        .frame(maxWidth: .infinity)
        .padding()
        .background(Color.blue)
        .foregroundStyle(.white)
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }

    private var isLoadingAny: Bool {
        if case .loading = viewModel.authState { return true }
        if case .simulatingExternalSheet = viewModel.authState { return true }
        return false
    }

    private var isLoadingEmail: Bool {
        if case .loading(.email) = viewModel.authState { return true }
        return false
    }

    private var isLoadingApple: Bool {
        if case .loading(.apple) = viewModel.authState { return true }
        if case .simulatingExternalSheet(.apple) = viewModel.authState { return true }
        return false
    }

    private var isLoadingGoogle: Bool {
        if case .loading(.google) = viewModel.authState { return true }
        if case .simulatingExternalSheet(.google) = viewModel.authState { return true }
        return false
    }
}
