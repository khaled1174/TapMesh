import AppKit
import Foundation
import TapMeshCore

enum LocalRuntimeError: LocalizedError {
    case installationIncomplete, startupFailed, startupTimedOut, invalidResponse, stopping, updateWorkActive
    case serviceError(String)
    var errorDescription: String? {
        switch self {
        case .installationIncomplete: "The TapMesh installation is incomplete. Install the complete TapMesh app to start its local services."
        case .startupFailed: "TapMesh could not start its local services. Reconnect to retry; local diagnostics are available in the connection folder."
        case .startupTimedOut: "TapMesh local services did not become ready within one minute. Reconnect to retry."
        case .invalidResponse: "TapMesh could not verify its local service configuration. Reinstall the complete app or reconnect."
        case .stopping: "TapMesh is shutting down its local services."
        case .updateWorkActive: "An update is waiting for active device work or recordings to finish."
        case .serviceError(let message): message
        }
    }
}

/// Owns only the supervisor launched by this app. Custom connections never
/// acquire ownership of another installation's server or private NAS tunnel.
@MainActor
final class LocalRuntimeController {
    static let shared = LocalRuntimeController()
    private let preferencesKey = "TapMesh.CustomConnectionFile"
    private var process: Process?
    private var output: Pipe?
    private var readiness: CheckedContinuation<URL, any Error>?
    private var readyURL: URL?
    private var launchID = UUID()
    private var timeoutTask: Task<Void, Never>?
    private var stopping = false
    private var ownsServices = false
    private var updateLeaseID: String?
    private(set) var customConnectionFile: URL?

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
        let child = Process(), pipe = Pipe(), id = UUID()
        launchID = id; readyURL = nil
        child.executableURL = python
        child.arguments = ["-I", "-B", "-m", "tapmesh_runtime", "supervise", "--state-dir", profile.path, "--resources", resources.path, "--parent-pid", String(ProcessInfo.processInfo.processIdentifier)]
        child.standardInput = FileHandle.nullDevice
        child.standardOutput = pipe
        child.standardError = FileHandle.nullDevice
        child.environment = ProcessInfo.processInfo.environment.filter { !$0.key.hasPrefix("PYTHON") && !$0.key.hasPrefix("DYLD_") }
        process = child; output = pipe
        return try await withCheckedThrowingContinuation { continuation in
            readiness = continuation
            let reader = pipe.fileHandleForReading
            // A single background reader assembles one bounded protocol line.
            // Process exit and pipe delivery may arrive in either order.
            DispatchQueue.global(qos: .utility).async { [weak self] in
                do {
                    let line = try RuntimeReadinessLine.read(from: reader)
                    Task { @MainActor in self?.receivedLine(line, launchID: id, expectedProfile: profile) }
                } catch {
                    Task { @MainActor in
                        guard let self, self.launchID == id, self.readiness != nil else { return }
                        self.fail(LocalRuntimeError.startupFailed)
                    }
                }
            }
            child.terminationHandler = { [weak self] _ in
                Task { @MainActor in self?.exited(launchID: id) }
            }
            do {
                try child.run()
                try? pipe.fileHandleForWriting.close()
                timeoutTask = Task { [weak self] in
                    do { try await Task.sleep(for: .seconds(60)) } catch { return }
                    guard let self, self.launchID == id, self.readiness != nil else { return }
                    self.fail(LocalRuntimeError.startupTimedOut)
                }
            } catch { try? pipe.fileHandleForWriting.close(); fail(LocalRuntimeError.startupFailed) }
        }
    }

    private func receivedLine(_ line: Data, launchID id: UUID, expectedProfile: URL) {
        guard id == launchID, readiness != nil else { return }
        guard let object = try? JSONSerialization.jsonObject(with: line) as? [String: Any] else { fail(LocalRuntimeError.invalidResponse); return }
        if object["status"] as? String == "error", let message = object["message"] as? String,
           !message.isEmpty, message.utf8.count <= 512,
           message.unicodeScalars.allSatisfy({ !CharacterSet.controlCharacters.contains($0) }) {
            fail(LocalRuntimeError.serviceError(message)); return
        }
        guard object["status"] as? String == "ready",
              let path = object["config_path"] as? String, path.hasPrefix("/"),
              let instance = object["instance_id"] as? String, !instance.isEmpty, instance.utf8.count <= 128,
              object["owner"] is Bool else { fail(LocalRuntimeError.invalidResponse); return }
        let file = URL(fileURLWithPath: path).standardizedFileURL
        guard file.resolvingSymlinksInPath() == expectedProfile.appendingPathComponent("connection.json").standardizedFileURL.resolvingSymlinksInPath() else { fail(LocalRuntimeError.invalidResponse); return }
        readyURL = file
        ownsServices = object["owner"] as? Bool == true
        timeoutTask?.cancel(); timeoutTask = nil
        let waiting = readiness; readiness = nil
        waiting?.resume(returning: file)
    }

    private func fail(_ error: any Error) {
        timeoutTask?.cancel(); timeoutTask = nil
        readyURL = nil
        if process?.isRunning == true { process?.terminate() }
        let waiting = readiness; readiness = nil
        waiting?.resume(throwing: error)
    }

    private func exited(launchID id: UUID) {
        guard id == launchID else { return }
        process = nil; output = nil
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
            throw LocalRuntimeError.serviceError("Reconnect in this TapMesh instance before updating. Close any other instance that owns these local services.")
        }
        let configuration = try ConnectionConfiguration.load(from: readyURL)
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
            let client = APIClient(configuration: try ConnectionConfiguration.load(from: file))
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
        guard let child = process, child.isRunning else { process = nil; output = nil; return }
        child.terminate()
        // The supervisor finalizes owned operations and reaps its children.
        // Never signal a PID loaded from configuration or kill a reused port.
        for _ in 0..<500 {
            guard child.isRunning else { break }
            try? await Task.sleep(for: .milliseconds(100))
        }
    }
}
