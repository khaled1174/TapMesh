import Darwin
import Foundation

public enum RuntimeReadinessError: Error { case closed, oversized, readFailed }

public struct RuntimeReadinessLine {
    private var pending = Data()

    public init() {}

    /// Each event must be delivered while the supervisor keeps stdout open.
    /// Foundation's read(upToCount:) can wait for the full requested length on
    /// a pipe; POSIX read returns the bytes currently available instead.
    public mutating func next(from handle: FileHandle, limit: Int = 16_384) throws -> Data {
        var bytes = [UInt8](repeating: 0, count: 4096)
        while true {
            if let newline = pending.firstIndex(of: 10) {
                guard newline - pending.startIndex <= limit else { throw RuntimeReadinessError.oversized }
                let line = Data(pending[..<newline])
                pending.removeSubrange(pending.startIndex...newline)
                return line
            }
            guard pending.count <= limit else { throw RuntimeReadinessError.oversized }
            let count = Darwin.read(handle.fileDescriptor, &bytes, bytes.count)
            if count < 0 {
                if errno == EINTR { continue }
                throw RuntimeReadinessError.readFailed
            }
            if count == 0 { throw RuntimeReadinessError.closed }
            pending.append(contentsOf: bytes.prefix(count))
        }
    }

    public static func read(from handle: FileHandle, limit: Int = 16_384) throws -> Data {
        var reader = RuntimeReadinessLine()
        return try reader.next(from: handle, limit: limit)
    }
}

public enum RuntimeSupervisorEvent: Decodable, Equatable, Sendable {
    public enum Service: String, Decodable, Sendable { case agent, server }
    case ready(configurationPath: String, instanceID: String, ownsServices: Bool)
    case restarted(Service)
    case failure(code: String)

    private enum Status: String, Decodable { case ready, restarted, error }
    private enum Key: String, CodingKey {
        case status, service, code, owner
        case configurationPath = "config_path", instanceID = "instance_id"
    }

    public init(from decoder: any Decoder) throws {
        let fields = try decoder.container(keyedBy: Key.self)
        switch try fields.decode(Status.self, forKey: .status) {
        case .ready:
            let path = try fields.decode(String.self, forKey: .configurationPath)
            guard path.hasPrefix("/"), !path.contains("\0") else {
                throw DecodingError.dataCorruptedError(forKey: .configurationPath, in: fields, debugDescription: "Invalid configuration path")
            }
            let instance = try fields.decode(String.self, forKey: .instanceID)
            guard !instance.isEmpty, instance.utf8.count <= 128 else {
                throw DecodingError.dataCorruptedError(forKey: .instanceID, in: fields, debugDescription: "Invalid runtime instance")
            }
            self = .ready(configurationPath: path, instanceID: instance, ownsServices: try fields.decode(Bool.self, forKey: .owner))
        case .restarted:
            self = .restarted(try fields.decode(Service.self, forKey: .service))
        case .error:
            let code = try fields.decode(String.self, forKey: .code)
            guard !code.isEmpty, code.utf8.count <= 128 else {
                throw DecodingError.dataCorruptedError(forKey: .code, in: fields, debugDescription: "Invalid runtime error code")
            }
            self = .failure(code: code)
        }
    }
}
