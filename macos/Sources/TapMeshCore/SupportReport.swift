import Darwin
import Foundation

public enum SupportReportError: Error { case unsafeFile, unavailable, oversized, writeFailed }

/// A closed support-report schema. Input dictionaries are never copied wholesale.
public struct SupportReport: Sendable {
    public let value: JSONValue
    private static let maxLogBytes = 1_048_576
    private static let maxReportBytes = 1_048_576
    private static let exceptionTypes: Set<String> = [
        "AssertionError", "RuntimeError", "ValueError", "TypeError", "KeyError", "IndexError",
        "AttributeError", "NameError", "UnboundLocalError", "OSError", "FileNotFoundError",
        "PermissionError", "ConnectionError", "ConnectionRefusedError", "ConnectionResetError",
        "BrokenPipeError", "TimeoutError", "NotImplementedError", "OverflowError", "MemoryError",
        "ImportError", "ModuleNotFoundError", "UnicodeError", "UnicodeDecodeError", "UnicodeEncodeError",
        "JSONDecodeError", "InvalidStateError", "HTTPError", "ConnectError", "ReadError", "WriteError",
        "RemoteProtocolError", "LocalProtocolError", "ReadTimeout", "WriteTimeout", "ConnectTimeout",
        "PoolTimeout", "HTTPStatusError", "SSLError", "ProcessLookupError", "ChildProcessError",
        "RuntimeWarning", "UnexpectedError"
    ]

    public init(appVersion: String, appBuild: String, operatingSystemVersion: String, architecture: String,
                serverHealth: JSONValue?, agentHealth: JSONValue?, counts: [String: Int],
                diagnosticEvents: [JSONValue], diagnosticsAvailable: Bool) {
        let safeCounts = ["devices", "ready_devices", "tools", "pending_approvals", "session_artifacts", "session_activity"]
            .reduce(into: [String: JSONValue]()) { result, key in result[key] = .number(Double(min(100_000, max(0, counts[key] ?? 0)))) }
        let events = diagnosticEvents.suffix(200).compactMap(Self.event)
        value = .object([
            "schema_version": .number(1),
            "app": .object(["name": .string("TapMesh"), "version": .string(Self.version(appVersion)), "build": .string(Self.version(appBuild))]),
            "platform": .object(["os_version": .string(Self.version(operatingSystemVersion)), "architecture": .string(["arm64", "x86_64"].contains(architecture) ? architecture : "unknown")]),
            "services": .object(["server": Self.health(serverHealth), "agent": Self.health(agentHealth)]),
            "counts": .object(safeCounts),
            "diagnostics": .object(["available": .bool(diagnosticsAvailable), "events": .array(events)])
        ])
    }

    private static func matches(_ value: String, _ pattern: String, limit: Int) -> Bool {
        value.utf8.count <= limit && value.range(of: pattern, options: .regularExpression) != nil
    }
    private static func version(_ value: String) -> String {
        matches(value, "\\A[0-9]{1,6}(?:\\.[0-9]{1,6}){0,3}\\z", limit: 27) ? value : "unknown"
    }
    private static func health(_ value: JSONValue?) -> JSONValue {
        let status = value?["status"]?.stringValue ?? "unavailable"
        return .object(["status": .string(["ok", "available", "degraded", "unavailable", "unknown"].contains(status) ? status : "unknown"),
                        "version": .string(version(value?["version"]?.stringValue ?? ""))])
    }
    private static func event(_ value: JSONValue) -> JSONValue? {
        guard value["event"]?.stringValue == "unexpected_backend_exception",
              let identifier = value["diagnostic_id"]?.stringValue,
              matches(identifier, "\\A[0-9a-f]{32}\\z", limit: 32) else { return nil }
        let exception = value["exception_type"]?.stringValue ?? "UnexpectedError"
        let frames = (value["stack"]?.arrayValue ?? []).suffix(8).compactMap { frame -> JSONValue? in
            guard let file = frame["file"]?.stringValue,
                  matches(file, "\\A[A-Za-z_][A-Za-z0-9_.-]{0,63}\\.py\\z", limit: 67),
                  let function = frame["function"]?.stringValue,
                  matches(function, "\\A(?:[A-Za-z_][A-Za-z0-9_]{0,63}|<module>|<lambda>)\\z", limit: 64),
                  let line = frame["line"]?.doubleValue, line.isFinite, line.rounded() == line,
                  line > 0, line <= 10_000_000 else { return nil }
            return .object(["file": .string(file), "line": .number(line), "function": .string(function)])
        }
        return .object(["event": .string("unexpected_backend_exception"), "diagnostic_id": .string(identifier),
                        "exception_type": .string(exceptionTypes.contains(exception) ? exception : "UnexpectedError"), "stack": .array(frames)])
    }

