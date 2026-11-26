import AppKit
import AVKit
import Foundation
import TapMeshCore

/// Per-session temporary home for preview copies of artifacts:
/// `$TMPDIR/TapMesh/ArtifactPreviews/<process id>-<token>/<artifact id>/<file name>`.
/// This session's folder is removed when TapMesh quits. The first time a session uses the cache it
/// also removes folders left by sessions that have ended (see `ArtifactPreviewCacheRule`), never
/// the folder of another TapMesh that is still running.
/// Previews never go to Documents, Downloads or any folder the person chose.
enum ArtifactPreviewCache {
    static let root = FileManager.default.temporaryDirectory.appendingPathComponent("TapMesh/ArtifactPreviews", isDirectory: true)

    static let directory: URL = {
        let fileManager = FileManager.default
        removeStaleSessions()
        let name = ArtifactPreviewCacheRule.sessionFolderName(processID: getpid(), token: UUID().uuidString)
        let session = root.appendingPathComponent(name, isDirectory: true)
        try? fileManager.createDirectory(at: session, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        // The center keeps block observers for the life of the process; nothing needs to hold the token.
        _ = NotificationCenter.default.addObserver(forName: NSApplication.willTerminateNotification, object: nil, queue: nil) { _ in
            try? FileManager.default.removeItem(at: session)
        }
        return session
    }()

    /// True for the preview cache itself, so a saved location there is never offered as a saved copy.
    static func contains(_ url: URL) -> Bool { ArtifactPreviewCacheRule.isInside(url, folder: root) }

    private static func removeStaleSessions() {
        let fileManager = FileManager.default
        guard let folders = try? fileManager.contentsOfDirectory(at: root, includingPropertiesForKeys: [.creationDateKey], options: []) else { return }
        let ownName = processName(getpid())
        for folder in folders {
            let created = (try? folder.resourceValues(forKeys: [.creationDateKey]))?.creationDate
            let stale = ArtifactPreviewCacheRule.isStale(folderName: folder.lastPathComponent, createdAt: created) { pid in
                guard let info = bsdInfo(pid), let ownName, name(of: info) == ownName else { return .notRunning }
                let started = Date(timeIntervalSince1970: TimeInterval(info.pbi_start_tvsec) + TimeInterval(info.pbi_start_tvusec) / 1_000_000)
                return .running(startedAt: started)
            }
            if stale { try? fileManager.removeItem(at: folder) }
        }
    }

    /// The kernel's record of a process, or nil when there is no such process.
    private static func bsdInfo(_ pid: Int32) -> proc_bsdinfo? {
        var info = proc_bsdinfo()
        let size = Int32(MemoryLayout<proc_bsdinfo>.size)
        return proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &info, size) == size ? info : nil
    }

    private static func processName(_ pid: Int32) -> String? { bsdInfo(pid).map(name(of:)) }

    /// The executable name the kernel keeps for the process (first 16 characters).
    private static func name(of info: proc_bsdinfo) -> String {
        var comm = info.pbi_comm
        return withUnsafeBytes(of: &comm) { String(decoding: $0.prefix { $0 != 0 }, as: UTF8.self) }
    }

    /// The same character rule `APIClient.artifact` enforces, checked before the id becomes a folder name.
    static func isSafeID(_ id: String) -> Bool {
        !id.isEmpty && id.count <= 128 && id.allSatisfy { $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "-" || $0 == "_") }
    }

    static func fileURL(id: String, filename: String) -> URL? {
        guard isSafeID(id) else { return nil }
        let name = (filename as NSString).lastPathComponent
        let safeName = name.isEmpty || name == "." || name == ".." || name.hasPrefix(".") ? id : name
        return directory.appendingPathComponent(id, isDirectory: true).appendingPathComponent(safeName)
    }

    /// A finished copy, if one was fetched earlier this session. `APIClient.artifact` moves the
    /// download into place only once it is complete, so an existing file is a whole file.
    static func existingFile(id: String, filename: String) -> URL? {
        guard let url = fileURL(id: id, filename: filename), FileManager.default.fileExists(atPath: url.path) else { return nil }
        return url
    }

    static func fetch(id: String, filename: String, client: APIClient) async throws -> URL {
        guard let url = fileURL(id: id, filename: filename) else { throw ClientError.protocolError(String(localized: "Invalid artifact identifier.")) }
        if FileManager.default.fileExists(atPath: url.path) { return url }
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        try await client.artifact(id, saveTo: url)
        return url
    }
}

