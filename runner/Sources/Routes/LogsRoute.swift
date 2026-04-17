//
//  LogsRoute.swift
//  SpecterQA Runner
//
//  GET /logs — in-process log ring buffer.
//
//  OSLogStore requires com.apple.logging.local-store which XCTest runners
//  cannot obtain.  Instead we serve entries from our in-process ring
//  buffer, populated via addLog() calls sprinkled throughout the handler
//  and by UIApplication lifecycle notification observers wired up in start().
//
//  Query params:
//    limit  — max entries to return (default 100, capped at 500)
//    level  — optional filter: "info" | "warning" | "error"
//    since  — optional ISO-8601 timestamp; only return entries after it
//

import Foundation

struct LogsRoute: Route {
    let path = "/logs"
    let methods = ["GET"]

    func handle(request: ParsedRequest, deps: RouteDependencies) -> HTTPResponse {
        let rawLimit = Int(request.query["limit"] ?? "100") ?? 100
        let limit = min(max(rawLimit, 1), deps.server.maxLogEntries)
        let levelFilter = request.query["level"]

        let sinceDate: Date?
        if let sinceStr = request.query["since"] {
            sinceDate = ISO8601DateFormatter().date(from: sinceStr)
        } else {
            sinceDate = nil
        }

        let snapshot = deps.server.snapshotLogs()

        let fmt = ISO8601DateFormatter()
        var filtered = snapshot
        if let since = sinceDate {
            filtered = filtered.filter { $0.timestamp > since }
        }
        if let lf = levelFilter {
            filtered = filtered.filter { $0.level == lf }
        }
        let entries = Array(filtered.suffix(limit))
        let logDicts: [[String: Any]] = entries.map { entry in
            [
                "timestamp": fmt.string(from: entry.timestamp),
                "level": entry.level,
                "message": entry.message,
            ]
        }
        return HTTPResponse.ok(["count": logDicts.count, "logs": logDicts])
    }
}
