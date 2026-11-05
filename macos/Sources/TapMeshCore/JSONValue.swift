import Foundation

public enum JSONValue: Codable, Equatable, Sendable {
    case object([String: JSONValue]), array([JSONValue]), string(String), number(Double), bool(Bool), null

    public init(from decoder: Decoder) throws {
        let box = try decoder.singleValueContainer()
        if box.decodeNil() { self = .null }
        else if let v = try? box.decode(Bool.self) { self = .bool(v) }
        else if let v = try? box.decode(Double.self) { self = .number(v) }
        else if let v = try? box.decode(String.self) { self = .string(v) }
        else if let v = try? box.decode([String: JSONValue].self) { self = .object(v) }
        else { self = .array(try box.decode([JSONValue].self)) }
    }
    public func encode(to encoder: Encoder) throws {
        var box = encoder.singleValueContainer()
        switch self {
        case .object(let v): try box.encode(v)
        case .array(let v): try box.encode(v)
        case .string(let v): try box.encode(v)
        case .number(let v): try box.encode(v)
        case .bool(let v): try box.encode(v)
        case .null: try box.encodeNil()
        }
    }
    public subscript(_ key: String) -> JSONValue? { objectValue?[key] }
    public var objectValue: [String: JSONValue]? { if case .object(let v) = self { v } else { nil } }
    public var arrayValue: [JSONValue]? { if case .array(let v) = self { v } else { nil } }
    public var stringValue: String? { if case .string(let v) = self { v } else { nil } }
    public var doubleValue: Double? { if case .number(let v) = self { v } else { nil } }
    public var boolValue: Bool? { if case .bool(let v) = self { v } else { nil } }
    public var scalarDescription: String {
        switch self { case .string(let s): s; case .number(let n): n.rounded() == n ? String(format: "%.0f", n) : String(n); case .bool(let b): String(b); default: prettyPrinted }
    }
    public var prettyPrinted: String {
        let encoder = JSONEncoder(); encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        return (try? String(data: encoder.encode(self), encoding: .utf8)) ?? "null"
    }
    public static func parse(_ text: String) throws -> JSONValue { try JSONDecoder().decode(Self.self, from: Data(text.utf8)) }
}

public struct ToolError: Codable, Sendable {
    public let code: String
    public let message: String
    public let retryable: Bool?
    public let sideEffectState: String?
    public let suggestedAction: String?
    public let details: JSONValue?
    enum CodingKeys: String, CodingKey { case code, message, retryable, details; case sideEffectState = "side_effect_state", suggestedAction = "suggested_action" }
}

public struct ToolEnvelope: Codable, Sendable {
    public let requestID: String
    public let status: String
    public let deviceID: String?
    public let connectionEpoch: JSONValue?
    public let data: JSONValue?
    public let error: ToolError?
    public var isSuccess: Bool { error == nil && ["ok", "success", "complete", "completed"].contains(status) }
    enum CodingKeys: String, CodingKey { case status, data, error; case requestID = "request_id", deviceID = "device_id", connectionEpoch = "connection_epoch" }
}

public struct Capability: Sendable {
    public let status: String
    public let reason: String
    public let limits: JSONValue?
    public init(_ value: JSONValue) { status = value["status"]?.stringValue ?? "unknown"; reason = value["reason"]?.stringValue ?? "No qualification details supplied."; limits = value["limits"] }
}

public struct Device: Identifiable, Sendable {
    public let id: String
    public let name: String
    public let backend: String
    public let nativeID: String
    public let ready: Bool
    public let reasons: [String]
    public let epoch: String
    public let capabilities: [String: Capability]
    public init?(_ value: JSONValue) {
        guard let id = value["device_id"]?.stringValue else { return nil }
        self.id = id; name = value["name"]?.stringValue ?? id; backend = value["backend"]?.stringValue ?? "unknown"
        nativeID = value["native_id"]?.stringValue ?? String(id.split(separator: ":", maxSplits: 1).last ?? "")
        ready = value["ready"]?.boolValue ?? false
        reasons = value["readiness_reasons"]?.arrayValue?.map(\.scalarDescription) ?? []
        epoch = value["connection_epoch"]?.scalarDescription ?? "unknown"
        capabilities = value["capabilities"]?.objectValue?.mapValues(Capability.init) ?? [:]
    }
}

public struct ToolDefinition: Identifiable, Sendable {
    public var id: String { name }
    public let name: String
    public let description: String
    public let schema: JSONValue
    public let capability: String
    public let policy: String
    public let mutation: Bool
    public let deviceBound: Bool
    public init?(_ value: JSONValue) {
        guard let name = value["name"]?.stringValue else { return nil }
        self.name = name; description = value["description"]?.stringValue ?? ""
        schema = value["inputSchema"] ?? .object([:]); capability = value["capability"]?.scalarDescription ?? ""
        policy = value["policy"]?.scalarDescription ?? ""; mutation = value["mutation"]?.boolValue ?? false
        deviceBound = value["device_bound"]?.boolValue ?? true
    }
}
