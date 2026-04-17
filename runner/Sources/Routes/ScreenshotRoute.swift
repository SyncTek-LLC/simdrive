//
//  ScreenshotRoute.swift
//  SpecterQA Runner
//
//  GET /screenshot — base64 JPEG/PNG with scale/quality/format params.
//

import Foundation

struct ScreenshotRoute: Route {
    let path = "/screenshot"
    let methods = ["GET"]

    func handle(request: ParsedRequest, deps: RouteDependencies) -> HTTPResponse {
        var result: HTTPResponse = HTTPResponse.error("screenshot failed", code: 500)
        deps.server.runOnMain {
            guard deps.server.waitForAppReady(injector: deps.injector) else {
                result = HTTPResponse.error("app not running — timed out waiting for foreground", code: 503)
                return
            }
            // v2: use SpecterQAScreenshot with scale/quality/format params
            if let screenshotCapture = deps.screenshotCapture {
                let opts = ScreenshotOptions.from(query: request.query)
                let dict = screenshotCapture.captureToDict(options: opts)
                let code = (dict["success"] as? Bool == true) ? 200 : 500
                result = HTTPResponse(statusCode: code, body: dict)
            } else {
                // v1 fallback: raw PNG
                let (png, size) = deps.injector.screenshot()
                let b64 = png.base64EncodedString()
                result = HTTPResponse.ok([
                    "base64": b64,
                    "width":  Int(size.width),
                    "height": Int(size.height)
                ])
            }
        }
        return result
    }
}
