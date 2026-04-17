//
//  HealthRoute.swift
//  SpecterQA Runner
//
//  GET /health — liveness probe.
//

import Foundation

struct HealthRoute: Route {
    let path = "/health"
    let methods = ["GET"]

    func handle(request: ParsedRequest, deps: RouteDependencies) -> HTTPResponse {
        return HTTPResponse.ok([
            "success": true,
            "status": "ok",
            "port": deps.server.port,
            "pid": ProcessInfo.processInfo.processIdentifier
        ])
    }
}
