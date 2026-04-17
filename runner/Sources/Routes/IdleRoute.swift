//
//  IdleRoute.swift
//  SpecterQA Runner
//
//  POST /idle — wait until element tree is stable (Fix 6).
//
//  Polls until the element tree is stable (two snapshots 300 ms apart have
//  the same element count) or a timeout is reached.
//  Body: {"timeout": <seconds, default 10, max 30>}
//

import Foundation
import XCTest

struct IdleRoute: Route {
    let path = "/idle"
    let methods = ["POST"]

    func handle(request: ParsedRequest, deps: RouteDependencies) -> HTTPResponse {
        let rawTimeout = (request.body["timeout"] as? Double) ?? 10.0
        let idleTimeout = min(max(rawTimeout, 0), 30.0)
        var idleResult: HTTPResponse = HTTPResponse.error("idle check failed", code: 500)
        deps.server.runOnMain {
            let deadline = Date().addingTimeInterval(idleTimeout)
            var waited: Double = 0.0
            let pollInterval: TimeInterval = 0.3

            // First ensure app is in foreground
            if deps.injector.app.state != .runningForeground {
                NSLog("[SpecterQA] idle: app not in foreground — aborting")
                idleResult = HTTPResponse.error("app not in runningForeground state", code: 503)
                return
            }

            while Date() < deadline {
                let countBefore: Int
                let countAfter: Int
                do {
                    let snap1 = try deps.injector.app.snapshot()
                    let c1 = IdleRoute.countDescendants(snap1)
                    Thread.sleep(forTimeInterval: pollInterval)
                    waited += pollInterval
                    let snap2 = try deps.injector.app.snapshot()
                    countBefore = c1
                    countAfter = IdleRoute.countDescendants(snap2)
                } catch {
                    // Snapshot failed — wait and retry
                    Thread.sleep(forTimeInterval: pollInterval)
                    waited += pollInterval
                    continue
                }

                if countBefore == countAfter {
                    NSLog("[SpecterQA] idle: stable after \(waited)s (count=\(countAfter))")
                    idleResult = HTTPResponse.ok(["status": "idle", "waited": waited])
                    return
                }
                // Tree is still changing — keep polling (no extra sleep, 300ms already spent)
            }

            NSLog("[SpecterQA] idle: timed out after \(idleTimeout)s")
            idleResult = HTTPResponse.ok(["status": "timeout", "waited": idleTimeout])
        }
        return idleResult
    }

    private static func countDescendants(_ snapshot: any XCUIElementSnapshot) -> Int {
        var count = 1
        for child in snapshot.children {
            count += countDescendants(child)
        }
        return count
    }
}
