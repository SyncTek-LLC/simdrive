//
//  DismissAlertRoute.swift
//  SpecterQA Runner
//
//  POST /dismiss-alert — dismiss visible system alert or sheet.
//

import Foundation
import XCTest

struct DismissAlertRoute: Route {
    let path = "/dismiss-alert"
    let methods = ["POST"]

    func handle(request: ParsedRequest, deps: RouteDependencies) -> HTTPResponse {
        var dismissResult: HTTPResponse = HTTPResponse.error("No alert or sheet visible", code: 404)
        deps.server.runOnMain {
            let alert = deps.injector.app.alerts.firstMatch
            let sheet = deps.injector.app.sheets.firstMatch
            let target: XCUIElement
            if alert.exists        { target = alert }
            else if sheet.exists   { target = sheet }
            else                   { return }

            let preferred = ["OK", "Allow", "Allow Full Access", "Allow While Using App",
                             "Allow Once", "Continue", "Done", "Close"]
            for label in preferred {
                let btn = target.buttons[label]
                if btn.exists {
                    btn.tap()
                    dismissResult = HTTPResponse.success(["dismissed_via": label])
                    return
                }
            }
            // Last resort: first non-denial button — use firstMatch subscript
            // rather than allElementsBoundByIndex + isHittable, which can crash
            // on iOS 26 when the alert tree contains SwiftUI-bridged elements.
            let denials: Set<String> = ["Don't Allow", "Deny", "Cancel", "Not Now", "Never"]
            let fallback = target.buttons.firstMatch
            if fallback.exists && !denials.contains(fallback.label) {
                fallback.tap()
                dismissResult = HTTPResponse.success(["dismissed_via": fallback.label, "fallback": true])
                return
            }
            dismissResult = HTTPResponse.error("Alert found but no tappable dismiss button")
        }
        return dismissResult
    }
}
