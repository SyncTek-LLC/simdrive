//
//  AppStateRoute.swift
//  SpecterQA Runner
//
//  GET /app_state — current XCUIApplication state (Fix 4).
//
//  Returns the current XCUIApplication state as a string and raw Int.
//

import Foundation
import XCTest

struct AppStateRoute: Route {
    let path = "/app_state"
    let methods = ["GET"]

    func handle(request: ParsedRequest, deps: RouteDependencies) -> HTTPResponse {
        var stateResult: HTTPResponse = HTTPResponse.error("app_state failed", code: 500)
        deps.server.runOnMain {
            let state = deps.injector.app.state
            let stateStr: String
            switch state {
            case .notRunning:                 stateStr = "notRunning"
            case .runningBackgroundSuspended: stateStr = "runningBackgroundSuspended"
            case .runningBackground:          stateStr = "runningBackground"
            case .runningForeground:          stateStr = "runningForeground"
            default:                          stateStr = "unknown(\(state.rawValue))"
            }
            stateResult = HTTPResponse.ok([
                "state": stateStr,
                "state_raw": state.rawValue
            ])
        }
        return stateResult
    }
}
