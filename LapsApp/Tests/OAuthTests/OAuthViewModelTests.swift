import XCTest
@testable import LapsApp

// OAuthViewModelTests — exercises mock auth flows for all three providers.
// Tests run on the main actor because OAuthViewModel is @Observable @MainActor.
@MainActor
final class OAuthViewModelTests: XCTestCase {

    func test_initialState_isIdle() {
        let vm = OAuthViewModel()
        XCTAssertEqual(vm.authState, .idle)
        XCTAssertTrue(vm.emailInput.isEmpty)
        XCTAssertTrue(vm.passwordInput.isEmpty)
    }

    func test_signInWithApple_endsAuthenticated() async {
        let vm = OAuthViewModel()
        await vm.signInWithApple()
        if case .authenticated(let provider, let token) = vm.authState {
            XCTAssertEqual(provider, .apple)
            XCTAssertTrue(token.hasPrefix("mock_apple_id_"), "Token must carry mock_ prefix, got: \(token)")
        } else {
            XCTFail("Expected .authenticated after Apple sign-in, got \(vm.authState)")
        }
    }

    func test_signInWithGoogle_endsAuthenticated() async {
        let vm = OAuthViewModel()
        await vm.signInWithGoogle()
        if case .authenticated(let provider, let token) = vm.authState {
            XCTAssertEqual(provider, .google)
            XCTAssertTrue(token.hasPrefix("mock_google_oauth2_"), "Token must carry mock_ prefix")
        } else {
            XCTFail("Expected .authenticated after Google sign-in, got \(vm.authState)")
        }
    }

    func test_signInWithEmail_emptyFields_fails() async {
        let vm = OAuthViewModel()
        await vm.signInWithEmail()
        if case .failed(let provider, let reason) = vm.authState {
            XCTAssertEqual(provider, .email)
            XCTAssertFalse(reason.isEmpty)
        } else {
            XCTFail("Expected .failed with empty fields, got \(vm.authState)")
        }
    }

    func test_signInWithEmail_validFields_endsAuthenticated() async {
        let vm = OAuthViewModel()
        vm.emailInput = "runner@lapsapp.test"
        vm.passwordInput = "TestPass123"
        await vm.signInWithEmail()
        if case .authenticated(let provider, let token) = vm.authState {
            XCTAssertEqual(provider, .email)
            XCTAssertTrue(token.hasPrefix("mock_email_"))
        } else {
            XCTFail("Expected .authenticated, got \(vm.authState)")
        }
    }

    func test_signOut_resetsState() async {
        let vm = OAuthViewModel()
        vm.emailInput = "runner@lapsapp.test"
        vm.passwordInput = "TestPass123"
        await vm.signInWithEmail()
        vm.signOut()
        XCTAssertEqual(vm.authState, .idle)
        XCTAssertTrue(vm.emailInput.isEmpty)
        XCTAssertTrue(vm.passwordInput.isEmpty)
    }

    func test_mockTokensNeverContainRealCredentialPatterns() async {
        let vm = OAuthViewModel()
        await vm.signInWithApple()
        if case .authenticated(_, let token) = vm.authState {
            // Must not look like a real OAuth token (JWT dot-delimited structure)
            XCTAssertFalse(token.contains("."), "Mock token must not resemble a real JWT: \(token)")
            XCTAssertTrue(token.hasPrefix("mock_"), "Token must start with mock_ sentinel: \(token)")
        }
    }
}
