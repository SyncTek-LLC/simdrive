import XCTest
import Foundation

/// AccessibilityTree — Handler for GET /source.
///
/// Returns the full XCUIElementSnapshot tree for the app-under-test as a JSON
/// array. Uses Option B from the HANDOFF spec: `XCUIApplication.snapshot()`
/// for structured, version-stable output.
///
/// JSON shape per element:
/// ```json
/// {
///   "type":     42,
///   "typeLabel": "button",
///   "label":    "General",
///   "value":    null,
///   "identifier": "general-settings",
///   "frame":    {"x": 0, "y": 236, "width": 390, "height": 44},
///   "enabled":  true,
///   "children": [...]
/// }
/// ```
///
/// The `type` is the raw `XCUIElement.ElementType` integer value. `typeLabel`
/// is a human-readable name useful for SoM filtering.
///
enum AccessibilityTree {

    // MARK: - Public API

    /// Capture and serialize the accessibility tree for the given app.
    ///
    /// Returns a tuple of (jsonData, httpStatusCode). On success the JSON data
    /// is an array of top-level elements (normally one root application element).
    /// On failure returns an error JSON object with status 503.
    static func capture(app: XCUIApplication) -> (Data, Int) {
        // Guard: app must be running — check via XCUIApplication.state.
        // XCUIApplicationState.runningForeground == 4
        guard app.state == .runningForeground || app.state == .runningBackground else {
            let err: [String: Any] = [
                "error": "app not running",
                "state": app.state.rawValue
            ]
            return (encodeJSON(err), 503)
        }

        do {
            let snapshot = try app.snapshot()
            let serialized = serialize(snapshot)
            // Wrap in an array for a uniform response shape — callers iterate elements.
            let payload: [[String: Any]] = [serialized]
            return (encodeJSONArray(payload), 200)
        } catch {
            let err: [String: Any] = [
                "error":  "snapshot failed",
                "detail": error.localizedDescription
            ]
            return (encodeJSON(err), 500)
        }
    }

    // MARK: - Serialization

    /// Recursively serialize an `XCUIElementSnapshot` into a plain dictionary.
    static func serialize(_ element: any XCUIElementSnapshot) -> [String: Any] {
        let frame = element.frame
        var dict: [String: Any] = [
            "type":       element.elementType.rawValue,
            "typeLabel":  typeName(element.elementType),
            "label":      element.label,
            "identifier": element.identifier,
            "frame": [
                "x":      frame.origin.x,
                "y":      frame.origin.y,
                "width":  frame.size.width,
                "height": frame.size.height
            ],
            "enabled": element.isEnabled,
        ]

        // value can be any Sendable — coerce to String where possible.
        if let v = element.value {
            dict["value"] = "\(v)"
        } else {
            dict["value"] = NSNull()
        }

        // Recurse into children. Always include the key so callers don't need nil checks.
        let kids = element.children
        dict["children"] = kids.map { serialize($0) }

        return dict
    }

    // MARK: - Element type name mapping

    // Maps XCUIElement.ElementType raw values to human-readable strings.
    // Coverage matches the types regularly seen in iOS UI trees.
    private static func typeName(_ type: XCUIElement.ElementType) -> String {
        switch type {
        case .any:                  return "any"   // XCUIElementTypeAny = 0
        case .other:                return "other"
        case .application:          return "application"
        case .group:                return "group"
        case .window:               return "window"
        case .sheet:                return "sheet"
        case .drawer:               return "drawer"
        case .alert:                return "alert"
        case .dialog:               return "dialog"
        case .button:               return "button"
        case .radioButton:          return "radioButton"
        case .radioGroup:           return "radioGroup"
        case .checkBox:             return "checkBox"
        case .disclosureTriangle:   return "disclosureTriangle"
        case .popUpButton:          return "popUpButton"
        case .comboBox:             return "comboBox"
        case .menuButton:           return "menuButton"
        case .toolbarButton:        return "toolbarButton"
        case .popover:              return "popover"
        case .keyboard:             return "keyboard"
        case .key:                  return "key"
        case .navigationBar:        return "navigationBar"
        case .tabBar:               return "tabBar"
        case .tabGroup:             return "tabGroup"
        case .toolbar:              return "toolbar"
        case .statusBar:            return "statusBar"
        case .table:                return "table"
        case .tableRow:             return "tableRow"
        case .tableColumn:          return "tableColumn"
        case .outline:              return "outline"
        case .outlineRow:           return "outlineRow"
        case .browser:              return "browser"
        case .collectionView:       return "collectionView"
        case .slider:               return "slider"
        case .pageIndicator:        return "pageIndicator"
        case .progressIndicator:    return "progressIndicator"
        case .activityIndicator:    return "activityIndicator"
        case .segmentedControl:     return "segmentedControl"
        case .picker:               return "picker"
        case .pickerWheel:          return "pickerWheel"
        case .switch:               return "switch"
        case .toggle:               return "toggle"
        case .link:                 return "link"
        case .image:                return "image"
        case .icon:                 return "icon"
        case .searchField:          return "searchField"
        case .scrollView:           return "scrollView"
        case .scrollBar:            return "scrollBar"
        case .staticText:           return "staticText"
        case .textField:            return "textField"
        case .secureTextField:      return "secureTextField"
        case .datePicker:           return "datePicker"
        case .textView:             return "textView"
        case .menu:                 return "menu"
        case .menuItem:             return "menuItem"
        case .menuBar:              return "menuBar"
        case .menuBarItem:          return "menuBarItem"
        case .map:                  return "map"
        case .webView:              return "webView"
        case .incrementArrow:       return "incrementArrow"
        case .decrementArrow:       return "decrementArrow"
        case .timeline:             return "timeline"
        case .ratingIndicator:      return "ratingIndicator"
        case .valueIndicator:       return "valueIndicator"
        case .splitGroup:           return "splitGroup"
        case .splitter:             return "splitter"
        case .relevanceIndicator:   return "relevanceIndicator"
        case .colorWell:            return "colorWell"
        case .helpTag:              return "helpTag"
        case .matte:                return "matte"
        case .dockItem:             return "dockItem"
        case .ruler:                return "ruler"
        case .rulerMarker:          return "rulerMarker"
        case .grid:                 return "grid"
        case .levelIndicator:       return "levelIndicator"
        case .cell:                 return "cell"
        case .layoutArea:           return "layoutArea"
        case .layoutItem:           return "layoutItem"
        case .handle:               return "handle"
        case .stepper:              return "stepper"
        case .tab:                  return "tab"
        case .touchBar:             return "touchBar"
        case .statusItem:           return "statusItem"
        @unknown default:           return "unknown(\(type.rawValue))"
        }
    }

    // MARK: - JSON encoding helpers

    private static func encodeJSON(_ dict: [String: Any]) -> Data {
        (try? JSONSerialization.data(withJSONObject: dict, options: .prettyPrinted))
            ?? Data("{\"error\":\"json encode failed\"}".utf8)
    }

    private static func encodeJSONArray(_ array: [[String: Any]]) -> Data {
        (try? JSONSerialization.data(withJSONObject: array, options: .prettyPrinted))
            ?? Data("[{\"error\":\"json encode failed\"}]".utf8)
    }
}
