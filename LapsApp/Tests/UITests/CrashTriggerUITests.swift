import XCTest

// CrashTriggerUITests — smoke test verifying the dev menu's accessibility identifiers
// are present before the crash button is tapped.
// We deliberately DO NOT tap "dev_menu_crash" in automated CI — that kills the test runner.
// The `crash-and-recover` SimDrive journey (journey #17) exercises the actual crash path.
//
// Navigation path (5-tab structure): Settings tab → Developer Menu nav link → CrashTriggerView
final class CrashTriggerUITests: XCTestCase {

    var app: XCUIApplication!

    override func setUpWithError() throws {
        continueAfterFailure = false
        app = XCUIApplication()
        app.launch()
    }

    override func tearDownWithError() throws {
        app.terminate()
    }

    // Navigate to CrashTriggerView via Settings tab → Developer Menu link
    private func navigateToDevMenu() {
        // Tab identifier from RootTabView
        let settingsTab = app.tabBars.buttons.matching(identifier: "tab_settings").firstMatch
        if !settingsTab.waitForExistence(timeout: 3) {
            // Fallback: find by label if identifier lookup fails on this sim
            let byLabel = app.tabBars.buttons["Settings"]
            XCTAssertTrue(byLabel.waitForExistence(timeout: 5), "Settings tab must be accessible")
            byLabel.tap()
        } else {
            settingsTab.tap()
        }

        // Tap the Developer Menu nav link (identifier from SettingsRootView)
        let devLink = app.buttons.matching(identifier: "settings_navlink_dev").firstMatch
        XCTAssertTrue(devLink.waitForExistence(timeout: 3), "Developer Menu nav link must be present")
        devLink.tap()
    }

    // Navigate to the Dev menu and verify crash trigger button is present with correct identifier
    func test_devMenuCrashButton_hasAccessibilityIdentifier() {
        navigateToDevMenu()

        // Verify the crash-trigger button is present
        let crashButton = app.buttons.matching(identifier: "dev_menu_open").firstMatch
        XCTAssertTrue(crashButton.waitForExistence(timeout: 3),
                      "dev_menu_open must be present for SimDrive crash-and-recover journey")
    }

    // Verify the crash confirmation alert appears and can be dismissed with Cancel.
    func test_crashAlert_appearAndCancel() {
        navigateToDevMenu()

        let crashButton = app.buttons.matching(identifier: "dev_menu_open").firstMatch
        XCTAssertTrue(crashButton.waitForExistence(timeout: 3))
        crashButton.tap()

        // Alert must appear
        let alert = app.alerts.firstMatch
        XCTAssertTrue(alert.waitForExistence(timeout: 3),
                      "crash confirmation alert must appear after tapping dev_menu_open")

        let cancelButton = alert.buttons.matching(identifier: "dev_menu_cancel").firstMatch
        XCTAssertTrue(cancelButton.exists, "Cancel button must be in the alert")
        cancelButton.tap()

        // After dismissal, the crash button should still be visible
        XCTAssertTrue(crashButton.waitForExistence(timeout: 3),
                      "crash button should be visible after Cancel dismisses the alert")
    }

    // Verify dev menu title text is rendered
    func test_devMenuTitle_isVisible() {
        navigateToDevMenu()

        let title = app.staticTexts.matching(identifier: "dev_menu_title").firstMatch
        XCTAssertTrue(title.waitForExistence(timeout: 3),
                      "dev_menu_title must be visible in CrashTriggerView")
    }
}
