import AppKit
import Combine
import TapMeshCore

struct ActivityEntry: Identifiable {
    let id = UUID()
    let date = Date()
    let tool: String
    let status: String
    let message: String
    let requestID: String
}
struct ArtifactEntry: Identifiable {
    let id: String
    let kind: String
    let state: String
    let filename: String
    let metadata: JSONValue
}

@MainActor
final class WorkspaceStore: ObservableObject {
    let sessionID = UUID().uuidString
    @Published var devices: [Device] = []
    @Published var selectedID: String?
    @Published var tools: [ToolDefinition] = []
    @Published var online = false
    @Published var connecting = false
    @Published var issue: String?
    @Published var lastAction = "Choose a device to begin."
    @Published var image: NSImage?
    @Published var observation: Observation?
    @Published var elements: [JSONValue] = []
    @Published var elementObservation: Observation?
    @Published var inspectorMessage = "Inspect the current screen to see accessibility or OCR elements."
    @Published var apps: [JSONValue] = []
    @Published var approvals: [JSONValue] = []
    @Published var approvalIssue: String?
    @Published var activity: [ActivityEntry] = []
    @Published var artifacts: [ArtifactEntry] = []
    @Published var liveEnabled = true
    @Published var previewMessage = "Waiting for a device."
    @Published var busy = false
    @Published private var recording = RecordingState()
    @Published var health: JSONValue?
    @Published var agentHealth: JSONValue?
    @Published var catalogVersion = "Unavailable"
    @Published var lastResult: JSONValue?
    @Published var buildState = "Ready"
    @Published var buildRunning = false
    @Published var cancellationState: String?
    @Published var takingControl = false
    @Published private var updatePreparing = false
    @Published private(set) var connectionFilePath = LocalRuntimeController.shared.configurationURL.path
    @Published private(set) var usingCustomConnection = LocalRuntimeController.shared.usesCustomConnection
    @Published private(set) var grantedBuildFolders: [String] = []
    @Published private(set) var localRestartRequired = false
    @Published private var ownershipByDevice: [String: ManualControlSession] = [:]
    private var client: APIClient?
    private var pollTask: Task<Void, Never>?
    private var monitorTask: Task<Void, Never>?
    private var buildTask: Task<Void, Never>?
    private var windowVisible = false
    private var active = true
    private var canvasVisible = true
    private var selectionRevision = 0
    private var captureInFlight = false
    var selectedDevice: Device? { devices.first { $0.id == selectedID } }
    var endpoint: String { client?.configuration.apiURL.absoluteString ?? "Not configured" }
    var agentEndpoint: String { client?.configuration.agentURL.absoluteString ?? "Not configured" }
    var canControl: Bool { online && selectedDevice?.ready == true && !busy && !takingControl && !updatePreparing }
    var controlReceipt: TakeoverReceipt? { selectedID.flatMap { ownershipByDevice[$0]?.receipt } }
    var recordingID: String? { recording.active?.id }
    var recordingDeviceID: String? { recording.active?.deviceID }
    var stoppingRecording: Bool { recording.stopping != nil }

