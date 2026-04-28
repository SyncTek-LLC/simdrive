//
//  WaitRoute.swift
//  SpecterQA Runner
//
//  POST /wait — REMOVED in v16.0.0.
//
//  The ios_wait_for_element MCP tool was deleted on the Python side.
//  This stub route remains so any straggler caller gets a clear migration
//  message. Use ios_observe in a polling loop instead.
//

import Foundation

struct WaitRoute: Route {
    let path = "/wait"
    let methods = ["POST"]

    func handle(request: ParsedRequest, deps: RouteDependencies) -> HTTPResponse {
        return HTTPResponse.error(
            "ios_wait_for_element removed in v16.0.0; use ios_observe in a polling loop instead",
            code: 410
        )
    }
}
