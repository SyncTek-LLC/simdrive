import XCTest
import Foundation

/// TouchInjector wraps `XCUIApplication` and `XCUICoordinate` to provide
/// coordinate-based touch synthesis inside the iOS Simulator.
///
/// All coordinates are in device logical points — the same space as UIKit.
/// For example, an iPhone 16 Pro (393 × 852 pt) accepts x in 0…393.
///
/// Public API surface is intentionally narrow and matches the HTTP endpoint
/// set defined in HTTPServer.swift.
///
final class TouchInjector {

    // MARK: - Properties

    /// The XCUIApplication instance for the app-under-test.
    /// Exposed internally so `HTTPServer` can pass it to `AccessibilityTree.capture(app:)`.
    let app: XCUIApplication

    // MARK: - Init

    init(bundleId: String) {
        app = XCUIApplication(bundleIdentifier: bundleId)
    }

    // MARK: - Touch actions

    /// Tap at absolute logical-point coordinates.
    /// `duration` is currently unused by the public API but reserved for
    /// long-press support (pass to `press(forDuration:)`).
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
        // press(forDuration:thenDragTo:withVelocity:thenHoldForDuration:) is the
        // public API equivalent of a slow drag — works for scroll and swipe.
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
    /// Recognised key names (case-insensitive):
    ///   return, enter, delete, backspace, escape, tab, space,
    ///   up, down, left, right, home, end, pageup, pagedown
    func pressKey(_ name: String) throws {
        guard let key = keyboardKey(for: name) else {
            throw TouchInjectorError.unknownKey(name)
        }
        app.keys[key.rawValue].tap()
    }

    /// Press a hardware button via XCUIDevice.
    ///
    /// Recognised buttons (case-insensitive): home, volumeup, volumedown
    func pressButton(_ name: String) throws {
        switch name.lowercased() {
        case "home":
            XCUIDevice.shared.press(.home)
        case "volumeup":
            XCUIDevice.shared.press(.volumeUp)
        case "volumedown":
            XCUIDevice.shared.press(.volumeDown)
        default:
            throw TouchInjectorError.unknownButton(name)
        }
    }

    /// Capture a screenshot of the app and return raw PNG bytes plus logical size.
    func screenshot() -> (Data, CGSize) {
        let shot = app.screenshot()
        return (shot.pngRepresentation, shot.image.size)
    }

    // MARK: - Private helpers

    /// Build an `XCUICoordinate` at absolute logical-point (x, y).
    ///
    /// Strategy: start from the normalizedOffset (0,0) anchor — which maps to
    /// the top-left corner of the app frame — then shift by (x, y) points using
    /// `withOffset(CGVector(dx:dy:))`.  This gives us absolute point addressing
    /// without needing to know the screen dimensions in advance.
    private func absoluteCoordinate(x: Double, y: Double) -> XCUICoordinate {
        let origin = app.coordinate(withNormalizedOffset: .zero)
        return origin.withOffset(CGVector(dx: x, dy: y))
    }

    // MARK: - Keyboard key mapping

    private func keyboardKey(for name: String) -> XCUIKeyboardKey? {
        switch name.lowercased() {
        case "return", "enter":  return .return
        case "delete", "backspace": return .delete
        case "escape":           return .escape
        case "tab":              return .tab
        case "space":            return .space
        case "up":               return .upArrow
        case "down":             return .downArrow
        case "left":             return .leftArrow
        case "right":            return .rightArrow
        case "home":             return .home
        case "end":              return .end
        case "pageup":           return .pageUp
        case "pagedown":         return .pageDown
        default:                 return nil
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
