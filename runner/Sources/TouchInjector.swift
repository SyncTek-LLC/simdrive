import XCTest
import Foundation

/// TouchInjector wraps `XCUIApplication` and `XCUICoordinate` to provide
/// coordinate-based touch synthesis inside the iOS Simulator.
///
/// All coordinates are in device logical points — the same space as UIKit.
/// For example, an iPhone 16 Pro (393 × 852 pt) accepts x in 0…393.
///
/// v2/v3 additions (from SpecterQAInteraction.swift / PoolIQ):
///   - pressReturnKey() with two-strategy crash mitigation for iOS 26
///     (strategy 3 / typeText("\n") removed — causes unconditional SIGABRT)
///   - scrollIntoView extension on XCUIElement
///
/// v1 retained:
///   - pressButton (home/volumeup/volumedown)
///   - absoluteCoordinate helper
///
final class TouchInjector {

    // MARK: - Properties

    /// The XCUIApplication instance for the app-under-test.
    let app: XCUIApplication

    // MARK: - Init

    init(bundleId: String) {
        app = XCUIApplication(bundleIdentifier: bundleId)
    }

    // MARK: - Touch actions

    /// Tap at absolute logical-point coordinates.
    func tap(x: Double, y: Double, duration: Double = 0.0) {
        let coord = absoluteCoordinate(x: x, y: y)
        if duration > 0 {
            coord.press(forDuration: duration)
        } else {
            coord.tap()
        }
    }

    /// Drag from one absolute logical-point coordinate to another.
    func swipe(fromX: Double, fromY: Double, toX: Double, toY: Double, duration: Double = 0.3) {
        let start = absoluteCoordinate(x: fromX, y: fromY)
        let end   = absoluteCoordinate(x: toX,   y: toY)
        start.press(
            forDuration: 0.05,
            thenDragTo: end,
            withVelocity: XCUIGestureVelocity(CGFloat(1.0 / max(duration, 0.05) * 100)),
            thenHoldForDuration: 0.0
        )
    }

    /// Type arbitrary text into the currently focused element.
    ///
    /// v5 error-reporting + focus-detection (fixes silent success bug):
    /// Prior to v5 this function returned `Void` and silently returned
    /// when no text field was found, while the HTTP handler always responded
    /// 200 OK.  Now the function throws `TouchInjectorError.noTextFieldFound`
    /// so the HTTP handler can return a 500 with a meaningful error message.
    ///
    /// Focus detection strategy (in priority order):
    ///   1. Scan textFields, secureTextFields, searchFields for `hasFocus == true`
    ///   2. Fall back to the first visible text field (any type)
    ///   3. Throw if no field is found or visible
    ///
    /// v4 crash mitigation for iOS 26 (retained):
    /// ``XCUIApplication.typeText()`` can corrupt the XCTest accessibility
    /// tree state on iOS 26 — the HTTP response returns OK but the next
    /// interaction crashes the runner with a delayed SIGABRT.
    ///
    /// Fix: ensure the element is focused before typing, wrap the call in
    /// an autoreleasepool to bound any internal allocations, then wait 0.8 s
    /// for the keyboard animation and accessibility tree to fully stabilize
    /// before the runner accepts the next HTTP request.
    func typeText(_ text: String) throws {
        // Strategy 1: find whichever text-input element currently has keyboard focus.
        var target: XCUIElement? = nil
        var alreadyFocused = false
        let inputTypes: [XCUIElementQuery] = [
            app.textFields, app.secureTextFields, app.searchFields
        ]
        outerLoop: for query in inputTypes {
            for element in query.allElementsBoundByIndex {
                if element.exists && element.hasFocus {
                    target = element
                    alreadyFocused = true
                    break outerLoop
                }
            }
        }

        // Strategy 2: fall back to the first visible field of any type.
        if target == nil {
            target = focusedTextElement()
        }

        guard let focused = target, focused.exists else {
            NSLog("[SpecterQA] typeText: no text field found or focused")
            throw TouchInjectorError.noTextFieldFound(
                "No visible text field found. Tap a text field first.")
        }

        // Only tap to focus if the field wasn't already focused (strategy 2).
        // When strategy 1 found a hasFocus field, tapping it would be redundant
        // at best and could steal focus from the intended field at worst.
        if !alreadyFocused {
            focused.tap()
            Thread.sleep(forTimeInterval: 0.3)
        }

        // Wrap in autoreleasepool to bound any internal XCTest allocations.
        autoreleasepool {
            focused.typeText(text)
        }

        // CRITICAL: settle delay before returning.
        // iOS 26 needs time to stabilize the accessibility tree after text
        // input; without this the next HTTP request hits a corrupted a11y
        // tree and the runner crashes with SIGABRT.
        Thread.sleep(forTimeInterval: 0.8)
    }

