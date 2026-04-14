//
//  SpecterQAElementQuery.swift
//  SpecterQA Runner
//
//  Element query engine wrapping XCUIApplication.
//  All public methods are safe to call from any thread — they dispatch
//  interactions to the main thread internally via runOnMain().
//
//  Copied from PoolIQ v2 (SpecterQAElementQuery.swift). No changes.
//

import XCTest

// MARK: - Element Descriptor

struct ElementDescriptor {
    let label: String
    let type: String
    let identifier: String
    let frame: CGRect
    let isEnabled: Bool
    let isSelected: Bool
    let isHittable: Bool
    let value: String
    let index: Int

    /// Sanitize a CGFloat for JSON — replace inf/NaN with 0
    private static func sanitize(_ v: CGFloat) -> CGFloat {
        v.isFinite ? v : 0
    }

    var dictionary: [String: Any] {
        [
            "label": label,
            "type": type,
            "identifier": identifier,
            "frame": [
                "x": Self.sanitize(frame.origin.x),
                "y": Self.sanitize(frame.origin.y),
                "width": Self.sanitize(frame.width),
                "height": Self.sanitize(frame.height)
            ],
            "enabled": isEnabled,
            "selected": isSelected,
            "hittable": isHittable,
            "value": value,
            "index": index
        ]
    }
}

// MARK: - SpecterQAElementQuery

final class SpecterQAElementQuery {

    private let app: XCUIApplication

    init(app: XCUIApplication) {
        self.app = app
    }

    // MARK: - queryAll

    /// Returns all accessible elements in the current view hierarchy.
    ///
    /// Uses a single XCUIApplication.snapshot() IPC call for performance — one
    /// round-trip instead of N per element. Falls back to per-element query if
    /// the snapshot API fails.
    ///
    /// - Parameters:
    ///   - limit: Maximum number of elements to return (default 200).
    ///   - types: Optional comma-separated type names to filter by (e.g. "button,staticText").
    ///            When nil or empty, defaults to fast set: button + staticText + textField.
    ///            Pass "all" to query the full interactive type set (12 types).
    func queryAll(limit: Int = 200, types: String? = nil) -> [ElementDescriptor] {
        var descriptors: [ElementDescriptor] = []

        let queryTypes: [XCUIElement.ElementType]
        if let requestedTypes = types, !requestedTypes.isEmpty, requestedTypes.lowercased() != "all" {
            queryTypes = requestedTypes
                .split(separator: ",")
                .compactMap { xcuiElementType(from: String($0).trimmingCharacters(in: .whitespaces)) }
        } else if types?.lowercased() == "all" {
            queryTypes = [
                .button, .staticText, .textField, .secureTextField,
                .navigationBar, .tabBar, .searchField, .switch,
                .slider, .alert, .sheet, .link
            ]
        } else {
            // Default: all interactive types. Prior versions only queried
            // button + staticText + textField, missing secureTextField,
            // searchField, cell, tabBar — causing empty element lists on
            // SwiftUI Form screens.
            queryTypes = [
                .button, .staticText, .textField, .secureTextField,
                .searchField, .cell, .tabBar, .navigationBar,
                .switch, .slider, .link, .image
            ]
        }

        let typeSet = Set(queryTypes.map { $0.rawValue })

        // Primary: snapshot-based walk (fast, works on most views).
        // Falls back to per-element query on snapshot failure.
        do {
            let snapshot = try self.app.snapshot()
            self.walkSnapshot(snapshot, types: typeSet, descriptors: &descriptors, limit: limit)
        } catch {
            NSLog("[SpecterQA] Snapshot failed (\(error)), using per-element query")
            for elType in queryTypes {
                if descriptors.count >= limit { break }
                let elements = self.app.descendants(matching: elType).allElementsBoundByIndex
                for element in elements {
                    if descriptors.count >= limit { break }
                    guard element.exists else { continue }
                    let label = element.label
                    let ident = element.identifier
                    if label.isEmpty && ident.isEmpty { continue }
                    let fr = element.frame
                    descriptors.append(ElementDescriptor(
                        label: label,
                        type: self.elementTypeName(element.elementType),
                        identifier: ident,
                        frame: fr,
                        isEnabled: element.isEnabled,
                        isSelected: false,
                        isHittable: fr.width > 0 && fr.height > 0,
                        value: (element.value as? String) ?? "",
                        index: descriptors.count
                    ))
                }
            }
        }

        // Append web view elements (not in snapshot — XCTest accessibility tree
        // excludes WKWebView content from snapshot but provides separate query)
        if descriptors.count < limit {
            let webViewElements = queryWebViewElements(limit: limit - descriptors.count)
            for el in webViewElements {
                descriptors.append(el)
            }
        }

        return descriptors
    }

