import Foundation

public enum LocalConnectionError: LocalizedError {
    case invalidConfiguration, missingCredential, ownerAuthorizationRequired, invalidRequest
    public var errorDescription: String? {
        switch self {
        case .invalidConfiguration: String(localized: "TapMesh requires local loopback HTTP services and absolute local credential paths. Review connection.json.")
        case .missingCredential: String(localized: "A required local credential file is missing or invalid. Reconnect or review the selected connection in Settings.")
        case .ownerAuthorizationRequired: String(localized: "This action requires the TapMesh instance that started the local services. For a custom connection, check the approval credential in connection.json.")
        case .invalidRequest: String(localized: "The local control request has no valid session or device target.")
        }
    }
}

public struct ConnectionConfiguration: Decodable, Sendable {
    public let apiURL: URL
    public let agentURL: URL
    public let serverTokenFile: String
    public let agentTokenFile: String
    public let approvalTokenFile: String?
    private var approvalSecret: String?
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
    public static func load(from file: URL = fileURL, approvalSecret: String? = nil) throws -> Self {
        if let approvalSecret {
            guard approvalSecret.utf8.count == 64,
                  approvalSecret.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }) else {
                throw LocalConnectionError.missingCredential
            }
        }
        var result = try JSONDecoder().decode(Self.self, from: Data(contentsOf: file))
        result.approvalSecret = approvalSecret
        try result.validate()
        return result
    }
    public func validate() throws {
        for url in [apiURL, agentURL] {
            guard url.scheme == "http", ["127.0.0.1", "localhost", "[::1]", "::1"].contains(url.host ?? ""), url.user == nil, url.password == nil, url.query == nil, url.fragment == nil else { throw LocalConnectionError.invalidConfiguration }
        }
        for path in [serverTokenFile, agentTokenFile, approvalTokenFile].compactMap({ $0 }) {
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
        if ownerAuthorized {
            let secret: String
            if let approvalSecret { secret = approvalSecret }
            else if let approvalTokenFile { secret = try credential(at: approvalTokenFile) }
            else { throw LocalConnectionError.ownerAuthorizationRequired }
            request.setValue("Bearer \(secret)", forHTTPHeaderField: "X-DeviceBridge-Local-Authorization")
        }
        if let body { request.httpBody = try JSONEncoder().encode(body); request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        return request
    }
    /// The long-lived live video request for one device: `GET /api/live/{device_id}?session_id=…`,
    /// with the same credential as every other `/api` call. The device id is one path segment.
    /// No request timeout: the stream's own silence check ends a dead stream.
    public func liveRequest(sessionID: String, deviceID: String) throws -> URLRequest {
        guard !sessionID.isEmpty, !deviceID.isEmpty else { throw LocalConnectionError.invalidRequest }
        var base = try request("api/live", timeout: Self.liveTimeout)
        var segment = CharacterSet.urlPathAllowed; segment.remove(charactersIn: "/;")
        guard let encoded = deviceID.addingPercentEncoding(withAllowedCharacters: segment), let url = base.url,
              var components = URLComponents(url: url, resolvingAgainstBaseURL: false) else { throw LocalConnectionError.invalidRequest }
        components.percentEncodedPath += "/" + encoded
        components.queryItems = [URLQueryItem(name: "session_id", value: sessionID)]
        guard let liveURL = components.url else { throw LocalConnectionError.invalidRequest }
        base.url = liveURL
        base.setValue("application/vnd.tapmesh.live", forHTTPHeaderField: "Accept")
        return base
    }
    /// A week: in practice no limit; the stream is ended by TapMesh or by its silence check.
    public static let liveTimeout: TimeInterval = 7 * 24 * 3600
    public func takeoverRequest(sessionID: String, deviceID: String) throws -> URLRequest {
        guard !sessionID.isEmpty, !deviceID.isEmpty else { throw LocalConnectionError.invalidRequest }
        return try request("api/takeover", method: "POST", body: .object(["session_id": .string(sessionID), "device_id": .string(deviceID)]), ownerAuthorized: true)
    }
}
