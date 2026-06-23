//
//  HTTPServer.swift
//  SpecterQA Runner
//
//  Merged v1 (NWListener) + v2 (BSD sockets) implementation.
//  Uses Darwin BSD sockets directly — no Network.framework entitlement issues.
//
//  Threading model:
//    - accept loop on a dedicated background DispatchQueue
//    - each connection handled on a per-connection concurrent DispatchQueue
//    - XCUITest interactions dispatched to the main thread via runOnMain()
//      using CFRunLoopPerformBlock + CFRunLoopWakeUp (compatible with
//      CFRunLoopRunInMode loop in SpecterQARunner.swift)
//
//  Route dispatch:
//    Routes are registered in SpecterQARunner.swift via registerRoutes().
//    Each route is a value type conforming to the Route protocol (Routes/*.swift).
//    The 24-case switch has been replaced with a linear path+method lookup.
//

import Foundation
import Darwin
import UIKit
import XCTest

// MARK: - LogEntry (shared with Routes)

struct LogEntry {
    let timestamp: Date
    let level: String
    let message: String
}

// MARK: - HTTPServer

final class HTTPServer {

    // MARK: - Properties

    let port: UInt16
    private var serverFD: Int32 = -1
    private(set) var isRunning = false
    private let acceptQueue = DispatchQueue(label: "com.specterqa.runner.accept", qos: .userInitiated)
    private let connectionQueue = DispatchQueue(label: "com.specterqa.runner.connection",
                                                qos: .userInitiated,
                                                attributes: .concurrent)

    // Held strongly so routes can call back into the injector/tree
    private let injector: TouchInjector

    /// Semaphore signaled when /shutdown or /stop is received.
    let stopSemaphore = DispatchSemaphore(value: 0)

    // MARK: - In-process log ring buffer
    //
    // OSLogStore requires the com.apple.logging.local-store entitlement which
    // XCTest runners don't have.  Instead we maintain a thread-safe ring buffer
    // that the server and action handlers write to via addLog(_:level:).
    // The buffer is capped at maxLogEntries to bound memory growth.

    private var logBuffer: [LogEntry] = []
    private let logBufferLock = NSLock()
    let maxLogEntries = 500

    // MARK: - Route registry

    private var routes: [Route] = []

    // MARK: - Init

    init(port: UInt16 = 8222, injector: TouchInjector) {
        self.port = port
        self.injector = injector
    }

    // MARK: - Route registration

    func registerRoutes(_ newRoutes: [Route]) {
        routes.append(contentsOf: newRoutes)
    }

    // MARK: - Lifecycle

    func start() throws {
        serverFD = socket(AF_INET, SOCK_STREAM, 0)
        guard serverFD >= 0 else {
            throw HTTPServerError.socketFailed("socket() failed: \(String(cString: strerror(errno)))")
        }

        var reuse: Int32 = 1
        setsockopt(serverFD, SOL_SOCKET, SO_REUSEADDR, &reuse, socklen_t(MemoryLayout<Int32>.size))

        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = port.bigEndian
        addr.sin_addr = in_addr(s_addr: INADDR_ANY)
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)

        let bindResult = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                bind(serverFD, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard bindResult == 0 else {
            throw HTTPServerError.bindFailed("bind() on port \(port) failed: \(String(cString: strerror(errno)))")
        }

        guard listen(serverFD, 16) == 0 else {
            throw HTTPServerError.listenFailed("listen() failed: \(String(cString: strerror(errno)))")
        }

        isRunning = true
        NSLog("[SpecterQA] HTTP server listening on port \(port)")

        acceptQueue.async { [weak self] in
            self?.acceptLoop()
        }

        // Observe UIApplication lifecycle notifications and write them to the
        // in-process log buffer.  These fire on the main thread; addLog() is
        // thread-safe so this is safe to call from any queue.
        NotificationCenter.default.addObserver(
            forName: UIApplication.didReceiveMemoryWarningNotification,
            object: nil,
            queue: nil
        ) { [weak self] _ in
            self?.addLog("MEMORY WARNING received", level: "error")
        }
        NotificationCenter.default.addObserver(
            forName: UIApplication.didEnterBackgroundNotification,
            object: nil,
            queue: nil
        ) { [weak self] _ in
            self?.addLog("App entered background", level: "warning")
        }
        NotificationCenter.default.addObserver(
            forName: UIApplication.willEnterForegroundNotification,
            object: nil,
            queue: nil
        ) { [weak self] _ in
            self?.addLog("App entering foreground", level: "info")
        }
        NotificationCenter.default.addObserver(
            forName: UIApplication.didBecomeActiveNotification,
            object: nil,
            queue: nil
        ) { [weak self] _ in
            self?.addLog("App became active", level: "info")
        }
        NotificationCenter.default.addObserver(
            forName: UIApplication.willResignActiveNotification,
            object: nil,
            queue: nil
        ) { [weak self] _ in
            self?.addLog("App will resign active", level: "warning")
        }
    }