    /// Press a named keyboard key (maps to XCUIKeyboardKey).
    ///
    /// For "return"/"enter", uses the two-strategy crash mitigation (v3) to
    /// handle iOS 26 SIGABRT.  The former typeText("\n") fallback (strategy 3)
    /// has been removed — SIGABRT kills the process unconditionally and cannot
    /// be caught by XCTExpectFailure.  A 0.5 s stabilization delay is inserted
    /// after each successful strategy so the accessibility tree is consistent
    /// before the next HTTP request is processed.
    ///
    /// For "tab", uses the same two-strategy crash mitigation to avoid SIGABRT
    /// on iOS 26 when calling app.keys["tab"].tap() directly.
    ///
    /// Recognised key names (case-insensitive):
    ///   return, enter, tab, delete, backspace, escape, space,
    ///   up, down, left, right, home, end, pageup, pagedown
    func pressKey(_ name: String) throws {
        let normalized = name.lowercased().trimmingCharacters(in: .whitespaces)
        switch normalized {
        case "return", "enter":
            try pressReturnKey()
        case "tab":
            pressTabKey()
        default:
            guard let key = keyboardKey(for: normalized) else {
                throw TouchInjectorError.unknownKey(name)
            }
            // Insert a short stabilization delay to prevent cascading crashes
            // for other keys on iOS 26 (mirrors the return key mitigation).
            app.keys[key.rawValue].tap()
            Thread.sleep(forTimeInterval: 0.5)
        }
    }

    /// Press a hardware button via XCUIDevice.
    ///
    /// Recognised buttons (case-insensitive): home, volumeup, volumedown
    func pressButton(_ name: String) throws {
        switch name.lowercased() {
        case "home":
            XCUIDevice.shared.press(.home)
        case "volumeup":
            #if targetEnvironment(simulator)
            throw TouchInjectorError.unknownButton("volumeUp (unavailable in simulator)")
            #else
            XCUIDevice.shared.press(.volumeUp)
            #endif
        case "volumedown":
            #if targetEnvironment(simulator)
            throw TouchInjectorError.unknownButton("volumeDown (unavailable in simulator)")
            #else
            XCUIDevice.shared.press(.volumeDown)
            #endif
        default:
            throw TouchInjectorError.unknownButton(name)
        }
    }

    /// Capture a screenshot of the app and return raw PNG bytes plus logical size.
    func screenshot() -> (Data, CGSize) {
        let shot = app.screenshot()
        return (shot.pngRepresentation, shot.image.size)
    }

    // MARK: - Tab Key (Two-Strategy Crash Mitigation — mirrors Return Key v3)
    //
    //  iOS 26: app.keys["tab"].tap() can SIGABRT identically to the return key.
    //  Strategy order mirrors pressReturnKey():
    //    1. Find and tap the Tab/Next keyboard button by label (safest)
    //    2. Coordinate tap at the known Tab key position (top-left of keyboard)
    //       Tab is typically ~8% across and ~15% down from the keyboard's origin.

    private let tabKeyLabels = ["Tab", "Next"]

    private func pressTabKey() {
        let keyboard = app.keyboards.firstMatch
        if keyboard.waitForExistence(timeout: 2) {
            // Strategy 1: find and tap by label
            for label in tabKeyLabels {
                let btn = keyboard.buttons[label]
                if btn.exists && btn.isHittable {
                    btn.tap()
                    NSLog("[SpecterQA] pressKey(tab): tapped keyboard button '\(label)'")
                    Thread.sleep(forTimeInterval: 0.5)
                    return
                }
            }

            // Strategy 2: coordinate tap at Tab key position (top-left area)
            let kbFrame = keyboard.frame
            let tabX = kbFrame.origin.x + kbFrame.width  * 0.08
            let tabY = kbFrame.origin.y + kbFrame.height * 0.15
            let coord = app.coordinate(withNormalizedOffset: .zero)
                .withOffset(CGVector(dx: tabX, dy: tabY))
            coord.tap()
            NSLog("[SpecterQA] pressKey(tab): coordinate fallback (\(tabX), \(tabY))")
            Thread.sleep(forTimeInterval: 0.5)
            return
        }

        // No keyboard visible — nothing to do.
        NSLog("[SpecterQA] pressKey(tab): no keyboard visible — assuming already dismissed")
    }

