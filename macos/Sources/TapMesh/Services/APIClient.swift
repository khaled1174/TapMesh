import Foundation
import TapMeshCore

enum ClientError: LocalizedError {
    case configuration(String), transport(Int), protocolError(String), service(ToolError), operationBusy
    var errorDescription: String? {
        switch self {
        case .configuration(let text), .protocolError(let text): text
        case .transport(let code): String(localized: "The service returned HTTP \(code). Check local service status and reconnect TapMesh.")
        case .service(let error): DisplayText.serviceError(error)
        case .operationBusy: String(localized: "An update is waiting for active device work or recordings to finish.")
        }
    }
}

struct APIClient: Sendable {
    let configuration: ConnectionConfiguration
    private struct ServiceFailure: Decodable { let error: ToolError }
    /// True for every way a cancelled request can surface: Swift task cancellation, and the
    /// `URLError(.cancelled)` that URLSession throws when the task awaiting it is cancelled.
    static func isCancellation(_ error: Error) -> Bool {
        error is CancellationError || (error as? URLError)?.code == .cancelled
    }
    /// Runs a URLSession call, reporting a cancelled request as `CancellationError`
    /// so callers see one kind of cancellation, never the system's wording.
    private static func cancellable<T>(_ operation: () async throws -> T) async throws -> T {
        do { return try await operation() }
        catch where isCancellation(error) { throw CancellationError() }
    }
    func request(_ path: String, method: String = "GET", body: JSONValue? = nil, approvals: Bool = false, timeout: TimeInterval = 30, agent: Bool = false) async throws -> JSONValue {
        let request = try configuration.request(path, method: method, body: body, ownerAuthorized: approvals, timeout: timeout, agent: agent)
        return try await send(request)
    }
    private func send(_ request: URLRequest) async throws -> JSONValue {
        let (data, response) = try await Self.cancellable { try await URLSession.shared.data(for: request) }
        guard let http = response as? HTTPURLResponse else { throw ClientError.protocolError(String(localized: "The service did not send an HTTP response.")) }
        guard (200..<300).contains(http.statusCode) else {
            if let failure = try? JSONDecoder().decode(ServiceFailure.self, from: data) {
                if http.statusCode == 409 && failure.error.code == "overloaded" { throw ClientError.operationBusy }
                throw ClientError.service(failure.error)
            }
            throw ClientError.transport(http.statusCode)
        }
        guard data.count <= 16_000_000 else { throw ClientError.protocolError(String(localized: "The service response exceeded the 16 MB application limit.")) }
        return try JSONDecoder().decode(JSONValue.self, from: data)
    }
    func takeover(sessionID: String, deviceID: String) async throws -> TakeoverReceipt {
        let request = try configuration.takeoverRequest(sessionID: sessionID, deviceID: deviceID)
        return try TakeoverReceipt(response: await send(request), requestedDeviceID: deviceID)
    }
    func call(sessionID: String, tool: String, arguments: [String: JSONValue]) async throws -> ToolEnvelope {
        let body = JSONValue.object(["session_id": .string(sessionID), "tool": .string(tool), "arguments": .object(arguments)])
        let timeout: TimeInterval = tool == "build_and_run" ? 1860 : 135
        let request: URLRequest
        do {
            request = try configuration.request("api/call", method: "POST", body: body, ownerAuthorized: true, timeout: timeout)
        } catch LocalConnectionError.missingCredential, LocalConnectionError.ownerAuthorizationRequired {
            request = try configuration.request("api/call", method: "POST", body: body, timeout: timeout)
        }
        let result = try await send(request)
        return try JSONDecoder().decode(ToolEnvelope.self, from: JSONEncoder().encode(result))
    }
    func artifact(_ id: String, saveTo destination: URL) async throws {
        guard !id.isEmpty, id.allSatisfy({ $0.isLetter || $0.isNumber || $0 == "-" || $0 == "_" }) else { throw ClientError.protocolError(String(localized: "Invalid artifact identifier.")) }
        var request = URLRequest(url: configuration.apiURL.appendingPathComponent("api/artifacts/\(id)"), timeoutInterval: 120)
        request.setValue("Bearer \(try configuration.credential(at: configuration.serverTokenFile))", forHTTPHeaderField: "Authorization")
        let (temporaryURL, response) = try await Self.cancellable { try await URLSession.shared.download(for: request) }
        defer { try? FileManager.default.removeItem(at: temporaryURL) }
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else { throw ClientError.protocolError(String(localized: "Could not retrieve this artifact. Check its availability in the server runtime.")) }
        // The save panel has already handled the user's overwrite decision.
        // Stream recordings to disk instead of buffering large files in Mac memory.
        if FileManager.default.fileExists(atPath: destination.path) { _ = try FileManager.default.replaceItemAt(destination, withItemAt: temporaryURL) }
        else { try FileManager.default.moveItem(at: temporaryURL, to: destination) }
    }
}
