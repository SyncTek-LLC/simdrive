//
//  KeyRoute.swift
//  SpecterQA Runner
//
//  POST /key          — press named key (return/tab crash-safe).
//  POST /press_button — hardware button (home/volumeup/volumedown).
//

import Foundation

struct KeyRoute: Route {
    let path = "/key"
    let methods = ["POST"]

    func handle(request: ParsedRequest, deps: RouteDependencies) -> HTTPResponse {
        guard let key = request.body["key"] as? String else {
            return HTTPResponse.error("key requires key (string)", code: 422)
        }
        var keyError: String? = nil
        deps.server.runOnMain {
            do { try deps.injector.pressKey(key) }
            catch { keyError = error.localizedDescription }
        }
        if let err = keyError {
            return HTTPResponse.error(err, code: 422)
        }
        return HTTPResponse.success(["key": key])
    }
}

struct PressButtonRoute: Route {
    let path = "/press_button"
    let methods = ["POST"]

    func handle(request: ParsedRequest, deps: RouteDependencies) -> HTTPResponse {
        guard let button = request.body["button"] as? String else {
            return HTTPResponse.error("press_button requires button (string)", code: 422)
        }
        var buttonError: String? = nil
        deps.server.runOnMain {
            do { try deps.injector.pressButton(button) }
            catch { buttonError = error.localizedDescription }
        }
        if let err = buttonError {
            return HTTPResponse.error(err, code: 422)
        }
        return HTTPResponse.success(["button": button])
    }
}
