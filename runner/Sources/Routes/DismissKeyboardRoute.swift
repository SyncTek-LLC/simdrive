//
//  DismissKeyboardRoute.swift
//  SpecterQA Runner
//
//  POST /dismiss_keyboard — dismiss the software keyboard.
//

import Foundation

struct DismissKeyboardRoute: Route {
    let path = "/dismiss_keyboard"
    let methods = ["POST"]

    func handle(request: ParsedRequest, deps: RouteDependencies) -> HTTPResponse {
        let injector = deps.injector
        var dismissed = false
        deps.server.runOnMain {
            let keyboard = injector.app.keyboards.firstMatch
            if keyboard.exists {
                // Strategy: tap above the keyboard to dismiss it.
                // Swipe down from the top of the keyboard area.
                let kbFrame = keyboard.frame
                let tapY = kbFrame.origin.y - 20  // just above keyboard
                let tapX = kbFrame.width / 2
                injector.tap(x: Double(tapX), y: Double(tapY))
                Thread.sleep(forTimeInterval: 0.5)
                // Check if it worked
                dismissed = !injector.app.keyboards.firstMatch.exists
                if !dismissed {
                    // Fallback: swipe down on the keyboard
                    let startY = kbFrame.origin.y + 10
                    let endY = kbFrame.origin.y + kbFrame.height + 50
                    injector.swipe(
                        fromX: Double(tapX), fromY: Double(startY),
                        toX: Double(tapX), toY: Double(endY),
                        duration: 0.3
                    )
                    Thread.sleep(forTimeInterval: 0.5)
                    dismissed = !injector.app.keyboards.firstMatch.exists
                }
            } else {
                dismissed = true // already dismissed
            }
        }
        deps.server.addLog("dismiss_keyboard: \(dismissed ? "ok" : "failed")")
        return HTTPResponse.success(["dismissed": dismissed])
    }
}
