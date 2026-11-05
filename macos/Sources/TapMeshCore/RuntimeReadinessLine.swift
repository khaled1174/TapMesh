import Darwin
import Foundation

public enum RuntimeReadinessError: Error { case closed, oversized, readFailed }

public enum RuntimeReadinessLine {
    /// A ready line must be delivered while its supervisor keeps stdout open.
    /// Foundation's read(upToCount:) can wait for the full requested length on
    /// a pipe; POSIX read returns the bytes currently available instead.
    public static func read(from handle: FileHandle, limit: Int = 16_384) throws -> Data {
        var output = Data()
        var bytes = [UInt8](repeating: 0, count: 4096)
        while true {
            let count = Darwin.read(handle.fileDescriptor, &bytes, bytes.count)
            if count < 0 {
                if errno == EINTR { continue }
                throw RuntimeReadinessError.readFailed
            }
            if count == 0 { throw RuntimeReadinessError.closed }
            guard output.count + count <= limit else { throw RuntimeReadinessError.oversized }
            output.append(contentsOf: bytes.prefix(count))
            if let newline = output.firstIndex(of: 10) { return Data(output[..<newline]) }
        }
    }
}
