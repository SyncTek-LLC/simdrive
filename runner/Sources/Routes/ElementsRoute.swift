//
//  ElementsRoute.swift
//  SpecterQA Runner
//
//  GET /elements — element query with ?limit=N&types=... (includes isHittable).
//
//  Brief settle wait lets in-progress SwiftUI animations complete before we
//  request a snapshot, preventing stale tree reads during view transitions
//  (e.g. NavigationLink push, tab switch).
//

import Foundation

struct ElementsRoute: Route {
    let path = "/elements"
    let methods = ["GET"]

    func handle(request: ParsedRequest, deps: RouteDependencies) -> HTTPResponse {
        guard let eq = deps.elementQuery else {
            return HTTPResponse.error("element query not available", code: 503)
        }
        Thread.sleep(forTimeInterval: 0.2)
        let limit = Int(request.query["limit"] ?? "200") ?? 200
        let types = request.query["types"]
        let elements = eq.queryAll(limit: limit, types: types)
        let dicts = elements.map { $0.dictionary }
        return HTTPResponse.ok(["success": true, "result": dicts, "count": dicts.count])
    }
}
