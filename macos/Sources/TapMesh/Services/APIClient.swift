import Foundation
import TapMeshCore

enum ClientError: LocalizedError {
    case configuration(String), transport(Int), protocolError(String), operationBusy
    var errorDescription: String? {
        switch self {
        case .configuration(let text), .protocolError(let text): text
        case .transport(let code): "The service returned HTTP \(code). Check local service status and reconnect TapMesh."
        case .operationBusy: "An update is waiting for active device work or recordings to finish."
        }
    }
}

struct APIClient: Sendable {
    let configuration: ConnectionConfiguration
    func request(_ path: String, method: String = "GET", body: JSONValue? = nil, approvals: Bool = false, timeout: TimeInterval = 30, agent: Bool = false) async throws -> JSONValue {
        let request = try configuration.request(path, method: method, body: body, ownerAuthorized: approvals, timeout: timeout, agent: agent)
        return try await send(request)
    }
    private func send(_ request: URLRequest) async throws -> JSONValue {
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw ClientError.protocolError("The service did not send an HTTP response.") }
        guard (200..<300).contains(http.statusCode) else {
            if let json = try? JSONDecoder().decode(JSONValue.self, from: data), let message = json["error"]?["message"]?.stringValue {
                if http.statusCode == 409 && json["error"]?["code"]?.stringValue == "overloaded" { throw ClientError.operationBusy }
                throw ClientError.protocolError("\(message) (\(json["error"]?["code"]?.stringValue ?? "request_failed"))")
            }
            throw ClientError.transport(http.statusCode)
        }
        guard data.count <= 16_000_000 else { throw ClientError.protocolError("The service response exceeded the 16 MB application limit.") }
        return try JSONDecoder().decode(JSONValue.self, from: data)
    }
    func takeover(sessionID: String, deviceID: String) async throws -> TakeoverReceipt {
        let request = try configuration.takeoverRequest(sessionID: sessionID, deviceID: deviceID)
        return try TakeoverReceipt(response: await send(request), requestedDeviceID: deviceID)
    }
    func call(sessionID: String, tool: String, arguments: [String: JSONValue]) async throws -> ToolEnvelope {
        let body = JSONValue.object(["session_id": .string(sessionID), "tool": .string(tool), "arguments": .object(arguments)])
        let result = try await request("api/call", method: "POST", body: body, timeout: tool == "build_and_run" ? 1860 : 135)
        return try JSONDecoder().decode(ToolEnvelope.self, from: JSONEncoder().encode(result))
    }
    func artifact(_ id: String, saveTo destination: URL) async throws {
        guard !id.isEmpty, id.allSatisfy({ $0.isLetter || $0.isNumber || $0 == "-" || $0 == "_" }) else { throw ClientError.protocolError("Invalid artifact identifier.") }
        var request = URLRequest(url: configuration.apiURL.appendingPathComponent("api/artifacts/\(id)"), timeoutInterval: 120)
        request.setValue("Bearer \(try configuration.credential(at: configuration.serverTokenFile))", forHTTPHeaderField: "Authorization")
        let (temporaryURL, response) = try await URLSession.shared.download(for: request)
        defer { try? FileManager.default.removeItem(at: temporaryURL) }
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else { throw ClientError.protocolError("Could not retrieve this artifact. Check its availability in the server runtime.") }
        // The save panel has already handled the user's overwrite decision.
        // Stream recordings to disk instead of buffering large files in Mac memory.
        if FileManager.default.fileExists(atPath: destination.path) { _ = try FileManager.default.replaceItemAt(destination, withItemAt: temporaryURL) }
        else { try FileManager.default.moveItem(at: temporaryURL, to: destination) }
    }
}
