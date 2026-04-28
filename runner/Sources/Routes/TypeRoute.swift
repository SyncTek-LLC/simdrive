//
//  TypeRoute.swift
//  SpecterQA Runner
//
//  POST /type — type text into focused field, or at given coordinates.
//
//  v16.0.0: AX-tree selector paths (label/identifier) removed.
//  Either pass (x, y) to tap-then-type, or pass no target to type into
//  whatever currently has focus. Higher-level label/identifier resolution
//  now lives in the Python ios_act tool.
//

import Foundation
import XCTest

struct TypeRoute: Route {
    let path = "/type"
    let methods = ["POST"]

    func handle(request: ParsedRequest, deps: RouteDependencies) -> HTTPResponse {
        let body = request.body
        let injector = deps.injector

        guard let text = body["text"] as? String else {
            return HTTPResponse.error("type requires text (string)", code: 422)
        }

        // Reject removed selector fields with a v16.0.0 migration message.
        if body["label"] != nil || body["identifier"] != nil || body["type"] != nil {
            return HTTPResponse.error(
                "ios_type requires (x, y) or no target in v16.0.0; use the ios_act tool",
                code: 422
            )
        }

        let targetX = body["x"] as? Double
        let targetY = body["y"] as? Double

        // Coordinate target: dismiss keyboard, tap coords, app.typeText
        if let x = targetX, let y = targetY {
            deps.server.runOnMain {
                // Dismiss keyboard
                let keyboard = injector.app.keyboards.firstMatch
                if keyboard.exists {
                    let kbFrame = keyboard.frame
                    injector.tap(x: Double(kbFrame.width / 2), y: Double(kbFrame.origin.y - 20))
                    Thread.sleep(forTimeInterval: 0.5)
                }
                // Tap target coordinates
                injector.tap(x: x, y: y)
                Thread.sleep(forTimeInterval: 0.5)
                // Type via app
                injector.app.typeText(text)
                Thread.sleep(forTimeInterval: 0.5)
            }
            deps.server.addLog("typed \(text.count) chars at (\(x),\(y))")
            return HTTPResponse.success(["characters": text.count, "focused": "coordinates:(\(x),\(y))"])
        }

        // No target specified — type into whatever has focus
        var typeError: String? = nil
        deps.server.runOnMain {
            do { try injector.typeText(text) }
            catch { typeError = error.localizedDescription }
        }
        if let err = typeError {
            deps.server.addLog("typeText FAILED: \(err)", level: "error")
            return HTTPResponse.error("typeText failed: \(err)", code: 500)
        }
        deps.server.addLog("typed \(text.count) chars into current focus")
        return HTTPResponse.success(["characters": text.count])
    }
}
