import AppKit
import Foundation
import TapMeshCore

enum LocalRuntimeError: LocalizedError {
    case installationIncomplete, startupFailed, startupTimedOut, serviceExited, invalidResponse, stopping, updateWorkActive
    case serviceError(String)

    init(supervisorCode: String) {
        switch supervisorCode {
        case "service_exited": self = .serviceExited
        case "startup_timeout": self = .startupTimedOut
        default: self = .startupFailed
        }
    }

    var errorDescription: String? {
        switch self {
        case .installationIncomplete: String(localized: "The TapMesh installation is incomplete. Install the complete TapMesh app to start its local services.")
        case .startupFailed: String(localized: "TapMesh couldn't start its local services. Quit TapMesh and open it again. If it keeps happening, export a diagnostic report from Settings.")
        case .startupTimedOut: String(localized: "TapMesh services took too long to start. Quit TapMesh and open it again. If it keeps happening, export a diagnostic report from Settings.")
        case .serviceExited: String(localized: "A TapMesh service stopped and couldn't recover. Quit TapMesh and open it again. If it keeps happening, export a diagnostic report from Settings.")
        case .invalidResponse: String(localized: "TapMesh could not verify its local service configuration. Reinstall the complete app or reconnect.")
        case .stopping: String(localized: "TapMesh is shutting down its local services.")
        case .updateWorkActive: String(localized: "An update is waiting for active device work or recordings to finish.")
        case .serviceError(let message): message
        }
    }
}

enum LocalRuntimeChange {
    case restarted
    case unavailable(LocalRuntimeError)
}

/// Owns only the supervisor launched by this app. Custom connections never
/// acquire ownership of another installation's server or private NAS tunnel.
@MainActor
final class LocalRuntimeController {
    static let shared = LocalRuntimeController()
    private let preferencesKey = "TapMesh.CustomConnectionFile"
    private var process: Process?
    private var readiness: CheckedContinuation<URL, any Error>?
    private var readyURL: URL?
    private var launchID = UUID()
    private var timeoutTask: Task<Void, Never>?
    private var stopping = false
    private var ownsServices = false
    private var approvalSecret: String?
    private var updateLeaseID: String?
    private(set) var customConnectionFile: URL?
    var onServiceChange: ((LocalRuntimeChange) -> Void)?

    init() {
        if let path = UserDefaults.standard.string(forKey: preferencesKey), path.hasPrefix("/") {
            customConnectionFile = URL(fileURLWithPath: path)
        }
    }

    var usesEnvironmentConnection: Bool { ProcessInfo.processInfo.environment["TAPMESH_CONNECTION_FILE"] != nil }
    var usesEnvironmentProfile: Bool { ProcessInfo.processInfo.environment["TAPMESH_PROFILE_DIR"] != nil }
    var usesEnvironmentSelection: Bool { usesEnvironmentConnection || usesEnvironmentProfile }
    var usesCustomConnection: Bool { usesEnvironmentConnection || (!usesEnvironmentProfile && customConnectionFile != nil) }
    var configurationURL: URL {
        if !usesEnvironmentSelection, let customConnectionFile { return customConnectionFile }
        return ConnectionConfiguration.fileURL
    }

    func selectCustomConnection(_ file: URL?) async {
        guard !usesEnvironmentSelection else { return }
        await stop()
        stopping = false
        customConnectionFile = file?.standardizedFileURL
        if let file = customConnectionFile { UserDefaults.standard.set(file.path, forKey: preferencesKey) }
        else { UserDefaults.standard.removeObject(forKey: preferencesKey) }
    }