    func stop() {
        guard isRunning else { return }
        isRunning = false
        if serverFD >= 0 {
            Darwin.shutdown(serverFD, SHUT_RDWR)
            close(serverFD)
            serverFD = -1
        }
        NSLog("[SpecterQA] HTTP server stopped.")
    }

    // MARK: - Accept Loop

    private func acceptLoop() {
        while isRunning {
            var clientAddr = sockaddr_in()
            var addrLen = socklen_t(MemoryLayout<sockaddr_in>.size)
            let clientFD = withUnsafeMutablePointer(to: &clientAddr) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    accept(serverFD, $0, &addrLen)
                }
            }

            guard clientFD >= 0 else {
                if isRunning {
                    NSLog("[SpecterQA] accept() error: \(String(cString: strerror(errno)))")
                }
                continue
            }

            connectionQueue.async { [weak self] in
                self?.handleConnection(fd: clientFD)
            }
        }
    }

    // MARK: - Connection Handling

    private func handleConnection(fd: Int32) {
        defer { close(fd) }

        // 5s receive timeout
        var tv = timeval(tv_sec: 5, tv_usec: 0)
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))

        guard let rawRequest = readRequest(fd: fd),
              let request = parseRequest(rawRequest) else {
            sendResponse(fd: fd, response: HTTPResponse.error("Bad request", code: 400))
            return
        }

        NSLog("[SpecterQA] \(request.method) \(request.path)")

        let response = dispatch(request: request)
        sendResponse(fd: fd, response: response)
    }

    // MARK: - Raw I/O

    private func readRequest(fd: Int32) -> Data? {
        var buffer = Data(capacity: 65536)
        let chunk = UnsafeMutablePointer<UInt8>.allocate(capacity: 4096)
        defer { chunk.deallocate() }

        var headerEndOffset: Int? = nil
        var contentLength = 0

        while true {
            let bytesRead = recv(fd, chunk, 4096, 0)
            if bytesRead <= 0 { break }
            buffer.append(chunk, count: bytesRead)

            if headerEndOffset == nil {
                if let range = buffer.range(of: Data("\r\n\r\n".utf8)) {
                    headerEndOffset = range.upperBound
                    let headerData = buffer.prefix(range.lowerBound)
                    let headerStr = String(data: headerData, encoding: .utf8) ?? ""
                    for line in headerStr.components(separatedBy: "\r\n") {
                        let lower = line.lowercased()
                        if lower.hasPrefix("content-length:") {
                            let value = line.dropFirst("content-length:".count).trimmingCharacters(in: .whitespaces)
                            contentLength = Int(value) ?? 0
                        }
                    }
                }
            }

            if let offset = headerEndOffset {
                if buffer.count - offset >= contentLength { break }
            }
        }

        return buffer.isEmpty ? nil : buffer
    }

    private func parseRequest(_ data: Data) -> ParsedRequest? {
        guard let headerBodySep = data.range(of: Data("\r\n\r\n".utf8)) else { return nil }

        let headerData = data.prefix(headerBodySep.lowerBound)
        let bodyData = data.suffix(from: headerBodySep.upperBound)

        guard let headerStr = String(data: headerData, encoding: .utf8) else { return nil }
        let lines = headerStr.components(separatedBy: "\r\n")
        guard let requestLine = lines.first else { return nil }

        let parts = requestLine.components(separatedBy: " ")
        guard parts.count >= 2 else { return nil }

        let method = parts[0].uppercased()
        let fullPath = parts[1]

        // Split path and query string
        var path = fullPath
        var query: [String: String] = [:]
        if let qIdx = fullPath.firstIndex(of: "?") {
            path = String(fullPath[fullPath.startIndex..<qIdx])
            let queryStr = String(fullPath[fullPath.index(after: qIdx)...])
            for pair in queryStr.components(separatedBy: "&") {
                let kv = pair.components(separatedBy: "=")
                if kv.count == 2 {
                    let key = kv[0].removingPercentEncoding ?? kv[0]
                    let val = kv[1].removingPercentEncoding ?? kv[1]
                    query[key] = val
                }
            }
        }

        var body: [String: Any] = [:]
        if !bodyData.isEmpty {
            body = (try? JSONSerialization.jsonObject(with: bodyData) as? [String: Any]) ?? [:]
        }

        return ParsedRequest(method: method, path: path, query: query, body: body, rawBody: Data(bodyData))
    }

    private func sendResponse(fd: Int32, response: HTTPResponse) {
        let data = response.serialized()
        data.withUnsafeBytes { ptr in
            guard let base = ptr.baseAddress else { return }
            _ = send(fd, base, data.count, 0)
        }
    }

    // MARK: - Route dispatch

    private func dispatch(request: ParsedRequest) -> HTTPResponse {
        guard let route = routes.first(where: {
            $0.path == request.path && $0.methods.contains(request.method)
        }) else {
            return HTTPResponse.notFound(request.path)
        }
        let deps = RouteDependencies(
            injector: injector,
            screenshotCapture: screenshotCapture,
            elementQuery: elementQuery,
            server: self
        )
        return route.handle(request: request, deps: deps)
    }

    // MARK: - Main thread dispatch
    //
    // Uses CFRunLoopPerformBlock + CFRunLoopWakeUp so that dispatch blocks
    // are processed by the CFRunLoopRunInMode loop in SpecterQARunner.swift.
    // DispatchQueue.main.sync would also work but can deadlock if the main
    // thread is already inside a sync call; the RunLoop approach is safer.

    func runOnMain(_ block: @escaping () -> Void) {
        // v16.0.0 — defense-in-depth (internal dogfood).
        //
        // XCTest can throw an ObjC NSException out of XCUICoordinate /
        // snapshot APIs on unexpected simulator states. Swift cannot
        // @try / @catch ObjC exceptions natively, so without this bridge
        // a throw inside a route handler propagates through
        // CFRunLoopPerformBlock and kills the test method that testServe()
        // is parked on.
        //
        // v16.0.0 deletes the SpecterQAElementQuery selector layer (which
        // was the dominant throw site in v15.x). The bridge stays as a
        // safety net — XCUICoordinate.tap and screenshot APIs can still
        // throw on rare iOS bugs.
        let safeBlock: () -> Void = { [weak self] in
            if let exception = SpecterQAObjCBridge.tryBlock(block) {
                let name = exception.name.rawValue
                let reason = exception.reason ?? "<no reason>"
                NSLog("[SpecterQA] runOnMain caught NSException: %@ — %@", name, reason)
                self?.addLog(
                    "NSException in route: \(name) — \(reason)",
                    level: "error"
                )
            }
        }

        if Thread.isMainThread {
            safeBlock()
        } else {
            let sem = DispatchSemaphore(value: 0)
            CFRunLoopPerformBlock(CFRunLoopGetMain(), CFRunLoopMode.defaultMode.rawValue) {
                safeBlock()
                sem.signal()
            }
            CFRunLoopWakeUp(CFRunLoopGetMain())
            sem.wait()
        }
    }

    // MARK: - Log ring buffer helpers

    /// Append a log entry to the in-process ring buffer.
    ///
    /// Thread-safe — may be called from any queue.
    /// Entries beyond `maxLogEntries` are evicted from the front (FIFO).
    func addLog(_ message: String, level: String = "info") {
        logBufferLock.lock()
        logBuffer.append(LogEntry(timestamp: Date(), level: level, message: message))
        if logBuffer.count > maxLogEntries {
            logBuffer.removeFirst(logBuffer.count - maxLogEntries)
        }
        logBufferLock.unlock()
    }

    /// Return a thread-safe snapshot of the log ring buffer.
    func snapshotLogs() -> [LogEntry] {
        logBufferLock.lock()
        let snapshot = logBuffer
        logBufferLock.unlock()
        return snapshot
    }

    // MARK: - App readiness check

    func waitForAppReady(injector: TouchInjector) -> Bool {
        let app = injector.app
        if app.state == .runningForeground { return true }
        NSLog("[SpecterQA] Waiting for app foreground (state=\(app.state.rawValue))…")
        return app.wait(for: .runningForeground, timeout: 10)
    }

    // MARK: - Optional v2 subsystems (set by SpecterQARunner after init)

    var screenshotCapture: SpecterQAScreenshot? = nil
    var elementQuery: SpecterQAElementQuery? = nil
}

