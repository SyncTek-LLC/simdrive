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
//  Supported endpoints (superset of v1 + v2):
//    POST /tap           — coordinate or element tap (auto-recovers from backgrounding)
//    POST /swipe         — swipe gesture (fromX/fromY/toX/toY or direction)
//    POST /type          — type text
//    POST /key           — press named key (return/tab crash-safe)
//    POST /press_button  — hardware button (home/volumeup/volumedown)
//    GET  /screenshot    — base64 JPEG/PNG with scale/quality params
//    GET  /source        — JSON accessibility tree
//    GET  /health        — health check
//    GET  /elements      — element query with ?limit=N&types=... (includes isHittable)
//    GET  /webview       — WKWebView descendant elements only
//    POST /wait          — wait for element by label
//    POST /scroll        — scroll gesture
//    POST /launch        — launch/activate app by bundle_id
//    POST /terminate     — terminate app by bundle_id
//    POST /dismiss-alert — dismiss visible system alert or sheet
//    POST /shutdown      — graceful shutdown (v1 compat)
//    POST /stop          — graceful shutdown (v2 compat alias)
//    POST /appearance    — set dark/light mode via XCUIDevice (avoids simctl conflict)
//    GET  /app_state     — current XCUIApplication state (string + raw int)
//    POST /idle          — wait until element tree is stable (two snapshots match)
//    GET  /logs          — log stream stub (OSLogStore requires unavailable entitlements)
//