/// Loads the selected artifact's preview on demand. Owned by `ArtifactsView`; it never changes
/// `WorkspaceStore` and never writes to Activity.
@MainActor
final class ArtifactPreviewModel: ObservableObject {
    enum Content {
        case image(NSImage)
        case video(AVPlayer)
        case text(String, truncated: Bool)
        /// Fetched (or not needed) but there is no inline view for this file.
        case none
    }

    enum Phase {
        case empty
        case notReady
        case unavailable
        case offline
        case needsConfirmation(sizeBytes: Int64?)
        case loading
        case failed(String)
        case ready(Content)
    }

    @Published private(set) var phase: Phase = .empty
    @Published private(set) var thumbnails: [String: NSImage] = [:]
    /// Bound to `.quickLookPreview`; setting it opens the system Quick Look panel.
    @Published var quickLookURL: URL?
    @Published private(set) var quickLookLoading = false

    /// Where the person saved each artifact during this session, by artifact id. Kept for the life
    /// of the process (the page's model is recreated when the person switches pages); not persisted.
    private static var sessionSavedLocations: [String: URL] = [:]
    @Published private var savedLocations: [String: URL] = ArtifactPreviewModel.sessionSavedLocations {
        didSet { Self.sessionSavedLocations = savedLocations }
    }

    private var currentID: String?
    private var loadTask: Task<Void, Never>?
    private var quickLookTask: Task<Void, Never>?

    /// Show the preview for `artifact`. `userRequested` skips the size check (Load Preview / Try Again).
    func show(_ artifact: ArtifactEntry?, store: WorkspaceStore, userRequested: Bool = false) {
        if artifact?.id != currentID {
            quickLookTask?.cancel(); quickLookTask = nil; quickLookLoading = false
        }
        loadTask?.cancel(); loadTask = nil
        stopPlayback()
        currentID = artifact?.id
        guard let artifact else { phase = .empty; return }

        switch ArtifactPreviewPolicy.readiness(state: artifact.state) {
        case .notReady: phase = .notReady; return
        case .unavailable: phase = .unavailable; return
        case .ready: break
        }
        let kind = ArtifactPreviewPolicy.previewKind(kind: artifact.kind, filename: artifact.filename)
        if let cached = ArtifactPreviewCache.existingFile(id: artifact.id, filename: artifact.filename) {
            phase = .loading
            loadTask = Task { await present(cached, kind: kind, id: artifact.id) }
            return
        }
        if kind == .unsupported { phase = .ready(.none); return }
        let size = ArtifactPreviewPolicy.sizeBytes(in: artifact.metadata)
        if !userRequested && !ArtifactPreviewPolicy.loadsAutomatically(kind, sizeBytes: size) {
            phase = .needsConfirmation(sizeBytes: size); return
        }
        guard let client = Self.client(for: store) else { phase = .offline; return }
        phase = .loading
        let id = artifact.id, filename = artifact.filename
        loadTask = Task {
            do {
                let url = try await ArtifactPreviewCache.fetch(id: id, filename: filename, client: client)
                guard !Task.isCancelled, currentID == id else { return }
                await present(url, kind: kind, id: id)
            } catch {
                guard !Task.isCancelled, currentID == id else { return }
                phase = .failed(Self.failureSentence(error))
            }
        }
    }

    /// Open the file in Quick Look, fetching it first if this session has no copy yet.
    func quickLook(_ artifact: ArtifactEntry, store: WorkspaceStore) {
        if quickLookURL != nil { quickLookURL = nil; return }
        guard ArtifactPreviewPolicy.readiness(state: artifact.state) == .ready else { return }
        if let cached = ArtifactPreviewCache.existingFile(id: artifact.id, filename: artifact.filename) {
            quickLookURL = cached; return
        }
        guard !quickLookLoading else { return }
        guard let client = Self.client(for: store) else { phase = .offline; return }
        quickLookLoading = true
        let id = artifact.id, filename = artifact.filename
        quickLookTask = Task {
            defer { if currentID == id { quickLookLoading = false } }
            do {
                let url = try await ArtifactPreviewCache.fetch(id: id, filename: filename, client: client)
                guard !Task.isCancelled, currentID == id else { return }
                quickLookURL = url
                // The inline preview can use the copy too, if it was waiting for one.
                switch phase {
                case .needsConfirmation, .failed, .offline: show(artifact, store: store)
                default: break
                }
            } catch {
                guard !Task.isCancelled, currentID == id else { return }
                phase = .failed(Self.failureSentence(error))
            }
        }
    }