    private func walkSnapshot(_ snapshot: any XCUIElementSnapshot,
                              types: Set<UInt>,
                              descriptors: inout [ElementDescriptor],
                              limit: Int,
                              depth: Int = 0) {
        guard descriptors.count < limit else { return }
        guard depth < 50 else { return }

        let rawType = snapshot.elementType.rawValue
        if types.contains(rawType) {
            let label = snapshot.label
            let ident = snapshot.identifier
            if !label.isEmpty || !ident.isEmpty {
                // XCUIElementSnapshot does not expose isHittable — derive a
                // best-effort value: enabled + non-zero frame + not off-screen.
                let fr = snapshot.frame
                let snapshotHittable = snapshot.isEnabled
                    && fr.width > 0 && fr.height > 0
                    && fr.origin.x >= 0 && fr.origin.y >= 0
                descriptors.append(ElementDescriptor(
                    label: label,
                    type: elementTypeName(snapshot.elementType),
                    identifier: ident,
                    frame: fr,
                    isEnabled: snapshot.isEnabled,
                    isSelected: snapshot.isSelected,
                    isHittable: snapshotHittable,
                    value: (snapshot.value as? String) ?? "",
                    index: descriptors.count
                ))
            }
        }

        for child in snapshot.children {
            if descriptors.count >= limit { break }
            walkSnapshot(child, types: types, descriptors: &descriptors,
                        limit: limit, depth: depth + 1)
        }
    }

    // MARK: - queryWebViewElements

    /// Query elements inside WKWebView content. XCTest CAN see web view
    /// elements via the .webView descendants chain — this is the only way
    /// to interact with EPUB readers, PDF viewers, etc.
    func queryWebViewElements(limit: Int = 100) -> [ElementDescriptor] {
        var descriptors: [ElementDescriptor] = []
        let webViews = self.app.webViews.allElementsBoundByIndex
        for webView in webViews {
            if descriptors.count >= limit { break }
            // Walk all descendants of the web view
            let elements = webView.descendants(matching: .any).allElementsBoundByIndex
            for element in elements {
                if descriptors.count >= limit { break }
                let label = element.label
                let ident = element.identifier
                if label.isEmpty && ident.isEmpty { continue }
                descriptors.append(ElementDescriptor(
                    label: label,
                    type: self.elementTypeName(element.elementType),
                    identifier: ident,
                    frame: element.frame,
                    isEnabled: element.isEnabled,
                    isSelected: element.isSelected,
                    isHittable: element.isHittable,
                    value: (element.value as? String) ?? "",
                    index: descriptors.count
                ))
            }
        }
        return descriptors
    }

    // MARK: - findByLabel

    func findByLabel(_ label: String, type: String? = nil) -> XCUIElement? {
        // Use XCTest subscript only — do NOT iterate allElementsBoundByIndex
        // which crashes on iOS 26 with SwiftUI TextField in List cells.
        if let typeName = type, let elementType = self.xcuiElementType(from: typeName) {
            let match = self.app.descendants(matching: elementType)[label]
            if match.exists { return match }
        }
        let match = self.app.descendants(matching: .any)[label]
        if match.exists { return match }
        // WebView fallback (subscript-based, safe)
        for webView in self.app.webViews.allElementsBoundByIndex {
            let wMatch = webView.descendants(matching: .any)[label]
            if wMatch.exists { return wMatch }
        }
        return nil
    }