// MARK: - HTTPResponse

struct HTTPResponse {
    let statusCode: Int
    let body: [String: Any]

    // Raw data path (used for AccessibilityTree which returns pre-encoded JSON)
    private let rawData: Data?

    init(statusCode: Int, body: [String: Any]) {
        self.statusCode = statusCode
        self.body = body
        self.rawData = nil
    }

    private init(statusCode: Int, rawData: Data) {
        self.statusCode = statusCode
        self.body = [:]
        self.rawData = rawData
    }

    static func rawData(_ data: Data, statusCode: Int) -> HTTPResponse {
        HTTPResponse(statusCode: statusCode, rawData: data)
    }

    static func ok(_ payload: [String: Any]) -> HTTPResponse {
        HTTPResponse(statusCode: 200, body: payload)
    }

    static func success(_ result: [String: Any] = [:]) -> HTTPResponse {
        var body: [String: Any] = ["success": true]
        result.forEach { body[$0.key] = $0.value }
        return HTTPResponse(statusCode: 200, body: body)
    }

    static func error(_ message: String, code: Int = 400) -> HTTPResponse {
        HTTPResponse(statusCode: code, body: ["success": false, "error": message])
    }

    static func notFound(_ path: String) -> HTTPResponse {
        HTTPResponse(statusCode: 404, body: ["success": false, "error": "Unknown route: \(path)"])
    }

