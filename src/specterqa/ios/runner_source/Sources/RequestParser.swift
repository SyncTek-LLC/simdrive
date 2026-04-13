import Foundation

/// Parsed representation of a single HTTP/1.1 request.
///
/// v2 addition: `query` dict for parsed URL query string parameters
/// (e.g. GET /screenshot?scale=0.5&format=jpeg → query["scale"] = "0.5").
struct HTTPRequest {
    let method:  String
    let path:    String
    /// Parsed query string parameters from the URL (e.g. ?key=value).
    let query:   [String: String]
    let headers: [String: String]
    /// Decoded JSON body as a dictionary. Empty for requests with no body.
    let json:    [String: Any]
}

/// Minimal HTTP/1.1 request parser.
///
/// Parses raw bytes from the socket into an `HTTPRequest`.  Only the features
/// required by the SpecterQA runner are implemented:
///   - Request line  (METHOD path HTTP/1.1)
///   - Header lines  (Key: Value)
///   - JSON body     (decoded via JSONSerialization)
///   - Query string  (?key=value&key2=value2, percent-decoded)
///
/// Does not support:
///   - Chunked transfer encoding
///   - Multipart bodies
///
enum RequestParser {

    // MARK: - Public API

    /// Parse raw socket bytes.  Returns nil if the data is not a valid HTTP request.
    static func parse(_ raw: Data) -> HTTPRequest? {
        guard let text = String(data: raw, encoding: .utf8) else { return nil }
        return parseString(text)
    }

    // MARK: - Implementation

    private static func parseString(_ text: String) -> HTTPRequest? {
        // Split on the header/body separator (\r\n\r\n or \n\n).
        let separatorCRLF = "\r\n\r\n"
        let separatorLF   = "\n\n"

        let headerBlock: String
        let bodyText: String

        if let range = text.range(of: separatorCRLF) {
            headerBlock = String(text[text.startIndex ..< range.lowerBound])
            bodyText    = String(text[range.upperBound...])
        } else if let range = text.range(of: separatorLF) {
            headerBlock = String(text[text.startIndex ..< range.lowerBound])
            bodyText    = String(text[range.upperBound...])
        } else {
            headerBlock = text
            bodyText    = ""
        }

        var lines = headerBlock.components(separatedBy: "\r\n")
        if lines.count == 1 {
            lines = headerBlock.components(separatedBy: "\n")
        }

        guard let requestLine = lines.first, !requestLine.isEmpty else { return nil }

        // Parse request line: METHOD SP path SP HTTP/x.x
        let requestParts = requestLine.split(separator: " ", maxSplits: 2, omittingEmptySubsequences: true)
        guard requestParts.count >= 2 else { return nil }

        let method   = String(requestParts[0]).uppercased()
        let fullPath = String(requestParts[1])

        // Split path and query string
        var path  = fullPath
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

        // Parse headers (lines after the request line, up to blank line).
        var headers: [String: String] = [:]
        for line in lines.dropFirst() {
            guard !line.isEmpty else { break }
            if let colonIndex = line.firstIndex(of: ":") {
                let key   = String(line[line.startIndex ..< colonIndex]).trimmingCharacters(in: .whitespaces).lowercased()
                let value = String(line[line.index(after: colonIndex)...]).trimmingCharacters(in: .whitespaces)
                headers[key] = value
            }
        }

        // Parse JSON body.
        var jsonBody: [String: Any] = [:]
        let trimmedBody = bodyText.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmedBody.isEmpty, let bodyData = trimmedBody.data(using: .utf8) {
            if let parsed = try? JSONSerialization.jsonObject(with: bodyData) as? [String: Any] {
                jsonBody = parsed
            }
        }

        return HTTPRequest(
            method:  method,
            path:    path,
            query:   query,
            headers: headers,
            json:    jsonBody
        )
    }
}
