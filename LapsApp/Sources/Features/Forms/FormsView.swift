import SwiftUI

// FormsView — feature area 7: sign-up form with async validation.
// Exercises `type_text` + debounce + inline error states per spec §3 §7.
// Identifiers: signup_email, signup_password, signup_submit, signup_error_<field>.
struct FormsView: View {
    @State private var viewModel = FormsViewModel()

    var body: some View {
        NavigationStack {
            Group {
                if viewModel.submitState == .success {
                    successView
                } else {
                    formContent
                }
            }
            .navigationTitle("Sign Up")
            .accessibilityIdentifier("signup_screen")
        }
    }

    // MARK: - Form

    private var formContent: some View {
        ScrollView {
            VStack(spacing: 16) {
                // Email
                VStack(alignment: .leading, spacing: 4) {
                    TextField("Email", text: $viewModel.emailInput)
                        .textContentType(.emailAddress)
                        .keyboardType(.emailAddress)
                        .autocapitalization(.none)
                        .padding()
                        .background(Color(.systemGray6))
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                        .accessibilityIdentifier("signup_email")
                        .overlay(alignment: .trailing) {
                            if viewModel.emailValidationInProgress {
                                ProgressView()
                                    .padding(.trailing, 12)
                                    .accessibilityIdentifier("signup_email_checking")
                            }
                        }
                    if let err = viewModel.emailError {
                        Text(emailErrorText(err))
                            .font(.caption)
                            .foregroundStyle(.red)
                            .accessibilityIdentifier("signup_error_email")
                    }
                }

                // Password
                VStack(alignment: .leading, spacing: 4) {
                    SecureField("Password (min 8 chars)", text: $viewModel.passwordInput)
                        .textContentType(.newPassword)
                        .padding()
                        .background(Color(.systemGray6))
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                        .accessibilityIdentifier("signup_password")
                    if let err = viewModel.passwordError {
                        Text(passwordErrorText(err))
                            .font(.caption)
                            .foregroundStyle(.red)
                            .accessibilityIdentifier("signup_error_password")
                    }
                }

                // Password confirm
                VStack(alignment: .leading, spacing: 4) {
                    SecureField("Confirm Password", text: $viewModel.passwordConfirmInput)
                        .textContentType(.newPassword)
                        .padding()
                        .background(Color(.systemGray6))
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                        .accessibilityIdentifier("signup_password_confirm")
                    if let err = viewModel.passwordConfirmError {
                        Text(passwordConfirmErrorText(err))
                            .font(.caption)
                            .foregroundStyle(.red)
                            .accessibilityIdentifier("signup_error_password_confirm")
                    }
                }

                // Age
                VStack(alignment: .leading, spacing: 4) {
                    TextField("Age", text: $viewModel.ageInput)
                        .keyboardType(.numberPad)
                        .padding()
                        .background(Color(.systemGray6))
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                        .accessibilityIdentifier("signup_age")
                    if let err = viewModel.ageError {
                        Text(ageErrorText(err))
                            .font(.caption)
                            .foregroundStyle(.red)
                            .accessibilityIdentifier("signup_error_age")
                    }
                }

                // Terms checkbox
                Toggle("I agree to the Terms of Service", isOn: $viewModel.termsAccepted)
                    .padding()
                    .background(Color(.systemGray6))
                    .clipShape(RoundedRectangle(cornerRadius: 10))
                    .accessibilityIdentifier("signup_terms_toggle")

                // Submit error
                if case .failed(let reason) = viewModel.submitState {
                    Text(reason)
                        .foregroundStyle(.red)
                        .font(.footnote)
                        .accessibilityIdentifier("signup_error_terms")
                }

                // Submit button
                Button {
                    Task { await viewModel.submit() }
                } label: {
                    HStack {
                        Text(viewModel.submitState == .submitting ? "Creating account…" : "Create Account")
                        if viewModel.submitState == .submitting {
                            ProgressView().tint(.white).padding(.leading, 4)
                        }
                    }
                    .frame(maxWidth: .infinity)
                    .padding()
                    .background(Color.blue)
                    .foregroundStyle(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 10))
                }
                .disabled(viewModel.submitState == .submitting || viewModel.emailValidationInProgress)
                .accessibilityIdentifier("signup_submit")
            }
            .padding()
        }
    }

    // MARK: - Success view

    private var successView: some View {
        VStack(spacing: 24) {
            Spacer()
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 80))
                .foregroundStyle(.green)
            Text("Account Created!")
                .font(.title.bold())
                .accessibilityIdentifier("signup_success_label")
            Button("Start Over") { viewModel.reset() }
                .buttonStyle(.bordered)
                .accessibilityIdentifier("signup_reset_button")
            Spacer()
        }
    }

    // MARK: - Error text helpers

    private func emailErrorText(_ err: FormsViewModel.FieldError) -> String {
        switch err {
        case .emailTaken:         return "This email is already taken"
        case .emailInvalidFormat: return "Please enter a valid email address"
        default:                  return "Invalid email"
        }
    }

    private func passwordErrorText(_ err: FormsViewModel.FieldError) -> String {
        switch err {
        case .weakPassword: return "Password must be at least 8 characters"
        default:            return "Invalid password"
        }
    }

    private func passwordConfirmErrorText(_ err: FormsViewModel.FieldError) -> String {
        switch err {
        case .passwordMismatch: return "Passwords do not match"
        default:                return "Password error"
        }
    }

    private func ageErrorText(_ err: FormsViewModel.FieldError) -> String {
        switch err {
        case .ageMissing: return "Age is required"
        case .ageInvalid: return "Please enter a valid age (13–120)"
        default:          return "Invalid age"
        }
    }
}
