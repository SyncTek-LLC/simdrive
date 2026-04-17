//
//  ScrollRoute.swift
//  SpecterQA Runner
//
//  POST /scroll — scroll gesture by direction (up/down/left/right).
//

import Foundation

struct ScrollRoute: Route {
    let path = "/scroll"
    let methods = ["POST"]

    func handle(request: ParsedRequest, deps: RouteDependencies) -> HTTPResponse {
        guard let direction = request.body["direction"] as? String else {
            return HTTPResponse.error("scroll requires {direction}", code: 422)
        }
        var scrollError: String? = nil
        deps.server.runOnMain {
            let window = deps.injector.app.windows.firstMatch
            guard window.exists else {
                scrollError = "app window not found"
                return
            }
            switch direction.lowercased() {
            case "up":    window.swipeUp()
            case "down":  window.swipeDown()
            case "left":  window.swipeLeft()
            case "right": window.swipeRight()
            default: scrollError = "Unknown scroll direction: \(direction)"
            }
        }
        if let err = scrollError {
            return HTTPResponse.error(err, code: 422)
        }
        return HTTPResponse.success(["direction": direction])
    }
}
