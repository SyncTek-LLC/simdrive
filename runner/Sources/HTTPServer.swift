import Foundation
import Network

/// Lightweight HTTP/1.1 server built on NWListener (Network.framework).
///
/// Zero external dependencies — runs inside the XCTest process on device or
/// simulator.  Implements a minimal subset of HTTP/1.1:
///   - Reads full request (headers + body) from each connection
///   - Routes to the appropriate TouchInjector method
///   - Writes a JSON response and closes the connection (Connection: close)
///
/// Supported endpoints:
///   POST /tap           {"x":200,"y":400,"duration":0.0}
///   POST /swipe         {"fromX":200,"fromY":400,"toX":200,"toY":100,"duration":0.3}
///   POST /type          {"text":"hello"}
///   POST /key           {"key":"return"}
///   POST /press_button  {"button":"home"}
///   GET  /screenshot    → {"base64":"…","width":390,"height":844}
///   GET  /source        → JSON element tree (accessibility snapshot)
///   GET  /health        → {"status":"ok","port":8222}
///   POST /shutdown      → shuts the server down
///
final class HTTPServer {

    // MARK: - Properties

    private let port: UInt16
    private let injector: TouchInjector
    private var listener: NWListener?
    private let stopSemaphore = DispatchSemaphore(value: 0)
    private let queue = DispatchQueue(label: "com.specterqa.http-server", qos: .userInitiated)

    // MARK: - Init

    init(port: UInt16, injector: TouchInjector) {
        self.port = port
        self.injector = injector
    }

    // MARK: - Lifecycle

    func start() throws {
        let params = NWParameters.tcp
        params.allowLocalEndpointReuse = true

        let nwPort = NWEndpoint.Port(rawValue: port)!
        let listener = try NWListener(using: params, on: nwPort)
        self.listener = listener

        listener.newConnectionHandler = { [weak self] connection in
            self?.handleConnection(connection)
        }

        listener.stateUpdateHandler = { state in
            switch state {
            case .ready:
                NSLog("[SpecterQA] HTTP server ready on port \(self.port)")
            case .failed(let error):
                NSLog("[SpecterQA] HTTP server failed: \(error)")
            default:
                break
            }
        }

        listener.start(queue: queue)
    }

    func stop() {
        listener?.cancel()
        stopSemaphore.signal()
    }

    /// Blocks the calling thread until POST /shutdown is received.
    func waitUntilStopped() {
        stopSemaphore.wait()
    }

    // MARK: - Connection handling

    private func handleConnection(_ connection: NWConnection) {
        connection.start(queue: queue)
        receiveRequest(from: connection)
    }

    private func receiveRequest(from connection: NWConnection) {
        // Read up to 64 KB — sufficient for all our request types.
        connection.receive(minimumIncompleteLength: 1, maximumLength: 65536) { [weak self] data, _, isComplete, error in
            guard let self = self else { return }

            if let error = error {
                NSLog("[SpecterQA] Receive error: \(error)")
                connection.cancel()
                return
            }

            guard let data = data, !data.isEmpty else {
                if isComplete { connection.cancel() }
                return
            }

            let (responseData, shouldStop) = self.processRequest(data)
            self.sendResponse(responseData, to: connection) {
                connection.cancel()
                if shouldStop {
                    self.stop()
                }
            }
        }
    }

    // MARK: - Request processing

