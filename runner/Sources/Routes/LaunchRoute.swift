//
//  LaunchRoute.swift
//  SpecterQA Runner
//
//  POST /launch    — launch/activate app by bundle_id.
//  POST /terminate — terminate app by bundle_id.
//

import Foundation
import XCTest

struct LaunchRoute: Route {
    let path = "/launch"
    let methods = ["POST"]

    func handle(request: ParsedRequest, deps: RouteDependencies) -> HTTPResponse {
        guard let bundleID = request.body["bundle_id"] as? String else {
            return HTTPResponse.error("launch requires {bundle_id}", code: 422)
        }
        deps.server.runOnMain {
            let targetApp = XCUIApplication(bundleIdentifier: bundleID)
            targetApp.launch()
            NSLog("[SpecterQA] Launched app: \(bundleID)")
        }
        return HTTPResponse.success(["action": "launch", "bundle_id": bundleID])
    }
}

struct TerminateRoute: Route {
    let path = "/terminate"
    let methods = ["POST"]

    func handle(request: ParsedRequest, deps: RouteDependencies) -> HTTPResponse {
        guard let bundleID = request.body["bundle_id"] as? String else {
            return HTTPResponse.error("terminate requires {bundle_id}", code: 422)
        }
        deps.server.runOnMain {
            let targetApp = XCUIApplication(bundleIdentifier: bundleID)
            targetApp.terminate()
            NSLog("[SpecterQA] Terminated app: \(bundleID)")
        }
        return HTTPResponse.success(["bundle_id": bundleID])
    }
}
