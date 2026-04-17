//
//  TapRoute.swift
//  SpecterQA Runner
//
//  POST /tap — coordinate or element tap (auto-recovers from backgrounding).
//

import Foundation
import XCTest

struct TapRoute: Route {
    let path = "/tap"
    let methods = ["POST"]

    func handle(request: ParsedRequest, deps: RouteDependencies) -> HTTPResponse {
        let body = request.body
        let injector = deps.injector

        // Element-based tap: find by label/identifier and use XCTest's
        // element.tap() which reliably transfers first-responder focus
        // even on SwiftUI SecureField inside List/Form cells.
        if let label = body["label"] as? String {
            let type = body["type"] as? String
            var found = false
            var autoRecovered = false
            deps.server.runOnMain {
                guard let el = deps.elementQuery?.findByLabel(label, type: type),
                      el.exists else { return }
                // Use element-relative coordinate tap — safer than el.tap()
                // which can crash with ObjC NSExceptions on iOS 26.
                // element.coordinate(withNormalizedOffset:).tap() goes through
                // XCTest's coordinate system but is element-aware, properly
                // transferring first-responder focus on SwiftUI SecureField.
                let coord = el.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5))
                coord.tap()
                found = true
                Thread.sleep(forTimeInterval: 0.3)
                if injector.app.state != .runningForeground {
                    injector.app.activate()
                    Thread.sleep(forTimeInterval: 1.0)
                    autoRecovered = true
                }
            }
            if found {
                var result: [String: Any] = ["mode": "element", "label": label]
                if autoRecovered { result["warning"] = "App was backgrounded and auto-recovered" }
                deps.server.addLog("tap element: '\(label)'")
                return HTTPResponse.success(result)
            }
            // Element not found by runner — fall through to coordinate tap
        }

        if let identifier = body["identifier"] as? String {
            var found = false
            var autoRecovered = false
            deps.server.runOnMain {
                guard let el = deps.elementQuery?.findByIdentifier(identifier),
                      el.exists else { return }
                let coord = el.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5))
                coord.tap()
                found = true
                Thread.sleep(forTimeInterval: 0.3)
                if injector.app.state != .runningForeground {
                    injector.app.activate()
                    Thread.sleep(forTimeInterval: 1.0)
                    autoRecovered = true
                }
            }
            if found {
                var result: [String: Any] = ["mode": "element", "identifier": identifier]
                if autoRecovered { result["warning"] = "App was backgrounded and auto-recovered" }
                deps.server.addLog("tap element id: '\(identifier)'")
                return HTTPResponse.success(result)
            }
            // Element not found by runner — fall through to coordinate tap
        }

        // Coordinate-based tap (fallback)
        if let x = body["x"] as? Double, let y = body["y"] as? Double {
            let duration = body["duration"] as? Double ?? 0.0
            var autoRecovered = false
            deps.server.runOnMain {
                injector.tap(x: x, y: y, duration: duration)
                // Fix 5: auto-recover if tap sent the app to background
                if injector.app.state != .runningForeground {
                    NSLog("[SpecterQA] tap: app backgrounded after tap — activating")
                    injector.app.activate()
                    Thread.sleep(forTimeInterval: 1.0)
                    autoRecovered = true
                }
            }
            var result: [String: Any] = ["mode": "coordinate", "x": x, "y": y]
            if autoRecovered {
                result["warning"] = "App was backgrounded and auto-recovered"
            }
            return HTTPResponse.success(result)
        }
        return HTTPResponse.error("tap requires x+y, label, or identifier", code: 422)
    }
}
