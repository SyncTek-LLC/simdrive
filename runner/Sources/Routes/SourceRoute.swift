//
//  SourceRoute.swift
//  SpecterQA Runner
//
//  GET /source — JSON accessibility tree.
//

import Foundation

struct SourceRoute: Route {
    let path = "/source"
    let methods = ["GET"]

    func handle(request: ParsedRequest, deps: RouteDependencies) -> HTTPResponse {
        var result: HTTPResponse = HTTPResponse.error("source failed", code: 500)
        deps.server.runOnMain {
            guard deps.server.waitForAppReady(injector: deps.injector) else {
                result = HTTPResponse.error("app not running — timed out waiting for foreground", code: 503)
                return
            }
            let (treeData, statusCode) = AccessibilityTree.capture(app: deps.injector.app)
            // AccessibilityTree returns raw JSON data; wrap in a response manually
            result = HTTPResponse.rawData(treeData, statusCode: statusCode)
        }
        return result
    }
}