    public func encoded() throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        var data = try encoder.encode(value)
        data.append(10)
        guard data.count <= Self.maxReportBytes else { throw SupportReportError.oversized }
        return data
    }

    /// Read only the three private, fixed-name logs. Arbitrary profile files,
    /// config contents and credential files never enter the reader.
    public static func recentDiagnostics(in directory: URL, limit: Int = 100) throws -> [JSONValue] {
        let parent = try openDirectory(directory, requirePrivate: true)
        defer { Darwin.close(parent) }
        var events: [JSONValue] = []
        for name in ["diagnostics.jsonl.2", "diagnostics.jsonl.1", "diagnostics.jsonl"] {
            let descriptor = openat(parent, name, O_RDONLY | O_NOFOLLOW | O_CLOEXEC)
            if descriptor < 0 {
                if errno == ENOENT { continue }
                throw SupportReportError.unsafeFile
            }
            defer { Darwin.close(descriptor) }
            var info = stat()
            guard fstat(descriptor, &info) == 0, regularPrivate(info) else { throw SupportReportError.unsafeFile }
            guard info.st_size <= maxLogBytes else { throw SupportReportError.oversized }
            var content = Data()
            var bytes = [UInt8](repeating: 0, count: 8192)
            while true {
                let count = Darwin.read(descriptor, &bytes, bytes.count)
                if count < 0 {
                    if errno == EINTR { continue }
                    throw SupportReportError.unavailable
                }
                if count == 0 { break }
                guard content.count + count <= maxLogBytes else { throw SupportReportError.oversized }
                content.append(contentsOf: bytes.prefix(count))
            }
            for line in content.split(separator: 10) where line.count <= 16_384 {
                if let decoded = try? JSONDecoder().decode(JSONValue.self, from: Data(line)), let safe = event(decoded) { events.append(safe) }
            }
            events = Array(events.suffix(min(200, max(0, limit))))
        }
        return events
    }

    /// The save panel chooses the destination; an anchored temporary file and
    /// rename prevent following an existing symlink or exposing a partial file.
    public func write(to file: URL) throws {
        guard file.isFileURL, file.pathExtension.lowercased() == "json", !file.lastPathComponent.isEmpty,
              !file.pathComponents.contains(where: { $0.lowercased().hasSuffix(".app") }) else { throw SupportReportError.unsafeFile }
        let data = try encoded()
        let parent = try Self.openDirectory(file.deletingLastPathComponent(), requirePrivate: false)
        defer { Darwin.close(parent) }
        try Self.validateOutput(file.lastPathComponent, parent: parent)
        let temporary = ".tapmesh-support-\(UUID().uuidString).tmp"
        let descriptor = openat(parent, temporary, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, mode_t(0o600))
        guard descriptor >= 0 else { throw SupportReportError.writeFailed }
        defer { Darwin.close(descriptor); unlinkat(parent, temporary, 0) }
        guard fchmod(descriptor, mode_t(0o600)) == 0 else { throw SupportReportError.writeFailed }
        var offset = 0
        while offset < data.count {
            let count = data.withUnsafeBytes { buffer in Darwin.write(descriptor, buffer.baseAddress!.advanced(by: offset), buffer.count - offset) }
            if count < 0, errno == EINTR { continue }
            guard count > 0 else { throw SupportReportError.writeFailed }
            offset += count
        }
        guard fsync(descriptor) == 0 else { throw SupportReportError.writeFailed }
        try Self.validateOutput(file.lastPathComponent, parent: parent)
        guard renameat(parent, temporary, parent, file.lastPathComponent) == 0 else { throw SupportReportError.writeFailed }
    }

    private static func validateOutput(_ name: String, parent: Int32) throws {
        var info = stat()
        if fstatat(parent, name, &info, AT_SYMLINK_NOFOLLOW) < 0 {
            if errno == ENOENT { return }
            throw SupportReportError.unsafeFile
        }
        guard info.st_mode & mode_t(S_IFMT) == mode_t(S_IFREG), info.st_uid == getuid(), info.st_nlink == 1 else { throw SupportReportError.unsafeFile }
    }
    private static func regularPrivate(_ info: stat) -> Bool {
        info.st_mode & mode_t(S_IFMT) == mode_t(S_IFREG) && info.st_uid == getuid()
            && info.st_nlink == 1 && info.st_mode & mode_t(0o777) == mode_t(0o600)
    }
    private static func openDirectory(_ url: URL, requirePrivate: Bool) throws -> Int32 {
        guard url.isFileURL, url.path.hasPrefix("/"), !url.path.utf8.contains(0) else { throw SupportReportError.unsafeFile }
        var descriptor = Darwin.open("/", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard descriptor >= 0 else { throw SupportReportError.unavailable }
        do {
            var components = url.path.split(separator: "/").map(String.init)
            // Foundation returns Apple's immutable /var and /tmp aliases for
            // standard temporary URLs. Expand only the root-owned, exact OS
            // alias; all user-created symlinks still fail O_NOFOLLOW below.
            if let first = components.first, ["var", "tmp", "etc"].contains(first) {
                var info = stat()
                var target = [CChar](repeating: 0, count: 64)
                let alias = "/" + first
                let length = readlink(alias, &target, target.count)
                guard lstat(alias, &info) == 0, info.st_uid == 0,
                      info.st_mode & mode_t(S_IFMT) == mode_t(S_IFLNK), length > 0,
                      String(bytes: target.prefix(length).map { UInt8(bitPattern: $0) }, encoding: .utf8) == "private/" + first else { throw SupportReportError.unsafeFile }
                components.insert("private", at: 0)
            }
            for component in components {
                guard component != ".", component != ".." else { throw SupportReportError.unsafeFile }
                let next = openat(descriptor, String(component), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
                guard next >= 0 else { throw SupportReportError.unsafeFile }
                Darwin.close(descriptor)
                descriptor = next
            }
            if requirePrivate {
                var info = stat()
                guard fstat(descriptor, &info) == 0, info.st_uid == getuid(), info.st_mode & mode_t(0o777) == mode_t(0o700) else { throw SupportReportError.unsafeFile }
            }
            return descriptor
        } catch { Darwin.close(descriptor); throw error }
    }
}
