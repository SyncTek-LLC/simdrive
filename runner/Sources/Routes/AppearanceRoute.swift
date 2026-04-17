//
//  AppearanceRoute.swift
//  SpecterQA Runner
//
//  POST /appearance — set dark/light mode via XCUIDevice (Fix 2).
//
//  Uses XCUIDevice.shared.appearance to avoid simctl conflict with active
//  XCTest session. Accepts {"mode": "dark"} or {"mode": "light"}.
//

import Foundation
import XCTest

struct AppearanceRoute: Route {
    let path = "/appearance"
    let methods = ["POST"]

    func handle(request: ParsedRequest, deps: RouteDependencies) -> HTTPResponse {
        guard let mode = request.body["mode"] as? String,
              mode == "dark" || mode == "light" else {
            return HTTPResponse.error("appearance requires {mode: 'dark' | 'light'}", code: 422)
        }
        deps.server.runOnMain {
            XCUIDevice.shared.appearance = (mode == "dark") ? .dark : .light
            NSLog("[SpecterQA] appearance set to \(mode)")
        }
        return HTTPResponse.success(["mode": mode])
    }
}
