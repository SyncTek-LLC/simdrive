//
//  TypeRoute.swift
//  SpecterQA Runner
//
//  POST /type — type text.
//
//  When a target field is specified, find the XCUIElement and call
//  element.typeText() DIRECTLY on it — bypassing TouchInjector's
//  focus detection which always types into the first field.
//  This is the ONLY way to type into a specific SwiftUI Form field.
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

        let targetLabel = body["label"] as? String
        let targetIdentifier = body["identifier"] as? String
        let targetX = body["x"] as? Double
        let targetY = body["y"] as? Double

        if targetLabel != nil || targetIdentifier != nil {
            var focusTarget: String? = nil
            var typeError: String? = nil
            deps.server.runOnMain {
                var el: XCUIElement? = nil
                if let label = targetLabel {
                    el = deps.elementQuery?.findByLabel(label, type: body["type"] as? String)
                    focusTarget = "label:\(label)"
                } else if let identifier = targetIdentifier {
                    el = deps.elementQuery?.findByIdentifier(identifier)
                    focusTarget = "identifier:\(identifier)"
                }
                guard let target = el, target.exists else {
                    typeError = "Element '\(focusTarget ?? "?")' not found"
                    return
                }
                // Step 1: Dismiss any active keyboard by tapping just above it.
                // This clears the first-responder so the next tap properly
                // focuses the TARGET field, not the previously focused one.
                let keyboard = injector.app.keyboards.firstMatch
                if keyboard.exists {
                    let kbFrame = keyboard.frame
                    let tapY = kbFrame.origin.y - 20
                    let tapX = kbFrame.width / 2
                    injector.tap(x: Double(tapX), y: Double(tapY))
                    Thread.sleep(forTimeInterval: 0.5)
                }

                // Step 2: Tap the target field to give it focus.
                target.tap()
                Thread.sleep(forTimeInterval: 0.5)

                // Step 3: Type via the app (sends to whatever now has focus).
                // Using app.typeText avoids the element-level typeText SIGABRT
                // that kills the runner on iOS 26.
                injector.app.typeText(text)
                Thread.sleep(forTimeInterval: 0.5)
            }
            if let err = typeError {
                deps.server.addLog("typeText FAILED: \(err)", level: "error")
                return HTTPResponse.error("typeText failed: \(err)", code: 500)
            }
            var result: [String: Any] = ["characters": text.count]
            if let ft = focusTarget { result["focused"] = ft }
            deps.server.addLog("typed \(text.count) chars into \(focusTarget ?? "?")")
            return HTTPResponse.success(result)
        }

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
