//
//  TapRoute.swift
//  SpecterQA Runner
//
//  POST /tap — coordinate-only tap (auto-recovers from backgrounding).
//
//  v16.0.0: AX-tree selector paths (label/identifier) removed.
//  Coordinates are the only supported targeting mode. Higher-level
//  label/identifier resolution now lives in the Python ios_act tool.
//

import Foundation
import XCTest

struct TapRoute: Route {
    let path = "/tap"
    let methods = ["POST"]

    func handle(request: ParsedRequest, deps: RouteDependencies) -> HTTPResponse {
        let body = request.body
        let injector = deps.injector

        // Coordinate-based tap is the only supported path in v16.0.0.
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
        return HTTPResponse.error(
            "ios_tap requires (x, y) coordinates in v16.0.0; use the ios_act tool",
            code: 422
        )
    }
}
