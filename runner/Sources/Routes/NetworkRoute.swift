//
//  NetworkRoute.swift
//  SpecterQA Runner
//
//  GET /network — network reachability check.
//
//  The XCTest runner cannot intercept the app's URLSession traffic (cross-
//  process limitation).  We report what we CAN observe: basic reachability
//  from the runner process, and a clear note about the cross-process gap.
//

import Foundation

struct NetworkRoute: Route {
    let path = "/network"
    let methods = ["GET"]

    func handle(request: ParsedRequest, deps: RouteDependencies) -> HTTPResponse {
        var reachable = false
        let semaphore = DispatchSemaphore(value: 0)
        let probeURL = URL(string: "https://www.apple.com")!
        URLSession.shared.dataTask(with: probeURL) { _, response, _ in
            if let http = response as? HTTPURLResponse {
                reachable = (200...299).contains(http.statusCode)
            }
            semaphore.signal()
        }.resume()
        _ = semaphore.wait(timeout: .now() + 3)

        return HTTPResponse.ok([
            "network_reachable": reachable,
            "note": "URL-level app traffic is not observable from the XCTest runner (cross-process limitation). Use ios_logs for CFNetwork entries if the app logs network activity.",
        ])
    }
}
