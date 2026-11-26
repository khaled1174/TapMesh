import Foundation

/// How the Artifacts page can show a file inline.
public enum ArtifactPreviewKind: Equatable, Sendable {
    case image, video, text, unsupported
}

/// Whether the artifact's file can be fetched at all, read from its `state`.
///
/// The service (agent/artifacts.py `finalize`) only records an artifact once its file is fully
/// written, with state `complete`, or `partial` for a recording that stopped after an error
/// (agent/backends.py). Other words are handled defensively: in-progress words mean the file is
/// still being written; removal words mean it is gone. Anything else is attempted.
public enum ArtifactReadiness: Equatable, Sendable {
    case ready, notReady, unavailable
}

/// Pure decisions behind the Artifacts page preview: which preview to use, whether to download
/// without asking, and how to describe sizes. No I/O.
public enum ArtifactPreviewPolicy {
    /// Files at or below this size are fetched for preview as soon as they are selected.
    public static let automaticLoadLimitBytes: Int64 = 50 * 1024 * 1024
    /// How much of a text file the preview shows.
    public static let textPreviewLimitBytes = 200 * 1024

    private static let imageExtensions: Set<String> = ["png", "jpg", "jpeg", "heic", "heif", "gif", "tif", "tiff", "bmp", "webp"]
    private static let videoExtensions: Set<String> = ["mp4", "mov", "m4v"]
    private static let textExtensions: Set<String> = ["log", "txt", "text", "json", "ips", "crash", "xml", "csv", "md", "yaml", "yml"]

    /// The file extension decides when it is one we know; otherwise the service's `kind`
    /// (`screenshot`, `recording`, `build_log`, `crash`, `app_container`) does.
    public static func previewKind(kind: String, filename: String) -> ArtifactPreviewKind {
        let ext = (filename as NSString).pathExtension.lowercased()
        if imageExtensions.contains(ext) { return .image }
        if videoExtensions.contains(ext) { return .video }
        if textExtensions.contains(ext) { return .text }
        if !ext.isEmpty && ext.count <= 8 && ext.allSatisfy({ $0.isLetter || $0.isNumber }) { return .unsupported }
        let kind = kind.lowercased()
        if kind == "screenshot" || kind.contains("image") { return .image }
        if kind == "recording" || kind.contains("video") { return .video }
        if kind.contains("log") || kind == "crash" || kind.contains("json") || kind.contains("text") { return .text }
        return .unsupported
    }

    public static func readiness(state: String) -> ArtifactReadiness {
        switch state.lowercased() {
        case "pending", "writing", "in_progress", "recording", "running", "started", "processing", "uploading": .notReady
        case "expired", "deleted", "removed", "missing", "unavailable", "failed", "discarded": .unavailable
        default: .ready
        }
    }

    /// True when the preview should be fetched without asking. Large files, and videos whose size
    /// is unknown, wait for the person to choose Load Preview. Files with no inline preview are
    /// never fetched up front.
    public static func loadsAutomatically(_ kind: ArtifactPreviewKind, sizeBytes: Int64?) -> Bool {
        if kind == .unsupported { return false }
        guard let sizeBytes else { return kind != .video }
        return sizeBytes <= automaticLoadLimitBytes
    }

    /// `size_bytes` from the artifact record, when it is a sensible whole number.
    public static func sizeBytes(in metadata: JSONValue) -> Int64? {
        guard let value = metadata["size_bytes"]?.doubleValue, value.isFinite, value >= 0, value < 9e18 else { return nil }
        return Int64(value)
    }

    public static func sizeText(_ bytes: Int64) -> String {
        ByteCountFormatter.string(fromByteCount: max(0, bytes), countStyle: .file)
    }

    /// The readable start of a text file. `data` may hold more than `limit` bytes; only `limit`
    /// are shown and `truncated` says so. A cut through a multi-byte character is trimmed.
    /// Returns nil when the bytes do not look like text (NUL bytes), so binary files are not
    /// shown as garbage.
    public static func textExcerpt(from data: Data, limit: Int = textPreviewLimitBytes) -> (text: String, truncated: Bool)? {
        let truncated = data.count > limit
        var bytes = Data(data.prefix(max(0, limit)))
        if bytes.contains(0) { return nil }
        for _ in 0..<4 {
            if let text = String(data: bytes, encoding: .utf8) { return (text, truncated) }
            guard !bytes.isEmpty, truncated else { break }
            bytes.removeLast()
        }
        return (String(decoding: bytes, as: UTF8.self), truncated)
    }
}

/// Who owns a preview cache folder, as seen by the process doing the cleanup.
public enum ArtifactPreviewCacheOwner: Equatable, Sendable {
    /// No process with that id, or one that is not TapMesh.
    case notRunning
    /// A TapMesh process with that id is running; `startedAt` is when it started, if known.
    case running(startedAt: Date?)
}

/// Naming and cleanup rules for the per-session preview cache folders. No I/O.
///
/// Each session's folder is named `<process id>-<random token>`. A folder is stale, and may be
/// removed by another session, when its name carries no process id (older layouts), when no
/// TapMesh process with that id is running, or when the TapMesh process with that id started
/// after the folder was made (the id was reused, so the folder's own session has ended).
/// A folder of a TapMesh that is still running is never removed, however old it is.
public enum ArtifactPreviewCacheRule {
    public static func sessionFolderName(processID: Int32, token: String) -> String {
        "\(processID)-\(token)"
    }

    public static func processID(fromFolderName name: String) -> Int32? {
        guard let dash = name.firstIndex(of: "-"), dash != name.startIndex,
              name[name.index(after: dash)...].isEmpty == false else { return nil }
        let digits = name[..<dash]
        guard digits.allSatisfy({ $0.isASCII && $0.isNumber }), let pid = Int32(digits), pid > 0 else { return nil }
        return pid
    }

    public static func isStale(folderName: String, createdAt: Date?, owner: (Int32) -> ArtifactPreviewCacheOwner) -> Bool {
        guard let pid = processID(fromFolderName: folderName) else { return true }
        switch owner(pid) {
        case .notRunning:
            return true
        case .running(let startedAt):
            guard let startedAt, let createdAt else { return false }
            // One second of slack for timestamp rounding; a folder is always made after its process starts.
            return createdAt < startedAt.addingTimeInterval(-1)
        }
    }

    /// True when `url` is `folder` or inside it, after resolving `..` and symbolic links
    /// (`/var` and `/private/var` name the same temporary folder).
    public static func isInside(_ url: URL, folder: URL) -> Bool {
        let path = url.standardizedFileURL.resolvingSymlinksInPath().pathComponents
        let root = folder.standardizedFileURL.resolvingSymlinksInPath().pathComponents
        return path.count >= root.count && Array(path.prefix(root.count)) == root
    }
}
