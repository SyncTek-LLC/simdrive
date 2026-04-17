//
//  WaitRoute.swift
//  SpecterQA Runner
//
//  POST /wait — wait for element by label.
//

import Foundation

struct WaitRoute: Route {
    let path = "/wait"
    let methods = ["POST"]

    func handle(request: ParsedRequest, deps: RouteDependencies) -> HTTPResponse {
        guard let label = request.body["label"] as? String else {
            return HTTPResponse.error("wait requires {label}", code: 422)
        }
        guard let eq = deps.elementQuery else {
            return HTTPResponse.error("element query not available", code: 503)
        }
        let type = request.body["type"] as? String
        let timeout = (request.body["timeout"] as? Double) ?? 10.0
        if let el = eq.waitForElement(label, type: type, timeout: timeout) {
            return HTTPResponse.success([
                "found": true,
                "label": el.label,
                "frame": [
                    "x": el.frame.origin.x, "y": el.frame.origin.y,
                    "width": el.frame.width, "height": el.frame.height
                ]
            ])
        }
        return HTTPResponse.error("Timeout after \(timeout)s waiting for '\(label)'", code: 408)
    }
}