    /// Recursively replace non-finite floats with 0 to prevent NSInvalidArgumentException
    private func sanitize(_ obj: Any) -> Any {
        if let dict = obj as? [String: Any] {
            return dict.mapValues { sanitize($0) }
        } else if let arr = obj as? [Any] {
            return arr.map { sanitize($0) }
        } else if let d = obj as? Double, !d.isFinite {
            return 0.0
        } else if let f = obj as? Float, !f.isFinite {
            return Float(0.0)
        } else if let cg = obj as? CGFloat, !cg.isFinite {
            return 0.0
        }
        return obj
    }

    func serialized() -> Data {
        let jsonData: Data
        if let raw = rawData {
            jsonData = raw
        } else {
            let safeBody = sanitize(body) as? [String: Any] ?? body
            jsonData = (try? JSONSerialization.data(withJSONObject: safeBody)) ?? Data("{\"error\":\"serialization failed\"}".utf8)
        }
        let jsonStr = String(data: jsonData, encoding: .utf8) ?? "{}"
        let http = "HTTP/1.1 \(statusCode) \(statusText)\r\nContent-Type: application/json\r\nContent-Length: \(jsonStr.utf8.count)\r\nConnection: close\r\n\r\n\(jsonStr)"
        return Data(http.utf8)
    }

    private var statusText: String {
        switch statusCode {
        case 200: return "OK"
        case 400: return "Bad Request"
        case 404: return "Not Found"
        case 408: return "Request Timeout"
        case 422: return "Unprocessable Entity"
        case 500: return "Internal Server Error"
        case 503: return "Service Unavailable"
        default:  return "Unknown"
        }
    }
}

// MARK: - Errors

enum HTTPServerError: Error {
    case socketFailed(String)
    case bindFailed(String)
    case listenFailed(String)
}
