//
//  ShutdownRoute.swift
//  SpecterQA Runner
//
//  POST /shutdown — graceful shutdown (v1 compat)
//  POST /stop     — graceful shutdown (v2 compat alias)
//

import Foundation

struct ShutdownRoute: Route {
    let path = "/shutdown"
    let methods = ["POST"]

    func handle(request: ParsedRequest, deps: RouteDependencies) -> HTTPResponse {
        DispatchQueue.global().asyncAfter(deadline: .now() + 0.05) {
            deps.server.stopSemaphore.signal()
        }
        return HTTPResponse.success(["message": "Shutting down"])
    }
}

struct StopRoute: Route {
    let path = "/stop"
    let methods = ["POST"]

    func handle(request: ParsedRequest, deps: RouteDependencies) -> HTTPResponse {
        DispatchQueue.global().asyncAfter(deadline: .now() + 0.05) {
            deps.server.stopSemaphore.signal()
        }
        return HTTPResponse.success(["message": "Shutting down"])
    }
}
