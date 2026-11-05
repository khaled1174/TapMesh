import Foundation

public enum LocalConnectionError: LocalizedError {
    case invalidConfiguration, missingCredential, ownerAuthorizationRequired, invalidRequest
    public var errorDescription: String? {
        switch self {
        case .invalidConfiguration: "TapMesh requires local loopback HTTP services and absolute local credential paths. Review connection.json."
        case .missingCredential: "A required local credential file is missing or invalid. Reconnect or review the selected connection in Settings."
        case .ownerAuthorizationRequired: "This action requires the private local owner credential. Check the approval credential in connection.json."
        case .invalidRequest: "The local control request has no valid session or device target."
        }
    }
}

public struct ConnectionConfiguration: Decodable, Sendable {
    public let apiURL: URL
    public let agentURL: URL
    public let serverTokenFile: String
    public let agentTokenFile: String
    public let approvalTokenFile: String
    enum CodingKeys: String, CodingKey {
        case apiURL = "api_url", agentURL = "agent_url", serverTokenFile = "server_token_file", agentTokenFile = "agent_token_file", approvalTokenFile = "approval_token_file"
    }
    public static let legacyFileURL = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/TapMesh/connection.json")
    public static var fileURL: URL { (try? defaultFileURL()) ?? legacyFileURL.deletingLastPathComponent().appendingPathComponent("Local/connection.json") }
    public static func localProfileDirectory(environment: [String: String] = ProcessInfo.processInfo.environment, homeDirectory: URL = FileManager.default.homeDirectoryForCurrentUser) throws -> URL {
        if let path = environment["TAPMESH_PROFILE_DIR"] {
            guard path.hasPrefix("/"), !path.contains("\0") else { throw LocalConnectionError.invalidConfiguration }
            return URL(fileURLWithPath: path, isDirectory: true).standardizedFileURL
        }
        return homeDirectory.appendingPathComponent("Library/Application Support/TapMesh/Local", isDirectory: true)
    }
    public static func defaultFileURL(environment: [String: String] = ProcessInfo.processInfo.environment, homeDirectory: URL = FileManager.default.homeDirectoryForCurrentUser) throws -> URL {
        if let path = environment["TAPMESH_CONNECTION_FILE"] {
            guard path.hasPrefix("/"), !path.contains("\0") else { throw LocalConnectionError.invalidConfiguration }
            return URL(fileURLWithPath: path).standardizedFileURL
        }
        return try localProfileDirectory(environment: environment, homeDirectory: homeDirectory).appendingPathComponent("connection.json")
    }
    public static func load(from file: URL = fileURL) throws -> Self {
        let result = try JSONDecoder().decode(Self.self, from: Data(contentsOf: file))
        try result.validate()
        return result
    }
    public func validate() throws {
        for url in [apiURL, agentURL] {
            guard url.scheme == "http", ["127.0.0.1", "localhost", "[::1]", "::1"].contains(url.host ?? ""), url.user == nil, url.password == nil, url.query == nil, url.fragment == nil else { throw LocalConnectionError.invalidConfiguration }
        }
        for path in [serverTokenFile, agentTokenFile, approvalTokenFile] {
            guard NSString(string: path).expandingTildeInPath.hasPrefix("/"), !path.isEmpty else { throw LocalConnectionError.invalidConfiguration }
        }
    }
    public func credential(at path: String) throws -> String {
        let expanded = NSString(string: path).expandingTildeInPath
        guard expanded.hasPrefix("/"), let value = try? String(contentsOfFile: expanded, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines), !value.isEmpty, !value.contains(where: { $0.isWhitespace || $0.isNewline }) else { throw LocalConnectionError.missingCredential }
        return value
    }
    public func request(_ path: String, method: String = "GET", body: JSONValue? = nil, ownerAuthorized: Bool = false, timeout: TimeInterval = 30, agent: Bool = false) throws -> URLRequest {
        try validate()
        if path == "api/takeover", !ownerAuthorized { throw LocalConnectionError.ownerAuthorizationRequired }
        var request = URLRequest(url: (agent ? agentURL : apiURL).appendingPathComponent(path), timeoutInterval: timeout)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if path != "health" { request.setValue("Bearer \(try credential(at: agent ? agentTokenFile : serverTokenFile))", forHTTPHeaderField: "Authorization") }
        if ownerAuthorized { request.setValue("Bearer \(try credential(at: approvalTokenFile))", forHTTPHeaderField: "X-DeviceBridge-Local-Authorization") }
        if let body { request.httpBody = try JSONEncoder().encode(body); request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        return request
    }
    public func takeoverRequest(sessionID: String, deviceID: String) throws -> URLRequest {
        guard !sessionID.isEmpty, !deviceID.isEmpty else { throw LocalConnectionError.invalidRequest }
        return try request("api/takeover", method: "POST", body: .object(["session_id": .string(sessionID), "device_id": .string(deviceID)]), ownerAuthorized: true)
    }
}
