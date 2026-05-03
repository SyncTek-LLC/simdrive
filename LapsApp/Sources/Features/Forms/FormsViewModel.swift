import Foundation
import Observation

// FormsViewModel — sign-up form with debounced async email validation.
//
// Exercises record/replay reliability across async server states per spec §3 §7.
// All validation is in-process — no real network calls. The email-uniqueness check
// uses a hardcoded "taken" list to produce deterministic pass/fail outcomes for replays.

@Observable
@MainActor
final class FormsViewModel {

    // MARK: - Field state

    var emailInput: String = "" {
        didSet { scheduleEmailValidation() }
    }
    var passwordInput: String = ""
    var passwordConfirmInput: String = ""
    var ageInput: String = ""
    var termsAccepted: Bool = false

    // MARK: - Validation state

    enum FieldError: Equatable {
        case emailTaken
        case emailInvalidFormat
        case weakPassword       // under 8 chars
        case passwordMismatch
        case ageMissing
        case ageInvalid
        case termsRequired
    }

    private(set) var emailValidationInProgress: Bool = false
    private(set) var emailError: FieldError? = nil
    private(set) var passwordError: FieldError? = nil
    private(set) var passwordConfirmError: FieldError? = nil
    private(set) var ageError: FieldError? = nil

    enum SubmitState: Equatable {
        case idle
        case submitting
        case success
        case failed(String)
    }

    private(set) var submitState: SubmitState = .idle

    // MARK: - Known-taken emails (deterministic for journey replays)
    // WHY: the journey `signup-with-validation` taps "taken@lapsapp.test" to get the email-taken error
    // and exercises SSIM masking around the inline error text per spec §3 §7.
    private let knownTakenEmails: Set<String> = ["taken@lapsapp.test", "test@test.com"]

    // MARK: - Debounced email validation

    private var emailDebounceTask: Task<Void, Never>?

    private func scheduleEmailValidation() {
        emailDebounceTask?.cancel()
        emailError = nil
        guard !emailInput.isEmpty else { return }

        // 500 ms debounce per spec §3 cycle-2 requirements
        emailValidationInProgress = true
        emailDebounceTask = Task { [weak self] in
            guard let self else { return }
            try? await Task.sleep(for: .milliseconds(500))
            guard !Task.isCancelled else { return }
            self.validateEmail()
        }
    }

    private func validateEmail() {
        emailValidationInProgress = false
        guard emailInput.contains("@"), emailInput.contains(".") else {
            emailError = .emailInvalidFormat
            return
        }
        if knownTakenEmails.contains(emailInput.lowercased()) {
            emailError = .emailTaken
        }
    }

    // MARK: - Submission

    func submit() async {
        // Validate all fields synchronously before submitting
        passwordError = nil
        passwordConfirmError = nil
        ageError = nil

        if passwordInput.count < 8 {
            passwordError = .weakPassword
        }
        if passwordInput != passwordConfirmInput {
            passwordConfirmError = .passwordMismatch
        }
        let ageValue = Int(ageInput)
        if ageInput.isEmpty {
            ageError = .ageMissing
        } else if ageValue == nil || (ageValue ?? 0) < 13 || (ageValue ?? 0) > 120 {
            ageError = .ageInvalid
        }

        let hasFieldErrors = emailError != nil || passwordError != nil ||
                             passwordConfirmError != nil || ageError != nil
        let termsError = !termsAccepted
        guard !hasFieldErrors, !termsError else {
            if termsError { submitState = .failed("Please accept the terms") }
            return
        }

        submitState = .submitting
        try? await Task.sleep(for: .milliseconds(600)) // simulate submit round-trip
        submitState = .success
    }

    func reset() {
        emailInput = ""
        passwordInput = ""
        passwordConfirmInput = ""
        ageInput = ""
        termsAccepted = false
        emailError = nil
        passwordError = nil
        passwordConfirmError = nil
        ageError = nil
        submitState = .idle
        emailValidationInProgress = false
        emailDebounceTask?.cancel()
    }
}
