//
//  Route.swift
//  SpecterQA Runner
//
//  Defines the Route protocol and RouteDependencies injection struct.
//  Each HTTP endpoint is a separate type conforming to Route.
//  HTTPServer dispatches via routes.first(where: path+method match)?.handle(...).
//

import Foundation
import XCTest

// MARK: - ParsedRequest (shared across route files)

struct ParsedRequest {
    let method: String
    let path: String
    let query: [String: String]
    let body: [String: Any]
    let rawBody: Data
}

// MARK: - RouteDependencies

/// Injected services available to every Route handler.
struct RouteDependencies {
    let injector: TouchInjector
    let screenshotCapture: SpecterQAScreenshot?
    let elementQuery: SpecterQAElementQuery?
    let server: HTTPServer          // back-reference for log ring buffer, stopSemaphore
}

// MARK: - Route protocol

protocol Route {
    /// URL path this route handles, e.g. "/tap".
    var path: String { get }
    /// HTTP methods this route accepts, e.g. ["POST"].
    var methods: [String] { get }
    /// Handle an incoming request and return a response.
    func handle(request: ParsedRequest, deps: RouteDependencies) -> HTTPResponse
}
