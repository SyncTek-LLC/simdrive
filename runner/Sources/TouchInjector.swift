import XCTest
import Foundation

/// TouchInjector wraps `XCUIApplication` and `XCUICoordinate` to provide
/// coordinate-based touch synthesis inside the iOS Simulator.
///
/// All coordinates are in device logical points — the same space as UIKit.
/// For example, an iPhone 16 Pro (393 × 852 pt) accepts x in 0…393.
///
/// v2 additions (from SpecterQAInteraction.swift / PoolIQ):
///   - pressReturnKey() with three-strategy crash mitigation for iOS 26
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
    func typeText(_ text: String) {
        app.typeText(text)
    }

    /// Press a named keyboard key (maps to XCUIKeyboardKey).
    ///
    /// For "return"/"enter", uses the three-strategy crash mitigation from v2
    /// (PoolIQ SpecterQAInteraction.swift) to handle iOS 26 SIGABRT.
    ///
    /// Recognised key names (case-insensitive):
    ///   return, enter, delete, backspace, escape, tab, space,
    ///   up, down, left, right, home, end, pageup, pagedown
    func pressKey(_ name: String) throws {
        let normalized = name.lowercased().trimmingCharacters(in: .whitespaces)
        switch normalized {
        case "return", "enter":
            try pressReturnKey()
        default:
            guard let key = keyboardKey(for: normalized) else {
                throw TouchInjectorError.unknownKey(name)
            }
            app.keys[key.rawValue].tap()
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

    // MARK: - Return Key (Three-Strategy Crash Mitigation — v2)
    //
    //  iOS 26 beta: typeText("\n") on some keyboards causes SIGABRT.
    //  Strategy order:
    //    1. Find and tap the Return/Done/Go/… keyboard button by label (safest)
    //    2. Coordinate tap at the known Return key position
    //    3. typeText("\n") wrapped in XCTExpectFailure (last resort)

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
            return
        }

        // Strategy 3: typeText("\n") guarded against iOS 26 crash
        guard let focused = focusedTextElement() else {
            throw TouchInjectorError.unknownKey("No keyboard visible and no text element has focus — cannot press return")
        }
        XCTExpectFailure("iOS 26 return key synthesis may fail — runner continues", options: .nonStrict()) {
            focused.typeText("\n")
        }
        NSLog("[SpecterQA] pressKey(return): typeText fallback attempted (crash-guarded)")
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
        case "tab":                 return .tab
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

    var description: String {
        switch self {
        case .unknownKey(let k):    return "Unknown keyboard key: '\(k)'"
        case .unknownButton(let b): return "Unknown hardware button: '\(b)'"
        }
    }
}
