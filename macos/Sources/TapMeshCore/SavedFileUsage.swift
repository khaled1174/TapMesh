import Foundation

public struct SavedFileUsage: Equatable, Sendable {
    public let count: Int
    public let bytes: Int64

    public static let empty = SavedFileUsage(count: 0, bytes: 0)

    private init(count: Int, bytes: Int64) {
        self.count = count
        self.bytes = bytes
    }

    public init?(_ value: JSONValue) {
        guard let rawCount = value["count"]?.doubleValue, let count = Int(exactly: rawCount), count >= 0,
              let rawBytes = value["bytes"]?.doubleValue, let bytes = Int64(exactly: rawBytes), bytes >= 0 else { return nil }
        self.init(count: count, bytes: bytes)
    }

    public var summary: String {
        let size = ArtifactPreviewPolicy.sizeText(bytes)
        return count == 1 ? String(localized: "1 file · \(size)") : String(localized: "\(count) files · \(size)")
    }
}