import Foundation
import Darwin
import XCTest

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

    // v2-style router (optional) — set after init for the v2 architecture
    private var routerV2: RouterV2?

    /// Semaphore signaled when /shutdown or /stop is received.
    let stopSemaphore = DispatchSemaphore(value: 0)

    // MARK: - Init

    init(port: UInt16 = 8222, injector: TouchInjector) {
        self.port = port
        self.injector = injector
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

        let response = route(request: request)
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

    // MARK: - Main thread dispatch
    //
    // Uses CFRunLoopPerformBlock + CFRunLoopWakeUp so that dispatch blocks
    // are processed by the CFRunLoopRunInMode loop in SpecterQARunner.swift.
    // DispatchQueue.main.sync would also work but can deadlock if the main
    // thread is already inside a sync call; the RunLoop approach is safer.

    private func runOnMain(_ block: @escaping () -> Void) {
        if Thread.isMainThread {
            block()
        } else {
            let sem = DispatchSemaphore(value: 0)
            CFRunLoopPerformBlock(CFRunLoopGetMain(), CFRunLoopMode.defaultMode.rawValue) {
                block()
                sem.signal()
            }
            CFRunLoopWakeUp(CFRunLoopGetMain())
            sem.wait()
        }
    }

    // MARK: - App readiness check

    private func waitForAppReady() -> Bool {
        let app = injector.app
        if app.state == .runningForeground { return true }
        NSLog("[SpecterQA] Waiting for app foreground (state=\(app.state.rawValue))…")
        return app.wait(for: .runningForeground, timeout: 10)
    }

    // MARK: - Router

    private func route(request: ParsedRequest) -> HTTPResponse {
        switch (request.method, request.path) {

        // ── Health ─────────────────────────────────────────────────────────────
        case ("GET", "/health"):
            return HTTPResponse.ok([
                "success": true,
                "status": "ok",
                "port": port,
                "pid": ProcessInfo.processInfo.processIdentifier
            ])

        // ── Shutdown (both aliases) ────────────────────────────────────────────
        case ("POST", "/shutdown"), ("POST", "/stop"):
            DispatchQueue.global().asyncAfter(deadline: .now() + 0.05) {
                self.stopSemaphore.signal()
            }
            return HTTPResponse.success(["message": "Shutting down"])

        // ── Tap ───────────────────────────────────────────────────────────────
        case ("POST", "/tap"):
            let body = request.body
            if let x = body["x"] as? Double, let y = body["y"] as? Double {
                let duration = body["duration"] as? Double ?? 0.0
                var autoRecovered = false
                runOnMain {
                    self.injector.tap(x: x, y: y, duration: duration)
                    // Fix 5: auto-recover if tap sent the app to background
                    if self.injector.app.state != .runningForeground {
                        NSLog("[SpecterQA] tap: app backgrounded after tap — activating")
                        self.injector.app.activate()
                        Thread.sleep(forTimeInterval: 1.0)
                        autoRecovered = true
                    }
                }
                var result: [String: Any] = ["mode": "coordinate", "x": x, "y": y]
                if autoRecovered {
                    result["warning"] = "App was backgrounded and auto-recovered"
                }
                return HTTPResponse.success(result)
            }
            return HTTPResponse.error("tap requires x, y (numbers)", code: 422)

        // ── Swipe ─────────────────────────────────────────────────────────────
        case ("POST", "/swipe"):
            let body = request.body
            // Coordinate swipe: {fromX, fromY, toX, toY, duration?}
            if let fromX = body["fromX"] as? Double,
               let fromY = body["fromY"] as? Double,
               let toX   = body["toX"]   as? Double,
               let toY   = body["toY"]   as? Double {
                let duration = body["duration"] as? Double ?? 0.3
                runOnMain { self.injector.swipe(fromX: fromX, fromY: fromY, toX: toX, toY: toY, duration: duration) }
                return HTTPResponse.success(["mode": "coordinate"])
            }
            return HTTPResponse.error("swipe requires fromX, fromY, toX, toY", code: 422)

        // ── Type ──────────────────────────────────────────────────────────────
        case ("POST", "/type"):
            guard let text = request.body["text"] as? String else {
                return HTTPResponse.error("type requires text (string)", code: 422)
            }
            runOnMain { self.injector.typeText(text) }
            return HTTPResponse.success(["characters": text.count])

        // ── Key ───────────────────────────────────────────────────────────────
        case ("POST", "/key"):
            guard let key = request.body["key"] as? String else {
                return HTTPResponse.error("key requires key (string)", code: 422)
            }
            var keyError: String? = nil
            runOnMain {
                do { try self.injector.pressKey(key) }
                catch { keyError = error.localizedDescription }
            }
            if let err = keyError {
                return HTTPResponse.error(err, code: 422)
            }
            return HTTPResponse.success(["key": key])

        // ── Press hardware button ─────────────────────────────────────────────
        case ("POST", "/press_button"):
            guard let button = request.body["button"] as? String else {
                return HTTPResponse.error("press_button requires button (string)", code: 422)
            }
            var buttonError: String? = nil
            runOnMain {
                do { try self.injector.pressButton(button) }
                catch { buttonError = error.localizedDescription }
            }
            if let err = buttonError {
                return HTTPResponse.error(err, code: 422)
            }
            return HTTPResponse.success(["button": button])

        // ── Screenshot ────────────────────────────────────────────────────────
        case ("GET", "/screenshot"):
            var result: HTTPResponse = HTTPResponse.error("screenshot failed", code: 500)
            runOnMain {
                guard self.waitForAppReady() else {
                    result = HTTPResponse.error("app not running — timed out waiting for foreground", code: 503)
                    return
                }
                // v2: use SpecterQAScreenshot with scale/quality/format params
                if let screenshotCapture = self.screenshotCapture {
                    let opts = ScreenshotOptions.from(query: request.query)
                    let dict = screenshotCapture.captureToDict(options: opts)
                    let code = (dict["success"] as? Bool == true) ? 200 : 500
                    result = HTTPResponse(statusCode: code, body: dict)
                } else {
                    // v1 fallback: raw PNG
                    let (png, size) = self.injector.screenshot()
                    let b64 = png.base64EncodedString()
                    result = HTTPResponse.ok([
                        "base64": b64,
                        "width":  Int(size.width),
                        "height": Int(size.height)
                    ])
                }
            }
            return result

        // ── Accessibility source tree ─────────────────────────────────────────
        case ("GET", "/source"):
            var result: HTTPResponse = HTTPResponse.error("source failed", code: 500)
            runOnMain {
                guard self.waitForAppReady() else {
                    result = HTTPResponse.error("app not running — timed out waiting for foreground", code: 503)
                    return
                }
                let (treeData, statusCode) = AccessibilityTree.capture(app: self.injector.app)
                // AccessibilityTree returns raw JSON data; wrap in a response manually
                result = HTTPResponse.rawData(treeData, statusCode: statusCode)
            }
            return result

        // ── WebView elements ──────────────────────────────────────────────────
        case ("GET", "/webview"):
            guard let eq = elementQuery else {
                return HTTPResponse.error("element query not available", code: 503)
            }
            let elements = eq.queryWebViewElements(limit: 100)
            let json = elements.map { $0.dictionary }
            return HTTPResponse.ok(["success": true, "elements": json, "count": json.count])

        // ── Elements (v2 addition) ─────────────────────────────────────────────
        case ("GET", "/elements"):
            guard let eq = elementQuery else {
                return HTTPResponse.error("element query not available", code: 503)
            }
            let limit = Int(request.query["limit"] ?? "200") ?? 200
            let types = request.query["types"]
            let elements = eq.queryAll(limit: limit, types: types)
            let dicts = elements.map { $0.dictionary }
            return HTTPResponse.ok(["success": true, "result": dicts, "count": dicts.count])

        // ── Wait for element (v2 addition) ────────────────────────────────────
        case ("POST", "/wait"):
            guard let label = request.body["label"] as? String else {
                return HTTPResponse.error("wait requires {label}", code: 422)
            }
            guard let eq = elementQuery else {
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

        // ── Scroll (v2 addition) ───────────────────────────────────────────────
        case ("POST", "/scroll"):
            guard let direction = request.body["direction"] as? String else {
                return HTTPResponse.error("scroll requires {direction}", code: 422)
            }
            var scrollError: String? = nil
            runOnMain {
                let window = self.injector.app.windows.firstMatch
                guard window.exists else {
                    scrollError = "app window not found"
                    return
                }
                switch direction.lowercased() {
                case "up":    window.swipeUp()
                case "down":  window.swipeDown()
                case "left":  window.swipeLeft()
                case "right": window.swipeRight()
                default: scrollError = "Unknown scroll direction: \(direction)"
                }
            }
            if let err = scrollError {
                return HTTPResponse.error(err, code: 422)
            }
            return HTTPResponse.success(["direction": direction])

        // ── Launch app (v2 addition) ───────────────────────────────────────────
        case ("POST", "/launch"):
            guard let bundleID = request.body["bundle_id"] as? String else {
                return HTTPResponse.error("launch requires {bundle_id}", code: 422)
            }
            var launchResult: HTTPResponse = HTTPResponse.success(["action": "launch", "bundle_id": bundleID])
            runOnMain {
                let targetApp = XCUIApplication(bundleIdentifier: bundleID)
                targetApp.launch()
                NSLog("[SpecterQA] Launched app: \(bundleID)")
            }
            return launchResult

        // ── Terminate app (v2 addition) ────────────────────────────────────────
        case ("POST", "/terminate"):
            guard let bundleID = request.body["bundle_id"] as? String else {
                return HTTPResponse.error("terminate requires {bundle_id}", code: 422)
            }
            runOnMain {
                let targetApp = XCUIApplication(bundleIdentifier: bundleID)
                targetApp.terminate()
                NSLog("[SpecterQA] Terminated app: \(bundleID)")
            }
            return HTTPResponse.success(["bundle_id": bundleID])

        // ── Dismiss alert (v2 addition) ────────────────────────────────────────
        case ("POST", "/dismiss-alert"):
            var dismissResult: HTTPResponse = HTTPResponse.error("No alert or sheet visible", code: 404)
            runOnMain {
                let alert = self.injector.app.alerts.firstMatch
                let sheet = self.injector.app.sheets.firstMatch
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
                // Last resort: first non-denial button
                let denials: Set<String> = ["Don't Allow", "Deny", "Cancel", "Not Now", "Never"]
                for btn in target.buttons.allElementsBoundByIndex where !denials.contains(btn.label) && btn.isHittable {
                    btn.tap()
                    dismissResult = HTTPResponse.success(["dismissed_via": btn.label, "fallback": true])
                    return
                }
                dismissResult = HTTPResponse.error("Alert found but no tappable dismiss button")
            }
            return dismissResult

        // ── Appearance (Fix 2) ────────────────────────────────────────────────
        // Uses XCUIDevice.shared.appearance to avoid simctl conflict with active
        // XCTest session. Accepts {"mode": "dark"} or {"mode": "light"}.
        case ("POST", "/appearance"):
            guard let mode = request.body["mode"] as? String,
                  mode == "dark" || mode == "light" else {
                return HTTPResponse.error("appearance requires {mode: 'dark' | 'light'}", code: 422)
            }
            runOnMain {
                XCUIDevice.shared.appearance = (mode == "dark") ? .dark : .light
                NSLog("[SpecterQA] appearance set to \(mode)")
            }
            return HTTPResponse.success(["mode": mode])

        // ── App state (Fix 4) ─────────────────────────────────────────────────
        // Returns the current XCUIApplication state as a string and raw Int.
        case ("GET", "/app_state"):
            var stateResult: HTTPResponse = HTTPResponse.error("app_state failed", code: 500)
            runOnMain {
                let state = self.injector.app.state
                let stateStr: String
                switch state {
                case .notInstalled:       stateStr = "notInstalled"
                case .notRunning:         stateStr = "notRunning"
                case .runningBackgroundSuspended: stateStr = "runningBackgroundSuspended"
                case .runningBackground:  stateStr = "runningBackground"
                case .runningForeground:  stateStr = "runningForeground"
                @unknown default:         stateStr = "unknown"
                }
                stateResult = HTTPResponse.ok([
                    "state": stateStr,
                    "state_raw": state.rawValue
                ])
            }
            return stateResult

        // ── Idle wait (Fix 6) ─────────────────────────────────────────────────
        // Polls until the element tree is stable (two snapshots 300ms apart have
        // the same element count) or a timeout is reached.
        // Body: {"timeout": <seconds, default 10, max 30>}
        case ("POST", "/idle"):
            let rawTimeout = (request.body["timeout"] as? Double) ?? 10.0
            let idleTimeout = min(max(rawTimeout, 0), 30.0)
            var idleResult: HTTPResponse = HTTPResponse.error("idle check failed", code: 500)
            runOnMain {
                let deadline = Date().addingTimeInterval(idleTimeout)
                var waited: Double = 0.0
                let pollInterval: TimeInterval = 0.3

                // First ensure app is in foreground
                if self.injector.app.state != .runningForeground {
                    NSLog("[SpecterQA] idle: app not in foreground — aborting")
                    idleResult = HTTPResponse.error("app not in runningForeground state", code: 503)
                    return
                }

                while Date() < deadline {
                    let countBefore: Int
                    let countAfter: Int
                    do {
                        let snap1 = try self.injector.app.snapshot()
                        let c1 = self.countDescendants(snap1)
                        Thread.sleep(forTimeInterval: pollInterval)
                        waited += pollInterval
                        let snap2 = try self.injector.app.snapshot()
                        countBefore = c1
                        countAfter = self.countDescendants(snap2)
                    } catch {
                        // Snapshot failed — wait and retry
                        Thread.sleep(forTimeInterval: pollInterval)
                        waited += pollInterval
                        continue
                    }

                    if countBefore == countAfter {
                        NSLog("[SpecterQA] idle: stable after \(waited)s (count=\(countAfter))")
                        idleResult = HTTPResponse.ok(["status": "idle", "waited": waited])
                        return
                    }
                    // Tree is still changing — keep polling (no extra sleep, 300ms already spent)
                }

                NSLog("[SpecterQA] idle: timed out after \(idleTimeout)s")
                idleResult = HTTPResponse.ok(["status": "timeout", "waited": idleTimeout])
            }
            return idleResult

        // ── Log stream (Fix 7 — stub) ─────────────────────────────────────────
        // OSLogStore requires entitlements unavailable to XCTest runners.
        // Return a clear stub with a terminal alternative.
        case ("GET", "/logs"):
            return HTTPResponse.ok([
                "success": false,
                "error": "not yet implemented",
                "suggestion": "Use 'xcrun simctl spawn booted log stream --predicate \"subsystem == \\\"<bundle_id>\\\"\" --style json' from the terminal"
            ])

        default:
            return HTTPResponse.notFound(request.path)
        }
    }

    // MARK: - Snapshot descendant counter (used by /idle)

    private func countDescendants(_ snapshot: any XCUIElementSnapshot) -> Int {
        var count = 1
        for child in snapshot.children {
            count += countDescendants(child)
        }
        return count
    }

    // MARK: - Optional v2 subsystems (set by SpecterQARunner after init)

    var screenshotCapture: SpecterQAScreenshot? = nil
    var elementQuery: SpecterQAElementQuery? = nil
}

// MARK: - RouterV2 (internal stub — keeps the architecture extensible)

private struct RouterV2 {}

// MARK: - ParsedRequest (internal to HTTPServer)

private struct ParsedRequest {
    let method: String
    let path: String
    let query: [String: String]
    let body: [String: Any]
    let rawBody: Data
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
