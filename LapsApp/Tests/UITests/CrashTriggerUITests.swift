import XCTest

// CrashTriggerUITests — smoke test verifying the dev menu's accessibility identifiers
// are present before the crash button is tapped.
// We deliberately DO NOT tap "dev_menu_crash" in automated CI — that kills the test runner.
// The `crash-and-recover` SimDrive journey (journey #17) exercises the actual crash path.
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

    // Navigate to the Dev tab and verify crash trigger button is present with correct identifier
    func test_devMenuCrashButton_hasAccessibilityIdentifier() {
        // Tap the Dev tab — identifier set in RootTabView
        let devTab = app.tabBars.buttons["Dev"]
        XCTAssertTrue(devTab.waitForExistence(timeout: 5), "Dev tab must exist in tab bar")
        devTab.tap()

        // Verify the crash-trigger button is present
        let crashButton = app.buttons["dev_menu_open"]
        XCTAssertTrue(crashButton.waitForExistence(timeout: 3),
                      "dev_menu_open must be present for SimDrive crash-and-recover journey")
    }

    // Verify the crash confirmation alert appears and can be dismissed with Cancel.
    // Using .alert (not confirmationDialog) ensures Cancel is reachable via XCUITest's
    // accessibility tree without relying on UIKit overlay windows.
    func test_crashAlert_appearAndCancel() {
        let devTab = app.tabBars.buttons["Dev"]
        XCTAssertTrue(devTab.waitForExistence(timeout: 5))
        devTab.tap()

        let crashButton = app.buttons["dev_menu_open"]
        XCTAssertTrue(crashButton.waitForExistence(timeout: 3))
        crashButton.tap()

        // Alert Cancel button should be in app.alerts
        let alert = app.alerts.firstMatch
        XCTAssertTrue(alert.waitForExistence(timeout: 3),
                      "crash confirmation alert must appear after tapping dev_menu_open")

        // Use firstMatch to avoid ambiguity when other "Cancel"-labelled elements exist
        let cancelButton = alert.buttons.matching(identifier: "Cancel").firstMatch
        XCTAssertTrue(cancelButton.exists, "Cancel button must be in the alert")
        cancelButton.tap()

        // After dismissal, the crash button should still be visible
        XCTAssertTrue(crashButton.waitForExistence(timeout: 3),
                      "crash button should be visible after Cancel dismisses the alert")
    }

    // Verify dev menu title and icon are rendered
    func test_devMenuTitle_isVisible() {
        let devTab = app.tabBars.buttons["Dev"]
        XCTAssertTrue(devTab.waitForExistence(timeout: 5))
        devTab.tap()

        let title = app.staticTexts["Developer Menu"]
        XCTAssertTrue(title.waitForExistence(timeout: 3),
                      "Developer Menu title must be visible")
    }
}