    // MARK: - Return Key (Two-Strategy Crash Mitigation — v3)
    //
    //  iOS 26 beta: typeText("\n") on some keyboards causes SIGABRT.
    //  SIGABRT is a process-level signal — XCTExpectFailure cannot guard against
    //  it (it only guards XCTest assertion failures), so strategy 3 is removed.
    //
    //  After each successful strategy, Thread.sleep(0.5) lets the keyboard
    //  dismiss animation complete and the accessibility tree stabilize before
    //  the runner accepts the next HTTP request. Without this delay the next
    //  action (tap, screenshot, elements) hits a corrupted a11y tree and crashes.
    //
    //  Strategy order:
    //    1. Find and tap the Return/Done/Go/… keyboard button by label (safest)
    //    2. Coordinate tap at the known Return key position (bottom-right corner)
    //       If no keyboard button was found, this almost always hits the key.
    //       Return success — do not attempt typeText("\n").

    private let returnKeyLabels = ["Return", "Done", "Go", "Send", "Search", "Join", "Next"]

    private func pressReturnKey() throws {
        let keyboard = app.keyboards.firstMatch
        if keyboard.waitForExistence(timeout: 2) {
            // Strategy 1: find and tap by label
            for label in returnKeyLabels {
                let btn = keyboard.buttons[label]
                if btn.exists && btn.isHittable {
                    btn.tap()
                    NSLog("[SpecterQA] pressKey(return): tapped keyboard button '\(label)'")
                    // Wait for keyboard dismiss animation and a11y tree stabilization.
                    Thread.sleep(forTimeInterval: 0.5)
                    return
                }
            }

            // Strategy 2: coordinate tap at Return key position (bottom-right)
            let kbFrame = keyboard.frame
            let returnX = kbFrame.origin.x + kbFrame.width * 0.87
            let returnY = kbFrame.origin.y + kbFrame.height * 0.88
            let coord = app.coordinate(withNormalizedOffset: .zero)
                .withOffset(CGVector(dx: returnX, dy: returnY))
            coord.tap()
            NSLog("[SpecterQA] pressKey(return): coordinate fallback (\(returnX), \(returnY))")
            // Wait for keyboard dismiss animation and a11y tree stabilization.
            Thread.sleep(forTimeInterval: 0.5)
            return
        }

        // No keyboard visible — coordinate tap was not attempted.
        // Return success: the field may have already dismissed the keyboard,
        // or the return action already completed. typeText("\n") is intentionally
        // omitted — it causes SIGABRT on iOS 26 and cannot be guarded by
        // XCTExpectFailure (which only catches XCTest assertion failures,
        // not process-level signals).
        NSLog("[SpecterQA] pressKey(return): no keyboard visible — assuming already dismissed")
    }

    // MARK: - Private helpers

    /// Build an `XCUICoordinate` at absolute logical-point (x, y).
    private func absoluteCoordinate(x: Double, y: Double) -> XCUICoordinate {
        let origin = app.coordinate(withNormalizedOffset: .zero)
        return origin.withOffset(CGVector(dx: x, dy: y))
    }

    /// Return the first visible focused text input element.
    private func focusedTextElement() -> XCUIElement? {
        if app.textFields.firstMatch.exists        { return app.textFields.firstMatch }
        if app.secureTextFields.firstMatch.exists  { return app.secureTextFields.firstMatch }
        if app.searchFields.firstMatch.exists      { return app.searchFields.firstMatch }
        return nil
    }

    // MARK: - Keyboard key mapping

    private func keyboardKey(for name: String) -> XCUIKeyboardKey? {
        switch name {
        case "delete", "backspace": return .delete
        case "escape":              return .escape
        // "tab" is intentionally absent — handled by pressTabKey() above to
        // avoid SIGABRT on iOS 26 when calling app.keys["tab"].tap() directly.
        case "space":               return .space
        case "up":                  return .upArrow
        case "down":                return .downArrow
        case "left":                return .leftArrow
        case "right":               return .rightArrow
        case "home":                return .home
        case "end":                 return .end
        case "pageup":              return .pageUp
        case "pagedown":            return .pageDown
        default:                    return nil
        }
    }
}

// MARK: - XCUIElement Scroll-Into-View (v2 addition from SpecterQAInteraction)

extension XCUIElement {
    /// Scroll the nearest ancestor scroll view until this element is hittable.
    func scrollIntoView(in app: XCUIApplication) {
        let scrollViews = app.scrollViews.allElementsBoundByIndex
        for scrollView in scrollViews where scrollView.exists {
            if scrollView.frame.contains(self.frame) {
                scrollView.scrollToElement(self)
                return
            }
        }
    }

    fileprivate func scrollToElement(_ element: XCUIElement) {
        while !element.isHittable {
            self.swipeUp()
        }
    }
}

// MARK: - Errors

enum TouchInjectorError: Error, CustomStringConvertible {
    case unknownKey(String)
    case unknownButton(String)
    case noTextFieldFound(String)

    var description: String {
        switch self {
        case .unknownKey(let k):       return "Unknown keyboard key: '\(k)'"
        case .unknownButton(let b):    return "Unknown hardware button: '\(b)'"
        case .noTextFieldFound(let m): return m
        }
    }
}
