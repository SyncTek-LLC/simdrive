//
//  WebviewElementsRoute.swift
//  SpecterQA Runner
//
//  GET /webview — WKWebView descendant elements only.
//

import Foundation

struct WebviewElementsRoute: Route {
    let path = "/webview"
    let methods = ["GET"]

    func handle(request: ParsedRequest, deps: RouteDependencies) -> HTTPResponse {
        guard let eq = deps.elementQuery else {
            return HTTPResponse.error("element query not available", code: 503)
        }
        let elements = eq.queryWebViewElements(limit: 100)
        let json = elements.map { $0.dictionary }
        return HTTPResponse.ok(["success": true, "elements": json, "count": json.count])
    }
}
