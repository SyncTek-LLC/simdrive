import XCTest
@testable import LapsApp

// Tests for AppearanceViewModel — TDD: tests written before implementation was locked.
final class AppearanceViewModelTests: XCTestCase {

    private var suiteName: String!
    private var defaults: UserDefaults!
    private var sut: AppearanceViewModel!

    override func setUp() {
        super.setUp()
        suiteName = "io.synctek.lapsapp.appearance.tests.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)!
        sut = AppearanceViewModel(defaults: defaults)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        super.tearDown()
    }

    // MARK: - Initial state

    func test_initialMode_isSystem_whenNoPersistedValue() {
        XCTAssertEqual(sut.selectedMode, .system)
    }

    // MARK: - Mode selection

    func test_selectDark_updateSelectedMode() {
        sut.select(.dark)
        XCTAssertEqual(sut.selectedMode, .dark)
    }

    func test_selectLight_updateSelectedMode() {
        sut.select(.light)
        XCTAssertEqual(sut.selectedMode, .light)
    }

    func test_selectSystem_fromDark_restoresSystem() {
        sut.select(.dark)
        sut.select(.system)
        XCTAssertEqual(sut.selectedMode, .system)
    }

    // MARK: - Persistence

    func test_selectDark_persistsToDefaults() {
        sut.select(.dark)
        let reloaded = AppearanceViewModel(defaults: defaults)
        XCTAssertEqual(reloaded.selectedMode, .dark)
    }

    func test_selectLight_persistsToDefaults() {
        sut.select(.light)
        let reloaded = AppearanceViewModel(defaults: defaults)
        XCTAssertEqual(reloaded.selectedMode, .light)
    }

    // MARK: - Mode identifiers (SimDrive accessibility ID anchors depend on rawValue)

    func test_modeRawValues_matchSpec() {
        XCTAssertEqual(AppearanceViewModel.Mode.system.rawValue, "System")
        XCTAssertEqual(AppearanceViewModel.Mode.light.rawValue, "Light")
        XCTAssertEqual(AppearanceViewModel.Mode.dark.rawValue, "Dark")
    }

    func test_allCases_count_isThree() {
        XCTAssertEqual(AppearanceViewModel.Mode.allCases.count, 3)
    }

    // MARK: - Display name

    func test_displayName_matchesRawValue() {
        for mode in AppearanceViewModel.Mode.allCases {
            XCTAssertEqual(mode.displayName, mode.rawValue)
        }
    }
}