    func ensureReady() async throws -> URL {
        guard !stopping else { throw LocalRuntimeError.stopping }
        if usesCustomConnection {
            if usesEnvironmentConnection { return try ConnectionConfiguration.defaultFileURL() }
            return configurationURL
        }
        if let readyURL, process?.isRunning == true { return readyURL }
        guard readiness == nil, process?.isRunning != true else { throw LocalRuntimeError.startupFailed }
        let profile = try ConnectionConfiguration.localProfileDirectory()
        guard let resources = Bundle.main.resourceURL else { throw LocalRuntimeError.installationIncomplete }
        let python = resources.appendingPathComponent("runtime/bin/python3")
        guard FileManager.default.isExecutableFile(atPath: python.path) else { throw LocalRuntimeError.installationIncomplete }
        let child = Process(), pipe = Pipe(), input = Pipe(), id = UUID()
        var random = SystemRandomNumberGenerator()
        let secret = (0..<32).map { _ in String(format: "%02x", UInt8.random(in: 0...255, using: &random)) }.joined()
        launchID = id; readyURL = nil
        approvalSecret = secret
        child.executableURL = python
        child.arguments = ["-I", "-B", "-m", "tapmesh_runtime", "supervise", "--state-dir", profile.path, "--resources", resources.path, "--parent-pid", String(ProcessInfo.processInfo.processIdentifier), "--approval-secret-stdin"]
        child.standardInput = input.fileHandleForReading
        child.standardOutput = pipe
        child.standardError = FileHandle.nullDevice
        child.environment = ProcessInfo.processInfo.environment.filter { !$0.key.hasPrefix("PYTHON") && !$0.key.hasPrefix("DYLD_") }
        process = child
        return try await withCheckedThrowingContinuation { continuation in
            readiness = continuation
            child.terminationHandler = { [weak self] _ in
                Task { @MainActor in self?.exited(launchID: id) }
            }
            do {
                try input.fileHandleForWriting.write(contentsOf: Data((secret + "\n").utf8))
                try input.fileHandleForWriting.close()
                try child.run()
                try? input.fileHandleForReading.close()
                try? pipe.fileHandleForWriting.close()
                readEvents(from: pipe.fileHandleForReading, launchID: id, profile: profile)
                timeoutTask = Task { [weak self] in
                    do { try await Task.sleep(for: .seconds(60)) } catch { return }
                    guard let self, self.launchID == id, self.readiness != nil else { return }
                    self.fail(LocalRuntimeError.startupTimedOut)
                }
            } catch {
                try? input.fileHandleForWriting.close()
                try? input.fileHandleForReading.close()
                try? pipe.fileHandleForWriting.close()
                try? pipe.fileHandleForReading.close()
                process = nil
                fail(.startupFailed)
            }
        }
    }

    func loadConfiguration(from file: URL) throws -> ConnectionConfiguration {
        let secret = ownsServices && readyURL == file && process?.isRunning == true ? approvalSecret : nil
        return try ConnectionConfiguration.load(from: file, approvalSecret: secret)
    }

    private func readEvents(from handle: FileHandle, launchID id: UUID, profile: URL) {
        let receive: @MainActor @Sendable (Data) -> Bool = { [weak self] line in
            self?.receivedLine(line, launchID: id, expectedProfile: profile) ?? false
        }
        let finish: @MainActor @Sendable (any Error) -> Void = { [weak self] error in
            self?.endedReading(error, launchID: id)
        }
        DispatchQueue.global(qos: .utility).async {
            defer { try? handle.close() }
            var reader = RuntimeReadinessLine()
            do {
                while true {
                    let line = try reader.next(from: handle)
                    // Process one event before reading the next so coalesced records keep their order.
                    let keepReading = DispatchQueue.main.sync { receive(line) }
                    guard keepReading else { return }
                }
            } catch {
                DispatchQueue.main.async { finish(error) }
            }
        }
    }

    private func receivedLine(_ line: Data, launchID id: UUID, expectedProfile: URL) -> Bool {
        guard id == launchID, !stopping, readiness != nil || readyURL != nil else { return false }
        guard let event = try? JSONDecoder().decode(RuntimeSupervisorEvent.self, from: line) else { fail(.invalidResponse); return false }
        switch event {
        case .ready(let path, _, let owner):
            guard let waiting = readiness else { fail(.invalidResponse); return false }
            let file = URL(fileURLWithPath: path).standardizedFileURL
            let expected = expectedProfile.appendingPathComponent("connection.json").standardizedFileURL
            guard file.resolvingSymlinksInPath() == expected.resolvingSymlinksInPath() else { fail(.invalidResponse); return false }
            readyURL = file
            ownsServices = owner
            if !owner { approvalSecret = nil }
            timeoutTask?.cancel(); timeoutTask = nil
            readiness = nil
            waiting.resume(returning: file)
            return owner
        case .restarted:
            guard readiness == nil, ownsServices else { fail(.invalidResponse); return false }
            onServiceChange?(.restarted)
            return true
        case .failure(let code):
            fail(LocalRuntimeError(supervisorCode: code))
            return false
        }
    }

    private func endedReading(_ error: any Error, launchID id: UUID) {
        guard id == launchID, !stopping else { return }
        if case RuntimeReadinessError.oversized = error { fail(.invalidResponse) }
        else if readiness != nil { fail(.startupFailed) }
        else if readyURL != nil { fail(.serviceExited) }
    }

    private func fail(_ error: LocalRuntimeError) {
        timeoutTask?.cancel(); timeoutTask = nil
        let wasReady = readyURL != nil
        readyURL = nil
        approvalSecret = nil
        if process?.isRunning == true { process?.terminate() }
        let waiting = readiness; readiness = nil
        waiting?.resume(throwing: error)
        if wasReady { onServiceChange?(.unavailable(error)) }
    }