    /// Parse raw HTTP bytes, route, execute, return (responseBody, shouldShutdown).
    private func processRequest(_ raw: Data) -> (Data, Bool) {
        guard let request = RequestParser.parse(raw) else {
            return (jsonError("could not parse request", status: 400), false)
        }

        NSLog("[SpecterQA] \(request.method) \(request.path)")

        switch (request.method, request.path) {

        case ("GET", "/health"):
            let body: [String: Any] = ["status": "ok", "port": port]
            return (jsonResponse(body), false)

        case ("POST", "/shutdown"):
            let body: [String: Any] = ["status": "stopping"]
            return (jsonResponse(body), true)

        case ("POST", "/tap"):
            guard
                let x        = request.json["x"] as? Double,
                let y        = request.json["y"] as? Double
            else {
                return (jsonError("tap requires x, y (numbers)", status: 422), false)
            }
            let duration = request.json["duration"] as? Double ?? 0.0
            injector.tap(x: x, y: y, duration: duration)
            return (jsonResponse(["status": "ok"]), false)

        case ("POST", "/swipe"):
            guard
                let fromX    = request.json["fromX"] as? Double,
                let fromY    = request.json["fromY"] as? Double,
                let toX      = request.json["toX"]   as? Double,
                let toY      = request.json["toY"]   as? Double
            else {
                return (jsonError("swipe requires fromX, fromY, toX, toY", status: 422), false)
            }
            let duration = request.json["duration"] as? Double ?? 0.3
            injector.swipe(fromX: fromX, fromY: fromY, toX: toX, toY: toY, duration: duration)
            return (jsonResponse(["status": "ok"]), false)

        case ("POST", "/type"):
            guard let text = request.json["text"] as? String else {
                return (jsonError("type requires text (string)", status: 422), false)
            }
            injector.typeText(text)
            return (jsonResponse(["status": "ok"]), false)

        case ("POST", "/key"):
            guard let key = request.json["key"] as? String else {
                return (jsonError("key requires key (string)", status: 422), false)
            }
            do {
                try injector.pressKey(key)
                return (jsonResponse(["status": "ok"]), false)
            } catch {
                return (jsonError("unknown key: \(key)", status: 422), false)
            }

        case ("POST", "/press_button"):
            guard let button = request.json["button"] as? String else {
                return (jsonError("press_button requires button (string)", status: 422), false)
            }
            do {
                try injector.pressButton(button)
                return (jsonResponse(["status": "ok"]), false)
            } catch {
                return (jsonError("unknown button: \(button)", status: 422), false)
            }

        case ("GET", "/screenshot"):
            // Wait for app to be in foreground before capturing.
            if !waitForAppReady() {
                return (jsonError("app not running — timed out waiting for foreground", status: 503), false)
            }
            let (png, size) = injector.screenshot()
            let b64 = png.base64EncodedString()
            let body: [String: Any] = [
                "base64": b64,
                "width":  Int(size.width),
                "height": Int(size.height)
            ]
            return (jsonResponse(body), false)

        case ("GET", "/source"):
            // Wait for app to be in foreground before querying tree.
            if !waitForAppReady() {
                return (jsonError("app not running — timed out waiting for foreground", status: 503), false)
            }
            let (treeData, _) = AccessibilityTree.capture(app: injector.app)
            return (treeData, false)

        default:
            return (jsonError("not found: \(request.method) \(request.path)", status: 404), false)
        }
    }

    // MARK: - App readiness

    /// Wait up to 10 seconds for the app to reach foreground.
    /// Returns true if the app is running, false on timeout.
    private func waitForAppReady() -> Bool {
        let app = injector.app
        if app.state == .runningForeground { return true }

        // App may still be launching — give it time.
        NSLog("[SpecterQA] Waiting for app to reach foreground (state=\(app.state.rawValue))...")
        return app.wait(for: .runningForeground, timeout: 10)
    }

    // MARK: - Response helpers

    private func sendResponse(_ body: Data, to connection: NWConnection, completion: @escaping () -> Void) {
        let header = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: \(body.count)\r\nConnection: close\r\n\r\n"
        var responseData = header.data(using: .utf8)!
        responseData.append(body)

        connection.send(content: responseData, completion: .contentProcessed { error in
            if let error = error {
                NSLog("[SpecterQA] Send error: \(error)")
            }
            completion()
        })
    }

    private func jsonResponse(_ dict: [String: Any]) -> Data {
        (try? JSONSerialization.data(withJSONObject: dict)) ?? Data("{}".utf8)
    }

    private func jsonError(_ message: String, status: Int) -> Data {
        let dict: [String: Any] = ["error": message, "status": status]
        return (try? JSONSerialization.data(withJSONObject: dict)) ?? Data("{}".utf8)
    }
}
