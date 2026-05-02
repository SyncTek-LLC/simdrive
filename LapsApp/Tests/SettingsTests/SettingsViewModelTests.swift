import XCTest
@testable import LapsApp

// Tests for SettingsViewModel — written before the implementation was finalised (TDD).
// Uses an isolated UserDefaults suite so we never pollute the standard suite.
final class SettingsViewModelTests: XCTestCase {

    private var suiteName: String!
    private var defaults: UserDefaults!
    private var sut: SettingsViewModel!

    override func setUp() {
        super.setUp()
        suiteName = "io.synctek.lapsapp.tests.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)!
        sut = SettingsViewModel(defaults: defaults)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        super.tearDown()
    }

    // MARK: - Initial state

    func test_initialState_notificationsDisabled() {
        XCTAssertFalse(sut.notificationsEnabled)
    }

    func test_initialState_locationDisabled() {
        XCTAssertFalse(sut.locationEnabled)
    }

    func test_initialState_textSizeIsMedium() {
        // Default text size index is 1 (Medium) when no persisted value exists
        XCTAssertEqual(sut.textSizeIndex, 1)
        XCTAssertEqual(sut.textSizeLabel, "Medium")
    }

    // MARK: - Persistence

    func test_toggleNotifications_persistsTrueToDefaults() {
        sut.notificationsEnabled = true
        XCTAssertTrue(defaults.bool(forKey: SettingsViewModel.Keys.notificationsEnabled))
    }

    func test_toggleNotifications_persistsFalseToDefaults() {
        sut.notificationsEnabled = true
        sut.notificationsEnabled = false
        XCTAssertFalse(defaults.bool(forKey: SettingsViewModel.Keys.notificationsEnabled))
    }

    func test_persistedValueLoaded_onInit() {
        defaults.set(true, forKey: SettingsViewModel.Keys.notificationsEnabled)
        defaults.set(true, forKey: SettingsViewModel.Keys.locationEnabled)
        defaults.set(3, forKey: SettingsViewModel.Keys.textSizeIndex)
        let reloaded = SettingsViewModel(defaults: defaults)
        XCTAssertTrue(reloaded.notificationsEnabled)
        XCTAssertTrue(reloaded.locationEnabled)
        XCTAssertEqual(reloaded.textSizeIndex, 3)
    }

    // MARK: - Text size stepper

    func test_incrementTextSize_fromMediumToLarge() {
        sut.textSizeIndex = 1
        sut.incrementTextSize()
        XCTAssertEqual(sut.textSizeIndex, 2)
        XCTAssertEqual(sut.textSizeLabel, "Large")
    }

    func test_incrementTextSize_clampAtMax() {
        sut.textSizeIndex = 3
        sut.incrementTextSize()
        XCTAssertEqual(sut.textSizeIndex, 3, "Should not exceed index 3 (Extra-Large)")
    }

    func test_decrementTextSize_fromMediumToSmall() {
        sut.textSizeIndex = 1
        sut.decrementTextSize()
        XCTAssertEqual(sut.textSizeIndex, 0)
        XCTAssertEqual(sut.textSizeLabel, "Small")
    }

    func test_decrementTextSize_clampAtMin() {
        sut.textSizeIndex = 0
        sut.decrementTextSize()
        XCTAssertEqual(sut.textSizeIndex, 0, "Should not go below index 0 (Small)")
    }

    // MARK: - Text size label

    func test_textSizeLabel_allValues() {
        let expected: [Int: String] = [0: "Small", 1: "Medium", 2: "Large", 3: "Extra-Large"]
        for (index, label) in expected {
            sut.textSizeIndex = index
            XCTAssertEqual(sut.textSizeLabel, label, "Wrong label for index \(index)")
        }
    }

    // MARK: - Reset

    func test_resetToDefaults_clearsAllToggles() {
        sut.notificationsEnabled = true
        sut.locationEnabled = true
        sut.analyticsEnabled = true
        sut.textSizeIndex = 3
        sut.resetToDefaults()
        XCTAssertFalse(sut.notificationsEnabled)
        XCTAssertFalse(sut.locationEnabled)
        XCTAssertFalse(sut.analyticsEnabled)
        XCTAssertEqual(sut.textSizeIndex, 1)
    }
}
