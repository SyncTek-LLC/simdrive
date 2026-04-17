//
//  SwipeRoute.swift
//  SpecterQA Runner
//
//  POST /swipe — swipe gesture (fromX/fromY/toX/toY/duration).
//

import Foundation

struct SwipeRoute: Route {
    let path = "/swipe"
    let methods = ["POST"]

    func handle(request: ParsedRequest, deps: RouteDependencies) -> HTTPResponse {
        let body = request.body
        // Coordinate swipe: {fromX, fromY, toX, toY, duration?}
        if let fromX = body["fromX"] as? Double,
           let fromY = body["fromY"] as? Double,
           let toX   = body["toX"]   as? Double,
           let toY   = body["toY"]   as? Double {
            let duration = body["duration"] as? Double ?? 0.3
            deps.server.runOnMain {
                deps.injector.swipe(fromX: fromX, fromY: fromY, toX: toX, toY: toY, duration: duration)
            }
            return HTTPResponse.success(["mode": "coordinate"])
        }
        return HTTPResponse.error("swipe requires fromX, fromY, toX, toY", code: 422)
    }
}
