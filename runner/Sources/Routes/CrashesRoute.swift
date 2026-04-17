//
//  CrashesRoute.swift
//  SpecterQA Runner
//
//  GET /crashes — app state + error log entries from the in-process ring buffer.
//
//  Reports app state from the XCTest perspective + any error-level log
//  entries from our in-process ring buffer.
//

import Foundation
import XCTest

struct CrashesRoute: Route {
    let path = "/crashes"
    let methods = ["GET"]

    func handle(request: ParsedRequest, deps: RouteDependencies) -> HTTPResponse {
        var result: [String: Any] = [:]
        deps.server.runOnMain {
            let appState = deps.injector.app.state
            let isRunning = appState == .runningForeground
                || appState == .runningBackground
                || appState == .runningBackgroundSuspended

            result["app_running"] = isRunning
            result["app_state_raw"] = appState.rawValue

            let stateStr: String
            switch appState {
            case .notRunning:                 stateStr = "notRunning"
            case .runningBackgroundSuspended: stateStr = "runningBackgroundSuspended"
            case .runningBackground:          stateStr = "runningBackground"
            case .runningForeground:          stateStr = "runningForeground"
            default:                          stateStr = "unknown(\(appState.rawValue))"
            }
            result["app_state"] = stateStr

            if isRunning {
                let t0 = Date()
                let _ = deps.injector.app.exists
                let elapsed = Date().timeIntervalSince(t0)
                result["responsive"] = elapsed < 2.0
                result["response_time_sec"] = elapsed
            } else {
                result["responsive"] = false
            }
        }

        // Surface error-level log entries from the ring buffer
        let errorLogs = deps.server.snapshotLogs().filter { $0.level == "error" }
        let fmt = ISO8601DateFormatter()
        result["error_count"] = errorLogs.count
        result["recent_errors"] = errorLogs.suffix(10).map { entry -> [String: Any] in
            [
                "timestamp": fmt.string(from: entry.timestamp),
                "message": entry.message,
            ]
        }

        return HTTPResponse.ok(result)
    }
}
