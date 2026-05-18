import XCTest
@testable import LapsApp

@MainActor
final class FormsViewModelTests: XCTestCase {

    func test_initialState_allFieldsEmpty() {
        let vm = FormsViewModel()
        XCTAssertTrue(vm.emailInput.isEmpty)
        XCTAssertTrue(vm.passwordInput.isEmpty)
        XCTAssertFalse(vm.termsAccepted)
        XCTAssertEqual(vm.submitState, .idle)
        XCTAssertNil(vm.emailError)
    }

    func test_emailValidation_knownTakenEmail_producesEmailTakenError() async {
        let vm = FormsViewModel()
        vm.emailInput = "taken@lapsapp.test"
        // Wait for 500ms debounce + buffer
        try? await Task.sleep(for: .milliseconds(700))
        XCTAssertEqual(vm.emailError, .emailTaken)
        XCTAssertFalse(vm.emailValidationInProgress)
    }

    func test_emailValidation_invalidFormat_producesFormatError() async {
        let vm = FormsViewModel()
        vm.emailInput = "notanemail"
        try? await Task.sleep(for: .milliseconds(700))
        XCTAssertEqual(vm.emailError, .emailInvalidFormat)
    }

    func test_emailValidation_validEmail_noError() async {
        let vm = FormsViewModel()
        vm.emailInput = "runner@lapsapp.test"
        try? await Task.sleep(for: .milliseconds(700))
        XCTAssertNil(vm.emailError)
    }

    func test_submit_weakPassword_fails() async {
        let vm = FormsViewModel()
        vm.emailInput = "runner@lapsapp.test"
        vm.passwordInput = "short"
        vm.passwordConfirmInput = "short"
        vm.ageInput = "25"
        vm.termsAccepted = true
        await vm.submit()
        XCTAssertEqual(vm.passwordError, .weakPassword)
        XCTAssertNotEqual(vm.submitState, .success)
    }

    func test_submit_passwordMismatch_fails() async {
        let vm = FormsViewModel()
        vm.emailInput = "runner@lapsapp.test"
        vm.passwordInput = "Password123"
        vm.passwordConfirmInput = "DifferentPassword"
        vm.ageInput = "25"
        vm.termsAccepted = true
        await vm.submit()
        XCTAssertEqual(vm.passwordConfirmError, .passwordMismatch)
    }

    func test_submit_validForm_succeeds() async {
        let vm = FormsViewModel()
        vm.emailInput = "newrunner@lapsapp.test"
        vm.passwordInput = "Password123"
        vm.passwordConfirmInput = "Password123"
        vm.ageInput = "28"
        vm.termsAccepted = true
        await vm.submit()
        XCTAssertEqual(vm.submitState, .success)
    }

    func test_submit_termsNotAccepted_fails() async {
        let vm = FormsViewModel()
        vm.emailInput = "runner@lapsapp.test"
        vm.passwordInput = "Password123"
        vm.passwordConfirmInput = "Password123"
        vm.ageInput = "25"
        vm.termsAccepted = false
        await vm.submit()
        XCTAssertNotEqual(vm.submitState, .success)
    }

    func test_reset_clearsAllState() async {
        let vm = FormsViewModel()
        vm.emailInput = "runner@lapsapp.test"
        vm.passwordInput = "Password123"
        vm.passwordConfirmInput = "Password123"
        vm.ageInput = "25"
        vm.termsAccepted = true
        await vm.submit()
        vm.reset()
        XCTAssertTrue(vm.emailInput.isEmpty)
        XCTAssertEqual(vm.submitState, .idle)
        XCTAssertNil(vm.emailError)
    }
}