    func findByLabel(_ label: String, type: String? = nil, index: Int) -> XCUIElement? {
        return runOnMain {
            // For index-based lookup, use subscript for index=0 (common case)
            if index == 0 {
                if let typeName = type, let elementType = self.xcuiElementType(from: typeName) {
                    let match = self.app.descendants(matching: elementType)[label]
                    if match.exists { return match }
                } else {
                    let match = self.app.descendants(matching: .any)[label]
                    if match.exists { return match }
                }
                return nil
            }
            // Index > 0: need to iterate (rare, only for disambiguation)
            var matches: [XCUIElement] = []
            let elType: XCUIElement.ElementType = {
                if let typeName = type, let et = self.xcuiElementType(from: typeName) { return et }
                return .any
            }()
            for el in self.app.descendants(matching: elType).allElementsBoundByIndex {
                guard el.exists else { continue }
                if el.label == label { matches.append(el) }
                if matches.count > index { break }
            }
            guard index < matches.count else { return nil }
            return matches[index]
        }
    }

    // MARK: - findByIdentifier

    func findByIdentifier(_ identifier: String) -> XCUIElement? {
        return runOnMain {
            // Use XCTest's built-in subscript for identifier lookup.
            // Iterating allElementsBoundByIndex crashes on iOS 26 when
            // the tree contains SwiftUI TextField in List cells —
            // accessing .identifier on certain elements throws ObjC NSException.
            let match = self.app.descendants(matching: .any)[identifier]
            if match.exists { return match }
            for webView in self.app.webViews.allElementsBoundByIndex {
                for el in webView.descendants(matching: .any).allElementsBoundByIndex {
                    if el.identifier == identifier && el.exists { return el }
                }
            }
            return nil
        }
    }

    // MARK: - waitForElement

    func waitForElement(_ label: String, type: String? = nil, timeout: TimeInterval = 10.0) -> XCUIElement? {
        let deadline = Date().addingTimeInterval(timeout)
        let pollInterval: TimeInterval = 0.25
        while Date() < deadline {
            if let el = findByLabel(label, type: type), el.exists { return el }
            Thread.sleep(forTimeInterval: pollInterval)
        }
        return nil
    }

    // MARK: - Helpers

    func xcuiElementType(from name: String) -> XCUIElement.ElementType? {
        switch name.lowercased() {
        case "button":          return .button
        case "textfield", "text_field", "field": return .textField
        case "securetextfield", "secure": return .secureTextField
        case "statictext", "text", "label": return .staticText
        case "image":           return .image
        case "cell":            return .cell
        case "table":           return .table
        case "collectionview", "collection": return .collectionView
        case "scrollview", "scroll": return .scrollView
        case "switch":          return .switch
        case "slider":          return .slider
        case "link":            return .link
        case "webview", "web":  return .webView
        case "alert":           return .alert
        case "sheet":           return .sheet
        case "window":          return .window
        case "view":            return .other
        case "navigationbar", "navbar": return .navigationBar
        case "toolbar":         return .toolbar
        case "tabbar", "tab":   return .tabBar
        case "searchfield", "search": return .searchField
        case "any", "":         return .any
        default:                return .any
        }
    }

    func elementTypeName(_ type: XCUIElement.ElementType) -> String {
        switch type {
        case .button:           return "button"
        case .textField:        return "textField"
        case .secureTextField:  return "secureTextField"
        case .staticText:       return "staticText"
        case .image:            return "image"
        case .cell:             return "cell"
        case .table:            return "table"
        case .collectionView:   return "collectionView"
        case .scrollView:       return "scrollView"
        case .switch:           return "switch"
        case .slider:           return "slider"
        case .link:             return "link"
        case .webView:          return "webView"
        case .alert:            return "alert"
        case .sheet:            return "sheet"
        case .window:           return "window"
        case .navigationBar:    return "navigationBar"
        case .toolbar:          return "toolbar"
        case .tabBar:           return "tabBar"
        case .searchField:      return "searchField"
        case .other:            return "other"
        default:                return "unknown"
        }
    }
}

// MARK: - Main Thread Dispatch Helper

func runOnMain<T>(_ block: () -> T) -> T {
    if Thread.isMainThread {
        return block()
    } else {
        return DispatchQueue.main.sync { block() }
    }
}