    /// Remember where the person saved `id`. Locations inside the preview cache are ignored.
    func recordSaved(_ url: URL, for id: String) {
        guard url.isFileURL, !ArtifactPreviewCache.contains(url) else { return }
        savedLocations[id] = url
    }

    /// The saved copy of `id`, while the file is still there.
    func savedLocation(for id: String) -> URL? {
        guard let url = savedLocations[id], FileManager.default.fileExists(atPath: url.path) else { return nil }
        return url
    }

    /// Forget saved copies that were moved or deleted, so their Show in Finder button goes away.
    func forgetMissingSavedCopies() {
        let missing = savedLocations.filter { !FileManager.default.fileExists(atPath: $0.value.path) }.map(\.key)
        for id in missing { savedLocations[id] = nil }
    }

    /// Select the saved copy in Finder. If it has been moved or deleted, forget it instead.
    func showInFinder(_ id: String) {
        guard let url = savedLocation(for: id) else { savedLocations[id] = nil; return }
        NSWorkspace.shared.activateFileViewerSelecting([url])
    }

    /// Stop work and playback when the page goes away. Fetched copies stay for this session.
    func stop() {
        loadTask?.cancel(); loadTask = nil
        quickLookTask?.cancel(); quickLookTask = nil; quickLookLoading = false
        stopPlayback()
    }

    func clearSavedFilePreviews() async throws {
        let pending = [loadTask, quickLookTask].compactMap { $0 }
        stop()
        currentID = nil
        quickLookURL = nil
        phase = .empty
        for task in pending { await task.value }
        thumbnails.removeAll()
        let folder = ArtifactPreviewCache.directory
        if FileManager.default.fileExists(atPath: folder.path) { try FileManager.default.removeItem(at: folder) }
    }

    private func stopPlayback() {
        if case .ready(.video(let player)) = phase { player.pause() }
    }

    private func present(_ url: URL, kind: ArtifactPreviewKind, id: String) async {
        do {
            let content: Content
            switch kind {
            case .image:
                let data = try await Task.detached(priority: .userInitiated) { try Data(contentsOf: url) }.value
                guard let image = NSImage(data: data) else { throw PreviewError.unreadable }
                thumbnails[id] = image
                content = .image(image)
            case .video:
                // Not started; the person presses play.
                content = .video(AVPlayer(url: url))
            case .text:
                let limit = ArtifactPreviewPolicy.textPreviewLimitBytes
                let data = try await Task.detached(priority: .userInitiated) { () throws -> Data in
                    let handle = try FileHandle(forReadingFrom: url)
                    defer { try? handle.close() }
                    return try handle.read(upToCount: limit + 1) ?? Data()
                }.value
                if let excerpt = ArtifactPreviewPolicy.textExcerpt(from: data, limit: limit) {
                    content = .text(excerpt.text, truncated: excerpt.truncated)
                } else {
                    content = .none
                }
            case .unsupported:
                content = .none
            }
            guard !Task.isCancelled, currentID == id else { return }
            phase = .ready(content)
        } catch {
            guard !Task.isCancelled, currentID == id else { return }
            phase = .failed(String(localized: "TapMesh fetched this file but couldn't open it for preview. Try Quick Look or save a copy instead."))
        }
    }

    private enum PreviewError: Error { case unreadable }

    /// A second client for the same connection file the store loaded. `APIClient` is a stateless
    /// value; the token is read from disk per request, exactly as for the store's own client.
    private static func client(for store: WorkspaceStore) -> APIClient? {
        guard store.online else { return nil }
        let file = URL(fileURLWithPath: store.connectionFilePath)
        guard let configuration = try? ConnectionConfiguration.load(from: file) else { return nil }
        return APIClient(configuration: configuration)
    }

    private static func failureSentence(_ error: Error) -> String {
        if error is URLError { return String(localized: "TapMesh couldn't reach the service to load this preview. Check the connection, then try again.") }
        return String(localized: "This preview couldn't be loaded. The file may no longer be available on the service.")
    }
}