    private func exited(launchID id: UUID) {
        guard id == launchID else { return }
        process = nil
        approvalSecret = nil
    }

    func allowedBuildFolders() throws -> [String] {
        let file = try ConnectionConfiguration.localProfileDirectory().appendingPathComponent("permissions.json")
        guard FileManager.default.fileExists(atPath: file.path) else { return [] }
        let values = try file.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey])
        guard values.isRegularFile == true, values.isSymbolicLink != true, (values.fileSize ?? 65_537) <= 65_536,
              let document = try JSONSerialization.jsonObject(with: Data(contentsOf: file)) as? [String: Any],
              Set(document.keys) == ["build_roots"], let roots = document["build_roots"] as? [String], roots.count <= 32,
              roots.allSatisfy({ $0.hasPrefix("/") && !$0.contains("\0") }) else { throw LocalConnectionError.invalidConfiguration }
        return roots
    }

    func setAllowedBuildFolders(_ roots: [String]) throws {
        let uniqueRoots = Array(Set(roots)).sorted()
        guard !usesCustomConnection, uniqueRoots.count <= 32, uniqueRoots.allSatisfy({ $0.hasPrefix("/") && !$0.contains("\0") }) else { throw LocalConnectionError.invalidConfiguration }
        let profile = try ConnectionConfiguration.localProfileDirectory()
        let values = try profile.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
        guard values.isDirectory == true, values.isSymbolicLink != true else { throw LocalConnectionError.invalidConfiguration }
        _ = try allowedBuildFolders()
        let file = profile.appendingPathComponent("permissions.json")
        let document = try JSONSerialization.data(withJSONObject: ["build_roots": uniqueRoots], options: [.prettyPrinted, .sortedKeys])
        try document.write(to: file, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
    }

    func prepareRestart() async throws {
        guard !usesCustomConnection else { return }
        await stop()
        stopping = false
        guard process?.isRunning != true else { throw LocalRuntimeError.stopping }
    }

    func prepareForUpdate() async throws {
        guard !usesCustomConnection else { return }
        guard ownsServices, process?.isRunning == true, let readyURL else {
            throw LocalRuntimeError.serviceError(String(localized: "Reconnect in this TapMesh instance before updating. Close any other instance that owns these local services."))
        }
        let configuration = try loadConfiguration(from: readyURL)
        let client = APIClient(configuration: configuration)
        let leaseID = UUID().uuidString
        updateLeaseID = leaseID
        let result: JSONValue
        do { result = try await client.request("api/update", method: "POST", body: .object(["action": .string("prepare"), "lease_id": .string(leaseID)]), approvals: true, timeout: 12) }
        catch ClientError.operationBusy { throw LocalRuntimeError.updateWorkActive }
        guard result["status"]?.stringValue == "prepared", result["lease_id"]?.stringValue == leaseID,
              let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String else { throw LocalRuntimeError.invalidResponse }
        try ProfileUpdateFence.install(profile: readyURL.deletingLastPathComponent(), leaseID: leaseID, buildNumber: build)
        await stop()
        guard process?.isRunning != true else { throw LocalRuntimeError.stopping }
    }

    func resumeAfterFailedUpdate() async {
        guard !usesCustomConnection, ownsServices, let leaseID = updateLeaseID else { return }
        defer { updateLeaseID = nil }
        do {
            try ProfileUpdateFence.clearOwned(profile: try ConnectionConfiguration.localProfileDirectory(), leaseID: leaseID)
            if stopping { try await prepareRestart() }
            let file = try await ensureReady()
            let client = APIClient(configuration: try loadConfiguration(from: file))
            _ = try await client.request("api/update", method: "POST", body: .object(["action": .string("resume"), "lease_id": .string(leaseID)]), approvals: true, timeout: 12)
        } catch { /* Normal reconnect remains available after an update failure. */ }
    }

    func stop() async {
        guard !stopping else { return }
        stopping = true
        timeoutTask?.cancel(); timeoutTask = nil
        let waiting = readiness; readiness = nil
        waiting?.resume(throwing: LocalRuntimeError.stopping)
        readyURL = nil
        approvalSecret = nil
        guard let child = process, child.isRunning else { process = nil; return }
        child.terminate()
        // The supervisor finalizes owned operations and reaps its children.
        // Never signal a PID loaded from configuration or kill a reused port.
        for _ in 0..<500 {
            guard child.isRunning else { break }
            try? await Task.sleep(for: .milliseconds(100))
        }
    }
}