    func start() async {
        await connect()
        monitorTask?.cancel()
        monitorTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(5))
                guard !Task.isCancelled, let self, self.active, self.windowVisible, !self.updatePreparing else { continue }
                if self.online { await self.refreshDevices(); await self.refreshApprovals() }
                else { await self.connect() }
            }
        }
    }
    func connect() async {
        guard !connecting, !updatePreparing else { return }
        connecting = true; defer { connecting = false }
        online = false; selectionRevision += 1; ownershipByDevice = [:]; invalidateObservation(); updatePolling()
        do {
            lastAction = usingCustomConnection ? "Connecting to the selected local connection…" : "Starting TapMesh local services…"
            let configurationFile = try await LocalRuntimeController.shared.ensureReady()
            connectionFilePath = configurationFile.path
            if !usingCustomConnection { grantedBuildFolders = (try? LocalRuntimeController.shared.allowedBuildFolders()) ?? [] }
            client = APIClient(configuration: try ConnectionConfiguration.load(from: configurationFile))
            guard let client else { return }
            health = try await client.request("health")
            online = true; issue = nil
            lastAction = "Local services ready. Select a device to begin."
            agentHealth = try? await client.request("health", agent: true)
            let catalog = try await client.request("api/tools")
            tools = (catalog["tools"]?.arrayValue ?? []).compactMap(ToolDefinition.init)
            catalogVersion = catalog["version"]?.scalarDescription ?? "Unknown version"
            await refreshDevices(); await refreshApprovals()
            updatePolling()
        } catch { online = false; issue = error.localizedDescription; previewMessage = "Reconnect to start TapMesh local services and discover devices."; lastAction = "Local services unavailable."; updatePolling() }
    }
    func chooseConnectionFile() async {
        guard !connecting, !busy, !buildRunning, recordingID == nil else { return }
        let panel = NSOpenPanel()
        panel.title = "Choose a TapMesh connection file"
        panel.message = "Use an existing local connection. TapMesh will not start or stop the services managed by this profile."
        panel.canChooseFiles = true; panel.canChooseDirectories = false; panel.allowsMultipleSelection = false
        panel.directoryURL = ConnectionConfiguration.legacyFileURL.deletingLastPathComponent()
        guard await panel.begin() == .OK, let file = panel.url else { return }
        do { _ = try ConnectionConfiguration.load(from: file) }
        catch { issue = error.localizedDescription; return }
        await changeConnection(to: file)
    }
    func useBuiltInRuntime() async { await changeConnection(to: nil) }
    func grantBuildFolder() async {
        guard !usingCustomConnection, online, !connecting, !busy, !buildRunning, recordingID == nil else { return }
        let panel = NSOpenPanel()
        panel.title = "Allow a build folder"
        panel.message = "TapMesh may read projects and run approved builds inside this folder. Choose only a folder containing projects you want to build."
        panel.prompt = "Allow Folder"
        panel.canChooseDirectories = true; panel.canChooseFiles = false; panel.allowsMultipleSelection = false
        guard await panel.begin() == .OK, let folder = panel.url else { return }
        do {
            let roots = try LocalRuntimeController.shared.allowedBuildFolders()
            try LocalRuntimeController.shared.setAllowedBuildFolders(roots + [folder.resolvingSymlinksInPath().standardizedFileURL.path])
            grantedBuildFolders = try LocalRuntimeController.shared.allowedBuildFolders()
            localRestartRequired = true; lastAction = "Build folder allowed. Restart local services to apply the change."
        } catch { issue = error.localizedDescription }
    }
    func removeBuildFolder(_ path: String) {
        guard !usingCustomConnection, !connecting, !busy, !buildRunning, recordingID == nil else { return }
        do {
            let roots = try LocalRuntimeController.shared.allowedBuildFolders().filter { $0 != path }
            try LocalRuntimeController.shared.setAllowedBuildFolders(roots)
            grantedBuildFolders = roots; localRestartRequired = true
            lastAction = "Build folder removed. Restart local services to apply the change."
        } catch { issue = error.localizedDescription }
    }
    func restartLocalServices() async {
        guard !usingCustomConnection, !connecting, !busy, !buildRunning, recordingID == nil else { return }
        connecting = true; online = false; client = nil; devices = []; selectedID = nil
        invalidateObservation(); updatePolling(); lastAction = "Restarting local services…"
        do {
            try await LocalRuntimeController.shared.prepareRestart()
            localRestartRequired = false
            connecting = false
            await connect()
        } catch { connecting = false; issue = error.localizedDescription }
    }
    private func changeConnection(to file: URL?) async {
        guard !connecting, !busy, !buildRunning, recordingID == nil, !LocalRuntimeController.shared.usesEnvironmentSelection else { return }
        online = false; client = nil; devices = []; selectedID = nil; health = nil; agentHealth = nil; tools = []; catalogVersion = "Unavailable"
        invalidateObservation(); updatePolling()
        connecting = true
        await LocalRuntimeController.shared.selectCustomConnection(file)
        connectionFilePath = LocalRuntimeController.shared.configurationURL.path
        usingCustomConnection = LocalRuntimeController.shared.usesCustomConnection
        grantedBuildFolders = []; localRestartRequired = false
        connecting = false
        await connect()
    }
    func refreshDevices(includeAll: Bool = true) async {
        guard let client, !updatePreparing else { return }
        do {
            let envelope = try await client.call(sessionID: sessionID, tool: "list_devices", arguments: ["include_all_simulators": .bool(includeAll)])
            guard envelope.error == nil else { issue = envelope.error?.message; return }
            let updated = envelope.data?["devices"]?.arrayValue?.compactMap(Device.init) ?? []
            let old = selectedDevice
            devices = updated
            if let old, let current = selectedDevice, old.epoch != current.epoch || old.ready != current.ready { ownershipByDevice.removeValue(forKey: current.id); invalidateObservation(); updatePolling() }
            if selectedID != nil, selectedDevice == nil { invalidateObservation(); previewMessage = "The selected device disconnected. Reconnect or select another device."; updatePolling() }
        } catch { online = false; issue = error.localizedDescription; invalidateObservation(); updatePolling() }
    }
    func select(_ id: String) async {
        guard id != selectedID, !busy else { return }
        let result = await run("select_device", arguments: ["device_id": .string(id)], bindDevice: false)
        if result?.error == nil, result != nil { selectedID = id; selectionRevision += 1; invalidateObservation(); apps = []; updatePolling() }
    }
    func bootSelected() async {
        guard let device = selectedDevice, device.backend == "simulator" else { return }
        _ = await run("start_simulator", arguments: ["simulator_id": .string(device.nativeID), "select": .bool(true)], bindDevice: false)
        await refreshDevices(); updatePolling()
    }
    func setVisibility(active: Bool? = nil, visible: Bool? = nil, canvas: Bool? = nil) {
        if let active { self.active = active }; if let visible { windowVisible = visible }; if let canvas { canvasVisible = canvas }
        updatePolling()
    }
    func setUpdatePreparation(_ preparing: Bool) {
        updatePreparing = preparing
        updatePolling()
    }
    func updatePolling() {
        pollTask?.cancel(); pollTask = nil
        guard active, windowVisible, canvasVisible, online, liveEnabled, !takingControl, !updatePreparing, selectedDevice?.ready == true else { return }
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                await self.capture()
                try? await Task.sleep(for: .seconds(1))
            }
        }
    }
    private func invalidateElements() {
        elements = []; elementObservation = nil
        inspectorMessage = "The screen snapshot was cleared. Inspect the current screen to refresh its elements."
    }
    func invalidateObservation() { observation = nil; image = nil; invalidateElements() }
    func capture() async {
        guard let client, let device = selectedDevice, device.ready, online, !takingControl, !captureInFlight, !updatePreparing else { return }
        captureInFlight = true; defer { captureInFlight = false }
        let revision = selectionRevision
        let generation = ownershipByDevice[device.id]?.receipt?.ownershipGeneration
        do {
            let arguments: [String: JSONValue] = ["device_id": .string(device.id), "format": .string("jpeg"), "max_width": .number(1280), "quality": .number(0.8), "require_fresh": .bool(true)]
            var result = try await client.call(sessionID: sessionID, tool: "take_screenshot", arguments: arguments)
            if result.error?.code == "stale_observation", result.error?.sideEffectState == "not_sent", !Task.isCancelled {
                // Apple's lazy device transport can activate during the first
                // read. Refresh its identity, then request one new frame. This
                // path never resends a tap, key, text, or other mutation.
                await refreshDevices()
                guard !Task.isCancelled, revision == selectionRevision, selectedID == device.id else { return }
                result = try await client.call(sessionID: sessionID, tool: "take_screenshot", arguments: arguments)
            }
            if result.error == nil, let data = result.data, let captured = Observation(data), captured.epoch != selectedDevice?.epoch {
                await refreshDevices()
            }
            guard !Task.isCancelled, revision == selectionRevision, selectedID == device.id else { return }
            if let error = result.error { previewMessage = "\(error.message) (\(error.code))"; image = nil; observation = nil; return }
            guard let currentDevice = selectedDevice, currentDevice.id == device.id,
                  let data = result.data, let observation = Observation(data), observation.deviceID == device.id, observation.epoch == currentDevice.epoch,
                  let encoded = data["image"]?["data"]?.stringValue, encoded.utf8.count <= 12_000_000, let bytes = Data(base64Encoded: encoded), let image = NSImage(data: bytes) else {
                previewMessage = "The service returned an image without valid device geometry or connection identity."; self.observation = nil; self.image = nil; return
            }
            self.observation = observation; self.image = image
            if var control = ownershipByDevice[device.id], let epoch = observation.epoch {
                control.observedFreshScreen(deviceID: device.id, epoch: epoch, generation: generation)
                ownershipByDevice[device.id] = control
            }
            previewMessage = observation.geometry == nil ? "This frame has no usable transform. Capture again before clicking." : "Click the screen to tap. Preview updates at most once per second."
        } catch { if !Task.isCancelled { online = false; issue = error.localizedDescription; invalidateObservation(); updatePolling() } }
    }
    @discardableResult
    func run(_ tool: String, arguments: [String: JSONValue] = [:], bindDevice: Bool = true) async -> ToolEnvelope? {
        guard let client, online else { issue = "Connect to TapMesh before running a tool."; return nil }
        guard !busy, !updatePreparing else { return nil }
        var arguments = arguments
        if bindDevice { guard let selectedID else { issue = "Select a device before running this action."; return nil }; arguments["device_id"] = .string(selectedID) }
        busy = true; defer { busy = false }
        guard let definition = tools.first(where: { $0.name == tool }) else { issue = "Reload the service tool catalog before running this action."; return nil }
        var authorizedControl: TakeoverReceipt?
        do {
            let targetID = arguments["device_id"]?.stringValue ?? selectedID
            let epoch = devices.first { $0.id == targetID }?.epoch
            if let plan = try ManualControlPlan.make(tool: definition, arguments: arguments, selectedDeviceID: selectedID, epoch: epoch) {
                guard let receipt = await takeControl(deviceID: plan.deviceID) else { return nil }
                if plan.requiresFreshInput(after: receipt) || ownershipByDevice[plan.deviceID]?.rejects(plan) == true {
                    await refreshDevices(); await capture()
                    issue = "Control changed on \(plan.deviceID). The previous screen action was discarded. Inspect the fresh screen and repeat the action; no old click, element reference, or focused-field text was replayed."
                    return nil
                }
                authorizedControl = receipt
            }
        } catch { issue = error.localizedDescription; return nil }
        return await dispatch(tool, arguments: arguments, client: client, authorizedControl: authorizedControl)
    }
    @discardableResult
    func takeControl(deviceID: String? = nil) async -> TakeoverReceipt? {
        guard let client, online else { issue = "Connect to TapMesh before taking control."; return nil }
        guard let target = deviceID ?? selectedID else { issue = ManualControlError.missingTarget.localizedDescription; return nil }
        guard !takingControl else { return nil }
        takingControl = true; selectionRevision += 1; updatePolling()
        defer { takingControl = false; updatePolling() }
        do {
            let receipt = try await client.takeover(sessionID: sessionID, deviceID: target)
            let knownEpoch = devices.first { $0.id == target }?.epoch
            let epochChanged = knownEpoch != receipt.epoch
            var control = ownershipByDevice[target] ?? ManualControlSession()
            control.tookControl(receipt, knownEpoch: knownEpoch)
            if control.needsFreshObservation {
                invalidateObservation()
                previewMessage = "Control changed. Capture a fresh screen before sending screen input."
            }
            if epochChanged { await refreshDevices() }
            ownershipByDevice[target] = control
            let message = "Control confirmed for \(target) · connection \(receipt.connectionEpoch) · generation \(receipt.ownershipGeneration) · \(receipt.cancelledRequests) interrupted requests. Delivered device actions may already have completed."
            lastAction = message
            activity.insert(ActivityEntry(tool: "take_control", status: "success", message: message, requestID: ""), at: 0)
            if activity.count > 300 { activity.removeLast(activity.count - 300) }
            return receipt
        } catch {
            issue = error.localizedDescription
            activity.insert(ActivityEntry(tool: "take_control", status: "error", message: error.localizedDescription, requestID: ""), at: 0)
            return nil
        }
    }
    /// Dispatch performs the same approval, reporting, and artifact handling for
    /// both ordinary work and the independently available recording stop action.
    private func dispatch(_ tool: String, arguments: [String: JSONValue], client: APIClient, authorizedControl: TakeoverReceipt? = nil) async -> ToolEnvelope? {
        func ownershipStillCurrent() -> Bool {
            guard let expected = authorizedControl else { return true }
            guard !takingControl, ownershipByDevice[expected.deviceID]?.receipt == expected else {
                issue = "Local control changed before this action was sent. Review the current screen and repeat the action."
                return false
            }
            return true
        }
        guard ownershipStillCurrent() else { return nil }
        do {
            var response = try await client.call(sessionID: sessionID, tool: tool, arguments: arguments)
            // An explicit local UI action authorizes only its immediate, server-bound request.
            // External/MCP requests and human-only handoffs always remain in the approvals view.
            if response.error?.code == "approval_required", response.error?.sideEffectState == "not_sent",
               let approvalID = response.error?.details?["approval_id"]?.stringValue,
               approvalID.allSatisfy({ $0.isLetter || $0.isNumber || $0 == "-" || $0 == "_" }) {
                guard ownershipStillCurrent() else { return nil }
                let pending = try await client.request("api/approvals", approvals: true)["approvals"]?.arrayValue ?? []
                if let approval = pending.first(where: { $0["id"]?.stringValue == approvalID }),
                   LocalApprovalBinding.matches(response: response, record: approval, sessionID: sessionID, tool: tool) {
                    guard ownershipStillCurrent() else { return nil }
                    _ = try await client.request("api/approvals/\(approvalID)", method: "POST", body: .object(["decision": .string("allow")]), approvals: true)
                    guard ownershipStillCurrent() else { return nil }
                    response = try await client.call(sessionID: sessionID, tool: tool, arguments: arguments)
                }
            }
            record(response, tool: tool)
            if let error = response.error {
                issue = "\(error.message)\n\(error.code) · Side effect: \(error.sideEffectState ?? "unspecified")\(error.suggestedAction.map { "\n" + $0 } ?? "")"
                if error.code.contains("approval") || error.code == "protected_action_requires_human" { await refreshApprovals() }
            } else {
                issue = nil; lastAction = "\(tool.replacingOccurrences(of: "_", with: " ").capitalized) · \(response.status)"
                if tools.first(where: { $0.name == tool })?.mutation == true { observation = nil; invalidateElements() }
            }
            lastResult = response.data
            if let data = response.data { collectArtifacts(in: data) }
            if let details = response.error?.details { collectArtifacts(in: details) }
            return response
        } catch {
            issue = error is CancellationError ? "The local request was cancelled. Inspect the device before retrying; cancellation does not prove a delivered action stopped." : error.localizedDescription
            activity.insert(ActivityEntry(tool: tool, status: "transport error", message: issue ?? "", requestID: ""), at: 0)
            return nil
        }
    }
    private func record(_ envelope: ToolEnvelope, tool: String) {
        activity.insert(ActivityEntry(tool: tool, status: envelope.status, message: envelope.error.map { "\($0.code) · \($0.message) · Side effect: \($0.sideEffectState ?? "unspecified")" } ?? "Request finished. Inspect the result to verify the completion condition.", requestID: envelope.requestID), at: 0)
        if activity.count > 300 { activity.removeLast(activity.count - 300) }
    }
    func tap(_ point: PlanePoint, observation: Observation) async {
        guard let selectedDevice, observation.deviceID == selectedDevice.id, observation.epoch == selectedDevice.epoch else { issue = "The frame belongs to an older connection. Capture again."; return }
        _ = await run("tap", arguments: ["target": .object(["x": .number(point.x), "y": .number(point.y), "coordinate_space": .string("device_points"), "observation_id": .string(observation.id)])])
        await capture()
    }
    func inspect() async {
        if let result = await run("list_elements", arguments: ["source": .string("auto"), "max_elements": .number(200)]), result.error == nil, let data = result.data {
            elements = data["elements"]?.arrayValue ?? []; elementObservation = Observation(data)
            inspectorMessage = "\(elements.count) elements · \(data["truncated"]?.boolValue == true ? "Results truncated" : "Snapshot complete")"
        }
    }
    func tapElement(_ element: JSONValue) async {
        guard let snapshot = elementObservation?.snapshotID, let ref = element["ref"]?.stringValue else { issue = "Refresh the element snapshot before interacting."; return }
        _ = await run("tap", arguments: ["target": .object(["ref": .string(ref), "snapshot_id": .string(snapshot)])]); await capture()
    }
    func refreshApps() async {
        if let result = await run("list_apps", arguments: ["include_system": .bool(false)]), result.error == nil { apps = result.data?["apps"]?.arrayValue ?? result.data?.arrayValue ?? [] }
    }
    func refreshApprovals() async {
        guard let client, online else { return }
        do { approvals = try await client.request("api/approvals", approvals: true)["approvals"]?.arrayValue ?? []; approvalIssue = nil }
        catch { approvalIssue = error.localizedDescription }
    }
    func decide(_ approval: JSONValue, allow: Bool) async {
        guard let client, let id = approval["id"]?.stringValue, id.allSatisfy({ $0.isLetter || $0.isNumber || $0 == "-" || $0 == "_" }) else { return }
        do {
            _ = try await client.request("api/approvals/\(id)", method: "POST", body: .object(["decision": .string(allow ? "allow" : "deny")]), approvals: true)
            await refreshApprovals(); lastAction = allow ? "Approval allowed. Retry the original action if the service requests it." : "Approval denied."
        } catch { issue = error.localizedDescription }
    }
    func toggleRecording() async {
        if recording.active != nil {
            await stopRecording()
        } else {
            guard let deviceID = selectedID else { issue = "Select a device before starting a recording."; return }
            if let result = await run("start_recording", arguments: ["device_id": .string(deviceID), "max_duration_seconds": .number(600)], bindDevice: false), result.error == nil,
               !recording.started(response: result, requestedDeviceID: deviceID) {
                issue = "The recording response did not identify the original device and recording. Check the agent before starting another recording."
            }
        }
    }
    func stopRecording() async {
        guard let client, online else { issue = "Reconnect to TapMesh to stop the recording on its original device."; return }
        guard let session = recording.beginStop() else { return }
        // Do not enter run(_:): an unrelated build or wait can hold busy for minutes.
        // This request never reads selectedID and never releases another request's busy state.
        let result = await dispatch("stop_recording", arguments: session.stopArguments, client: client)
        recording.finishedStop(session, response: result)
    }
    func build(project: String, scheme: String, configuration: String, launch: Bool) {
        guard !buildRunning, !updatePreparing else { return }
        buildRunning = true; buildState = "Waiting for authorization, then building, installing\(launch ? " and launching" : "")…"; cancellationState = nil
        buildTask = Task {
            let result = await run("build_and_run", arguments: ["project_path": .string(project), "scheme": .string(scheme), "configuration": .string(configuration), "launch": .bool(launch), "timeout_ms": .number(1_800_000)])
            buildState = result.map { $0.error?.message ?? "Build request finished: \($0.status). Review stage results below." } ?? "Build request ended. Review activity and device state."
            buildRunning = false
        }
    }
    func cancelBuild() {
        buildTask?.cancel()
        cancellationState = "Cancellation requested through the server and agent. A delivered device action may already have completed; check the result and device before retrying."
    }
    private func collectArtifacts(in value: JSONValue) {
        if let id = value["artifact_id"]?.stringValue, !artifacts.contains(where: { $0.id == id }) {
            let path = value["local_path"]?.stringValue ?? ""
            artifacts.insert(ArtifactEntry(id: id, kind: value["kind"]?.stringValue ?? "artifact", state: value["state"]?.stringValue ?? "unknown", filename: path.isEmpty ? id : URL(fileURLWithPath: path).lastPathComponent, metadata: value), at: 0)
        }
        if let object = value.objectValue { for child in object.values { collectArtifacts(in: child) } }
        if let array = value.arrayValue { for child in array { collectArtifacts(in: child) } }
    }
    func saveArtifact(_ artifact: ArtifactEntry) async {
        guard !busy, !updatePreparing else { return }
        busy = true; defer { busy = false }
        guard let client else { return }
        let panel = NSSavePanel(); panel.nameFieldStringValue = artifact.filename; panel.title = "Save TapMesh Artifact"
        guard await panel.begin() == .OK, let url = panel.url else { return }
        do { try await client.artifact(artifact.id, saveTo: url); lastAction = "Saved \(url.lastPathComponent)." }
        catch { issue = error.localizedDescription }
    }
}
