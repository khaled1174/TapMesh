import AppKit
import Combine
import TapMeshCore

/// One line of the Activity page. Arguments and typed text are never stored here.
struct ActivityEntry: Identifiable {
    let id = UUID()
    let date = Date()
    let tool: String
    let status: String
    /// A plain sentence about what happened; empty for an ordinary success.
    let message: String
    let requestID: String
    /// The service's error code, kept for support; shown quietly.
    var code: String? = nil
    /// Whether the action reached the device, in plain words (`DeliveryNote`). Never dropped when present.
    var delivery: String? = nil
    /// True when the delivery could not be confirmed either way, so the person should check the device.
    var deliveryUncertain = false
    /// Identifiers useful in a bug report (connection, generation…); shown quietly.
    var technical: String? = nil
}

/// The most recent `build_and_run` request. Other actions never replace it; starting a
/// new build clears it, so an earlier result is never shown as the current one.
struct BuildRun: Identifiable {
    enum Phase: Equatable {
        case running
        /// The service answered; see `report`.
        case finished
        /// Cancelled before the service answered. Some steps may already have run.
        case cancelled
        /// No answer for another reason (not connected, transport failure, control changed).
        case noResult
    }
    let id = UUID()
    let startedAt = Date()
    /// The device the build was sent to; updated from the response when the service names it.
    var deviceID: String?
    var deviceName: String?
    var phase: Phase = .running
    var report: BuildReport?
    /// Plain explanation when the build failed or produced no result, including whether the device was changed.
    var summary: String?
    var finishedAt: Date?
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
    @Published private(set) var xcodeMissing = false
    @Published var selectedID: String?
    @Published var tools: [ToolDefinition] = []
    @Published var online = false
    @Published var connecting = false
    @Published var issue: String? {
        didSet { if issueCode != nil { issueCode = nil } }
    }
    @Published private(set) var issueCode: String?
    @Published var lastAction = String(localized: "Choose a device to begin.")
    @Published var image: NSImage?
    @Published var observation: Observation?
    @Published var elements: [JSONValue] = []
    @Published var elementObservation: Observation?
    @Published var inspectorMessage = String(localized: "Inspect the screen to list the buttons, text and other items on it.")
    @Published var apps: [JSONValue] = []
    @Published var approvals: [JSONValue] = []
    @Published var approvalIssue: String?
    /// Set when the person clicked an approval notification; ContentView shows the Approvals page and clears it.
    @Published var approvalsPageRequested = false
    /// System notifications and the Dock badge for waiting approvals.
    let approvalNotifier = ApprovalNotifier()
    private static let legacyFastControlKey = "fastControlEnabled"
    private static let fastControlMigratedKey = "fastControlStoredInService"
    @Published private(set) var fastControl = false
    @Published private(set) var agentDevices: Set<String> = []
    @Published private(set) var controlSettingsReady = false
    @Published private(set) var controlSettingsBusy = false
    @Published private(set) var controlSettingIssue: String?
    @Published private(set) var agentBanner: AgentBanner = .none
    @Published private(set) var agentHoldPending = false
    @Published var activity: [ActivityEntry] = []
    @Published var artifacts: [ArtifactEntry] = []
    @Published private(set) var savedFileUsage: SavedFileUsage?
    @Published var liveEnabled = true
    @Published var previewMessage = String(localized: "Waiting for a device.") {
        didSet { if previewErrorCode != nil { previewErrorCode = nil } }
    }
    @Published private(set) var previewErrorCode: String?
    @Published var busy = false
    @Published private var recording = RecordingState()
    @Published var health: JSONValue?
    @Published var agentHealth: JSONValue?
    @Published var catalogVersion = String(localized: "Unavailable")
    @Published var lastResult: JSONValue?
    @Published var buildState = String(localized: "Ready")
    @Published var buildRunning = false
    @Published var cancellationState: String?
    /// The last build's own result, independent of `lastResult`, which every action replaces.
    @Published private(set) var lastBuild: BuildRun?
    @Published var takingControl = false
    /// The shown picture is out of date (the screen changed, or the last read failed). It stays on
    /// screen, dimmed, until the next picture arrives, but can't be clicked.
    @Published private(set) var previewStale = false
    @Published private var updatePreparing = false
    @Published private(set) var connectionFilePath = LocalRuntimeController.shared.configurationURL.path
    @Published private(set) var usingCustomConnection = LocalRuntimeController.shared.usesCustomConnection
    @Published private(set) var grantedBuildFolders: [String] = []
    @Published private(set) var localRestartRequired = false
    @Published private var ownershipByDevice: [String: ManualControlSession] = [:]
    /// The live video stream for the selected device while one runs (see `updateLiveVideo`).
    /// Until its first frame is decoded the last picture stays on screen.
    @Published private(set) var liveVideo: LiveVideoSession?
    /// The running stream's last FORMAT: its crop is the same area as the screenshots.
    @Published private(set) var liveFormat: LiveStreamFormat?
    /// Decoded video is on screen in place of the pictures. Pictures keep coming, slowly, only
    /// to keep an observation for clicks and the inspector.
    @Published private(set) var liveVideoShowing = false
    private var livePolicy = LivePreviewPolicy()
    private var liveVideoID: UUID?
    private var liveRetryTask: Task<Void, Never>?
    /// A stopped stream's connection is still closing; a new one waits for it.
    private var liveClosing = false
    private var client: APIClient?
    private var pollTask: Task<Void, Never>?
    private var agentActivityTask: Task<Void, Never>?
    private struct AgentPollTarget: Equatable {
        let connection: Int
        let selection: Int
        let deviceID: String
    }
    private var agentPollTarget: AgentPollTarget?
    private var holdRevision = 0
    private var controlSettingsRevision = 0
    private var monitorTask: Task<Void, Never>?
    private var buildTask: Task<Void, Never>?
    private var windowVisible = false
    private var active = true
    private var canvasVisible = true
    private var connectionRevision = 0
    private var storageRevision = 0
    private var runtimeRefreshPending = false
    private var automaticRecoveryPaused = false
    private var selectionRevision = 0
    private var captureInFlight = false
    /// A picture was asked for while another read was running; read again when it finishes.
    private var captureAgain = false
    private var foreignActivity = ForeignActivityTracker()
    var selectedDevice: Device? { devices.first { $0.id == selectedID } }
    var endpoint: String { client?.configuration.apiURL.absoluteString ?? String(localized: "Not configured") }
    var agentEndpoint: String { client?.configuration.agentURL.absoluteString ?? String(localized: "Not configured") }
    var canControl: Bool {
        online && !connecting && selectedDevice?.ready == true && !busy && !takingControl && !updatePreparing
            && (usingCustomConnection || controlSettingsReady)
    }
    var canChangeAgentControl: Bool { online && !connecting && controlSettingsReady && !controlSettingsBusy && !updatePreparing }
    var controlReceipt: TakeoverReceipt? { selectedID.flatMap { ownershipByDevice[$0]?.receipt } }
    var recordingID: String? { recording.active?.id }
    var recordingDeviceID: String? { recording.active?.deviceID }
    var stoppingRecording: Bool { recording.stopping != nil }

    init() {
        approvalNotifier.openApprovals = { [weak self] in self?.approvalsPageRequested = true }
        LocalRuntimeController.shared.onServiceChange = { [weak self] change in self?.runtimeChanged(change) }
        // The center keeps block observers for the life of the process; nothing needs to hold the token.
        _ = NotificationCenter.default.addObserver(forName: NSApplication.willTerminateNotification, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.stopLiveVideo() }
        }
    }
    func start() async {
        await connect()
        monitorTask?.cancel()
        monitorTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(5))
                guard !Task.isCancelled, let self, !self.updatePreparing, !self.connecting else { continue }
                if self.runtimeRefreshPending || !self.online {
                    if !self.automaticRecoveryPaused { await self.connect(keepingPreview: true) }
                    continue
                }
                if self.controlSettingsReady { await self.refreshControlSettings() }
                else { await self.syncControlSetting() }
                if self.active, self.windowVisible { await self.refreshDevices() }
                await self.refreshApprovals(); await self.checkForeignActivity()
            }
        }
    }
    private func runtimeChanged(_ change: LocalRuntimeChange) {
        guard !usingCustomConnection else { return }
        connectionRevision += 1; selectionRevision += 1; ownershipByDevice = [:]
        resetControlSettings()
        online = false
        markPreviewStale()
        stopLiveVideo()
        livePolicy.serviceRestarted()
        updatePolling()
        switch change {
        case .restarted:
            runtimeRefreshPending = true
            Task { [weak self] in await self?.connect(keepingPreview: true) }
        case .unavailable(let error):
            automaticRecoveryPaused = true
            issue = error.localizedDescription
        }
    }

    func connect(keepingPreview: Bool = false) async {
        guard !connecting, !updatePreparing else { return }
        if !keepingPreview { automaticRecoveryPaused = false }
        connecting = true; defer { connecting = false }
        connectionRevision += 1; selectionRevision += 1; ownershipByDevice = [:]
        resetControlSettings()
        let revision = connectionRevision
        online = false
        if keepingPreview || runtimeRefreshPending { markPreviewStale() } else { invalidateObservation() }
        runtimeRefreshPending = false
        updatePolling()
        do {
            lastAction = usingCustomConnection ? String(localized: "Connecting to your chosen connection…") : String(localized: "Starting TapMesh services…")
            let configurationFile = try await LocalRuntimeController.shared.ensureReady()
            guard revision == connectionRevision else { return }
            connectionFilePath = configurationFile.path
            if !usingCustomConnection { grantedBuildFolders = (try? LocalRuntimeController.shared.allowedBuildFolders()) ?? [] }
            client = APIClient(configuration: try LocalRuntimeController.shared.loadConfiguration(from: configurationFile))
            guard let client else { return }
            let currentHealth = try await client.request("health", timeout: 5)
            guard revision == connectionRevision else { return }
            health = currentHealth
            issue = nil
            await syncControlSetting()
            guard revision == connectionRevision else { return }
            online = true
            lastAction = String(localized: "TapMesh is ready. Choose a device to begin.")
            let currentAgentHealth = try? await client.request("health", timeout: 5, agent: true)
            let catalog = try await client.request("api/tools")
            guard revision == connectionRevision else { return }
            agentHealth = currentAgentHealth
            tools = (catalog["tools"]?.arrayValue ?? []).compactMap(ToolDefinition.init)
            catalogVersion = catalog["version"]?.scalarDescription ?? String(localized: "Unknown version")
            await refreshDevices(); await refreshApprovals()
            updatePolling()
        } catch {
            guard revision == connectionRevision else { return }
            if let runtimeError = error as? LocalRuntimeError, case .serviceExited = runtimeError { automaticRecoveryPaused = true }
            online = false; issue = error.localizedDescription; previewMessage = String(localized: "TapMesh couldn't connect to its services. Click Reconnect to try again."); lastAction = String(localized: "Not connected to TapMesh services."); updatePolling()
        }
    }
    func chooseConnectionFile() async {
        guard !connecting, !busy, !buildRunning, recordingID == nil else { return }
        let panel = NSOpenPanel()
        panel.title = String(localized: "Choose a TapMesh connection file")
        panel.message = String(localized: "Use an existing local connection. TapMesh will not start or stop the services managed by this profile.")
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
        panel.title = String(localized: "Allow a build folder")
        panel.message = String(localized: "TapMesh may read projects and run approved builds inside this folder. Choose only a folder containing projects you want to build.")
        panel.prompt = String(localized: "Allow Folder")
        panel.canChooseDirectories = true; panel.canChooseFiles = false; panel.allowsMultipleSelection = false
        guard await panel.begin() == .OK, let folder = panel.url else { return }
        do {
            let roots = try LocalRuntimeController.shared.allowedBuildFolders()
            try LocalRuntimeController.shared.setAllowedBuildFolders(roots + [folder.resolvingSymlinksInPath().standardizedFileURL.path])
            grantedBuildFolders = try LocalRuntimeController.shared.allowedBuildFolders()
            localRestartRequired = true; lastAction = String(localized: "Build folder added. Restart local services to apply the change.")
        } catch { issue = error.localizedDescription }
    }
    func removeBuildFolder(_ path: String) {
        guard !usingCustomConnection, !connecting, !busy, !buildRunning, recordingID == nil else { return }
        do {
            let roots = try LocalRuntimeController.shared.allowedBuildFolders().filter { $0 != path }
            try LocalRuntimeController.shared.setAllowedBuildFolders(roots)
            grantedBuildFolders = roots; localRestartRequired = true
            lastAction = String(localized: "Build folder removed. Restart local services to apply the change.")
        } catch { issue = error.localizedDescription }
    }
    func restartLocalServices() async {
        guard !usingCustomConnection, !connecting, !busy, !buildRunning, recordingID == nil else { return }
        connectionRevision += 1
        resetControlSettings()
        connecting = true; online = false; client = nil; devices = []; selectedID = nil; xcodeMissing = false
        invalidateObservation(); updatePolling(); lastAction = String(localized: "Restarting local services…")
        do {
            try await LocalRuntimeController.shared.prepareRestart()
            localRestartRequired = false
            connecting = false
            await connect()
        } catch { connecting = false; issue = error.localizedDescription }
    }
    private func changeConnection(to file: URL?) async {
        guard !connecting, !busy, !buildRunning, recordingID == nil, !LocalRuntimeController.shared.usesEnvironmentSelection else { return }
        connectionRevision += 1
        resetControlSettings()
        online = false; client = nil; devices = []; selectedID = nil; xcodeMissing = false; health = nil; agentHealth = nil; tools = []; catalogVersion = String(localized: "Unavailable")
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
        let revision = connectionRevision
        do {
            let envelope = try await client.call(sessionID: sessionID, tool: "list_devices", arguments: ["include_all_simulators": .bool(includeAll)])
            guard revision == connectionRevision else { return }
            if let error = envelope.error {
                issue = errorSummary(error)
                issueCode = error.code
                return
            }
            let availability = envelope.data?["tools"]?.objectValue?.compactMapValues(\.stringValue) ?? [:]
            xcodeMissing = SetupGuidance.xcodeMissing(tools: availability)
            let updated = envelope.data?["devices"]?.arrayValue?.compactMap(Device.init) ?? []
            let old = selectedDevice
            devices = updated
            if let old, let current = selectedDevice, old.epoch != current.epoch || old.ready != current.ready {
                ownershipByDevice.removeValue(forKey: current.id)
                // A ready device that reconnected keeps its last picture until the next one arrives.
                if current.ready { markPreviewStale() } else { invalidateObservation() }
                updatePolling()
            }
            if selectedID != nil, selectedDevice == nil { invalidateObservation(); previewMessage = old.map { String(localized: "\($0.name) disconnected. Reconnect it or choose another device.") } ?? String(localized: "The selected device disconnected. Reconnect it or choose another device."); updatePolling() }
        } catch {
            guard revision == connectionRevision, !APIClient.isCancellation(error) else { return }
            online = false; issue = error.localizedDescription; markPreviewStale(); updatePolling()
        }
    }
    func select(_ id: String) async {
        guard id != selectedID, !busy else { return }
        let result = await run("select_device", arguments: ["device_id": .string(id)], bindDevice: false)
        if result?.error == nil, result != nil { selectedID = id; selectionRevision += 1; ownershipByDevice[id]?.expire(); invalidateObservation(); apps = []; updatePolling() }
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
    private func resetControlSettings() {
        controlSettingsReady = false
        controlSettingsBusy = false
        controlSettingIssue = nil
        fastControl = false
        agentDevices = []
        agentHoldPending = false
    }
    /// Register this app once per connection. An existing service setting is
    /// kept unless the bundled profile still needs to import a legacy opt-out.
    private func syncControlSetting() async {
        let bundled = !usingCustomConnection && !LocalRuntimeController.shared.usesEnvironmentProfile
        let defaults = UserDefaults.standard
        let legacyOff = ControlSettings.legacyFastControlOverride(
            defaults.object(forKey: Self.legacyFastControlKey) as? Bool,
            bundled: bundled, migrated: defaults.bool(forKey: Self.fastControlMigratedKey)
        )
        var updates: [String: JSONValue] = ["owner_session": .string(sessionID)]
        if let legacyOff { updates["fast_control"] = .bool(legacyOff) }
        await readControlSettings { client, revision in
            let settings = try await self.requestControlSettings(client, updates: updates)
            guard revision == self.connectionRevision, !Task.isCancelled else { throw CancellationError() }
            if bundled { defaults.set(true, forKey: Self.fastControlMigratedKey) }
            return settings
        }
    }
    private func refreshControlSettings() async {
        await readControlSettings(changing: false) { client, _ in try await self.requestControlSettings(client) }
    }
    func setFastControl(_ enabled: Bool) async {
        guard canChangeAgentControl, enabled != fastControl else { return }
        await readControlSettings { client, _ in
            try await self.requestControlSettings(client, updates: ["fast_control": .bool(enabled)])
        }
    }
    func setAgentControl(deviceID: String, allowed: Bool) async {
        guard canChangeAgentControl else { return }
        await readControlSettings { client, revision in
            let latest = try await self.requestControlSettings(client)
            guard revision == self.connectionRevision, !Task.isCancelled else { throw CancellationError() }
            var devices = latest.agentDevices
            if allowed { devices.insert(deviceID) } else { devices.remove(deviceID) }
            guard devices != latest.agentDevices else { return latest }
            return try await self.requestControlSettings(client, updates: ["agent_devices": .array(devices.sorted().map(JSONValue.string))])
        }
    }
    private func requestControlSettings(_ client: APIClient, updates: [String: JSONValue] = [:]) async throws -> ControlSettings {
        let response = try await client.request("api/settings", method: updates.isEmpty ? "GET" : "POST",
                                                body: updates.isEmpty ? nil : .object(updates), approvals: true, timeout: 5)
        guard let settings = ControlSettings(response) else {
            throw ClientError.protocolError(String(localized: "The service didn't confirm the device control setting."))
        }
        return settings
    }
    private func applyControlSettings(_ settings: ControlSettings) {
        if fastControl != settings.fastControl { fastControl = settings.fastControl }
        if agentDevices != settings.agentDevices { agentDevices = settings.agentDevices }
        if !controlSettingsReady { controlSettingsReady = true }
        if controlSettingIssue != nil { controlSettingIssue = nil }
    }
    private func readControlSettings(changing: Bool = true, _ operation: (APIClient, Int) async throws -> ControlSettings) async {
        guard let client, !controlSettingsBusy, !updatePreparing else { return }
        let revision = connectionRevision
        if changing {
            controlSettingsRevision += 1
            controlSettingsBusy = true
        }
        let settingsRevision = controlSettingsRevision
        defer { if changing, revision == connectionRevision { controlSettingsBusy = false } }
        do {
            let settings = try await operation(client, revision)
            guard revision == connectionRevision, settingsRevision == controlSettingsRevision, !Task.isCancelled else { return }
            applyControlSettings(settings)
        } catch {
            guard revision == connectionRevision, settingsRevision == controlSettingsRevision, !APIClient.isCancellation(error) else { return }
            if case ClientError.transport(404) = error {
                controlSettingIssue = String(localized: "The connected services do not support device control settings.")
            } else {
                controlSettingIssue = String(localized: "TapMesh couldn't apply your device control setting to its services. Reconnect to try again.")
            }
        }
    }
    func setDeviceHeld(_ held: Bool, for deviceID: String) async {
        guard let client, online, !connecting, !updatePreparing, !agentHoldPending, deviceID == selectedID else { return }
        let connection = connectionRevision
        let selection = selectionRevision
        holdRevision += 1
        agentHoldPending = true
        defer { if connection == connectionRevision { agentHoldPending = false } }
        do {
            let receipt = try await client.request("api/hold", method: "POST",
                                                   body: .object(["device_id": .string(deviceID), "held": .bool(held)]), approvals: true)
            guard connection == connectionRevision, selection == selectionRevision, deviceID == selectedID else { return }
            guard receipt["device_id"]?.stringValue == deviceID, let confirmed = receipt["held"]?.boolValue else {
                throw ClientError.protocolError(String(localized: "TapMesh couldn't confirm whether AI control is paused. Try again."))
            }
            agentBanner = confirmed ? .paused : .none
        } catch {
            guard connection == connectionRevision, selection == selectionRevision, !APIClient.isCancellation(error) else { return }
            reportIssue(error)
        }
    }
    private func updateAgentActivityPolling() {
        let target: AgentPollTarget?
        if online, windowVisible, !updatePreparing, let device = selectedDevice {
            target = AgentPollTarget(connection: connectionRevision, selection: selectionRevision, deviceID: device.id)
        } else {
            target = nil
        }
        guard target != agentPollTarget else { return }
        agentActivityTask?.cancel()
        agentActivityTask = nil
        agentPollTarget = target
        agentBanner = .none
        guard let target, let client else { return }
        agentActivityTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.refreshAgentActivity(client, for: target)
                try? await Task.sleep(for: .seconds(1))
            }
        }
    }
    private func refreshAgentActivity(_ client: APIClient, for target: AgentPollTarget) async {
        guard !agentHoldPending else { return }
        let revision = holdRevision
        guard let rows = try? await client.request("api/activity", timeout: 3)["devices"]?.arrayValue,
              !Task.isCancelled, target == agentPollTarget, revision == holdRevision, !agentHoldPending else { return }
        let activity = rows.lazy.compactMap(DeviceActivity.init).first { $0.deviceID == target.deviceID }
        let banner = AgentBanner.banner(for: activity)
        if banner != agentBanner { agentBanner = banner }
    }
    func updatePolling() {
        pollTask?.cancel(); pollTask = nil
        updateAgentActivityPolling()
        defer { updateLiveVideo() }
        guard active, windowVisible, canvasVisible, online, liveEnabled, !takingControl, !updatePreparing, selectedDevice?.ready == true else { return }
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                // Every device command shares one channel on the service. While an action
                // runs, a preview capture would only queue in front of it and slow it down.
                // The next frame is asked for shortly after the previous one arrives; after a
                // failed read the loop waits longer so an unavailable screen isn't hammered.
                // While live video plays, a picture every two seconds keeps the observation
                // that clicks and the inspector use.
                var shown = true
                if !self.busy { shown = await self.capture(afterRunningRead: false) }
                try? await Task.sleep(for: shown ? .seconds(self.livePolicy.screenshotInterval) : .seconds(1))
            }
        }
    }
    private static var uptime: Double { ProcessInfo.processInfo.systemUptime }
    /// Runs live video for the selected device whenever the preview is live and shown, the device
    /// is eligible (a ready physical device; the service refuses Wi-Fi ones) and the policy allows
    /// a try. Any failure falls back quietly to pictures, and live is tried again later.
    /// One stream at most: a new one starts only after the previous connection has closed.
    private func updateLiveVideo() {
        let device = selectedDevice
        let wanted = client != nil && active && windowVisible && canvasVisible && online && liveEnabled && !updatePreparing
            && device.map { LivePreviewPolicy.isEligible(backend: $0.backend, ready: $0.ready) } == true
        if let session = liveVideo, !wanted || session.deviceID != device?.id || session.epoch != device?.epoch { stopLiveVideo() }
        livePolicy.select(deviceID: device?.id)
        guard wanted, let device, let client else { liveRetryTask?.cancel(); liveRetryTask = nil; return }
        guard liveVideo == nil, !liveClosing, let delay = livePolicy.delayBeforeStart(at: Self.uptime) else { return }
        liveRetryTask?.cancel(); liveRetryTask = nil
        if delay > 0 {
            liveRetryTask = Task { [weak self] in
                try? await Task.sleep(for: .seconds(delay))
                guard !Task.isCancelled, let self else { return }
                self.liveRetryTask = nil
                self.updateLiveVideo()
            }
            return
        }
        guard let request = try? client.configuration.liveRequest(sessionID: sessionID, deviceID: device.id) else { return }
        let id = UUID()
        let session = LiveVideoSession(request: request, deviceID: device.id, epoch: device.epoch) { [weak self] event in
            self?.liveEvent(event, from: id)
        }
        livePolicy.started(at: Self.uptime)
        liveVideo = session; liveVideoID = id; liveFormat = nil; liveVideoShowing = false
        session.start()
    }
    private func liveEvent(_ event: LiveVideoEvent, from id: UUID) {
        guard id == liveVideoID, liveVideo != nil else { return }
        switch event {
        case .format(let format):
            let previous = liveFormat
            liveFormat = format
            // A new size or orientation, or a picture that doesn't have the video's shape: clicks
            // need a matching picture, so ask for one now rather than at the next slow tick.
            let pictureMatches = observation?.geometry.map { format.matchesAspect(of: $0.image) } ?? false
            if previous != nil || !pictureMatches { Task { await capture() } }
        case .firstFrame:
            livePolicy.firstFrame(at: Self.uptime)
            liveVideoShowing = true
        case .ended:
            // Quietly back to pictures, at their usual pace from now on; live is tried again later.
            livePolicy.failed(at: Self.uptime)
            liveVideo = nil; liveVideoID = nil; liveFormat = nil; liveVideoShowing = false
            updatePolling()
        }
    }
    private func stopLiveVideo() {
        liveRetryTask?.cancel(); liveRetryTask = nil
        guard let session = liveVideo else { return }
        livePolicy.stopped()
        liveVideo = nil; liveVideoID = nil; liveFormat = nil; liveVideoShowing = false
        liveClosing = true
        Task { [weak self] in
            await session.stop()
            guard let self else { return }
            self.liveClosing = false
            self.updateLiveVideo()
        }
    }
    /// After an action: a new picture, unless live video already shows the result and the shown
    /// picture still works for clicks (ordinary input leaves it valid on the service). Actions
    /// that make it out of date (rotation, Home…) mark it stale, so they still get a new one.
    func refreshAfterAction() async {
        if liveVideoShowing, !previewStale { return }
        await capture()
    }
    private func invalidateElements() {
        elements = []; elementObservation = nil
        inspectorMessage = String(localized: "The list was cleared because the screen may have changed. Inspect the screen again to refresh it.")
    }
    /// Clears the preview. Only for a real change of device or connection: the picture no longer
    /// shows the selected device at all.
    func invalidateObservation() { observation = nil; image = nil; previewStale = false; invalidateElements() }
    /// Keeps showing the last picture, but not as something to click, until the next one arrives.
    private func markPreviewStale() { if image != nil { previewStale = true }; invalidateElements() }
    /// Notes device work that other sessions finished, from the service's request history.
    /// Older services without a readable history fall back on `ManualControlSession.idleRetakeInterval`.
    private func checkForeignActivity() async {
        guard let client, online, !updatePreparing else { return }
        guard let rows = try? await client.request("api/history", timeout: 5)["activity"]?.arrayValue else { return }
        foreignActivity.observe(history: rows, mutatingTools: Set(tools.filter(\.mutation).map(\.name)))
    }
    /// Reads one new screen picture. Returns false only when the read failed; a capture that
    /// was skipped (another one already running, no device) or superseded counts as true.
    /// With `afterRunningRead` (after an action), a read already on its way may show the screen
    /// from before that action, so one more read follows it straight away instead of waiting for
    /// the next live frame. The live loop passes false and keeps its pause between frames.
    @discardableResult
    func capture(afterRunningRead: Bool = true) async -> Bool {
        guard client != nil, selectedDevice?.ready == true, online, !takingControl, !updatePreparing else { return true }
        guard !captureInFlight else { if afterRunningRead { captureAgain = true }; return true }
        captureInFlight = true; defer { captureInFlight = false }
        captureAgain = false
        var shown = await captureOnce()
        while captureAgain, !Task.isCancelled {
            captureAgain = false
            shown = await captureOnce()
        }
        return shown
    }
    private func captureOnce() async -> Bool {
        guard let client, let device = selectedDevice, device.ready, online, !takingControl, !updatePreparing else { return true }
        let revision = selectionRevision
        let generation = ownershipByDevice[device.id]?.receipt?.ownershipGeneration
        do {
            let arguments: [String: JSONValue] = ["device_id": .string(device.id), "format": .string("jpeg"), "max_width": .number(1280), "quality": .number(0.8), "require_fresh": .bool(true)]
            var result = try await client.call(sessionID: sessionID, tool: "take_screenshot", arguments: arguments)
            if result.error?.code == "stale_observation", result.error?.sideEffectState == "not_sent", !Task.isCancelled {
                guard revision == selectionRevision, selectedID == device.id else { return true }
                markPreviewStale()
                // The connection or display can change during a read. Refresh and request one new frame.
                await refreshDevices()
                guard !Task.isCancelled, revision == selectionRevision, selectedID == device.id else { return true }
                result = try await client.call(sessionID: sessionID, tool: "take_screenshot", arguments: arguments)
            }
            if result.error == nil, let data = result.data, let captured = Observation(data), captured.epoch != selectedDevice?.epoch {
                await refreshDevices()
            }
            guard !Task.isCancelled, revision == selectionRevision, selectedID == device.id else { return true }
            if let error = result.error {
                previewMessage = DisplayText.serviceError(error)
                previewErrorCode = error.code
                markPreviewStale()
                return false
            }
            guard let currentDevice = selectedDevice, currentDevice.id == device.id,
                  let data = result.data, let observation = Observation(data), observation.deviceID == device.id, observation.epoch == currentDevice.epoch,
                  let encoded = data["image"]?["data"]?.stringValue, encoded.utf8.count <= 12_000_000, let bytes = Data(base64Encoded: encoded), let image = NSImage(data: bytes) else {
                previewMessage = String(localized: "The screen image couldn't be used because it didn't match this device's current connection. Refresh the screen to try again."); markPreviewStale(); return false
            }
            self.observation = observation; self.image = image; previewStale = false
            if var control = ownershipByDevice[device.id], let epoch = observation.epoch {
                control.observedFreshScreen(deviceID: device.id, epoch: epoch, generation: generation)
                ownershipByDevice[device.id] = control
            }
            previewMessage = observation.geometry == nil ? String(localized: "This screen image can't be used for tapping. Refresh the screen before clicking.") : String(localized: "Click the screen to tap. The preview updates continuously.")
            return true
        } catch {
            if !Task.isCancelled, revision == selectionRevision {
                online = false; issue = error.localizedDescription; markPreviewStale(); updatePolling()
            }
            return false
        }
    }
    @discardableResult
    func run(_ tool: String, arguments: [String: JSONValue] = [:], bindDevice: Bool = true) async -> ToolEnvelope? {
        await perform(tool, arguments: arguments, bindDevice: bindDevice, announce: true).envelope
    }
    /// One request's result: the service's answer when there was one, and a plain sentence
    /// saying why the request didn't work when it didn't.
    private struct Outcome {
        var envelope: ToolEnvelope?
        var problem: String?
    }
    /// `run(_:)` with the reason for a failure returned to the caller. With `announce: false`
    /// a problem is only returned and never shown in the global banner (`issue`); the
    /// Activity page still records the request exactly as for any other action.
    private func perform(_ tool: String, arguments: [String: JSONValue], bindDevice: Bool, announce: Bool) async -> Outcome {
        func failed(_ message: String) -> Outcome { if announce { issue = message }; return Outcome(problem: message) }
        guard let client, online else { return failed(String(localized: "TapMesh isn't connected, so nothing was sent. Reconnect, then try again.")) }
        // Never shown in the banner: the action that is running reports for itself.
        guard !busy, !connecting, !updatePreparing else { return Outcome(problem: String(localized: "Another action is running, so nothing was sent. Try again when it finishes.")) }
        var arguments = arguments
        if bindDevice { guard let selectedID else { return failed(String(localized: "Choose a device first. Nothing was sent.")) }; arguments["device_id"] = .string(selectedID) }
        busy = true; defer { busy = false }
        guard let definition = tools.first(where: { $0.name == tool }) else { return failed(String(localized: "TapMesh doesn't recognize this action, so nothing was sent. Reconnect to reload the list of actions, then try again.")) }
        if definition.mutation, !usingCustomConnection, !controlSettingsReady {
            return failed(controlSettingIssue ?? String(localized: "The service didn't confirm the device control setting."))
        }
        var authorizedControl: TakeoverReceipt?
        do {
            let targetID = arguments["device_id"]?.stringValue ?? selectedID
            let epoch = devices.first { $0.id == targetID }?.epoch
            if let plan = try ManualControlPlan.make(tool: definition, arguments: arguments, selectedDeviceID: selectedID, epoch: epoch) {
                if foreignActivity.takeForeignActivity() {
                    for id in Array(ownershipByDevice.keys) { ownershipByDevice[id]?.otherSessionMayHaveActed() }
                }
                // The person's own recent control is reused: taking it again for every tap or swipe
                // would only add a round trip. It is taken again on the first action, after the
                // connection changed, after another session acted, or after a pause.
                var receipt = ownershipByDevice[plan.deviceID].flatMap { $0.needsTakeover(for: plan) ? nil : $0.receipt }
                var tookControlNow = false
                if receipt == nil {
                    // acquireControl reports its own failure in the banner.
                    guard let taken = await acquireControl(of: plan.deviceID, explicit: false) else { return Outcome(problem: String(localized: "TapMesh couldn't take control of the device, so nothing was sent.")) }
                    receipt = taken; tookControlNow = true
                }
                if let receipt, (tookControlNow && plan.requiresFreshInput(after: receipt)) || ownershipByDevice[plan.deviceID]?.rejects(plan) == true {
                    await refreshDevices(); await capture()
                    let deviceName = devices.first { $0.id == plan.deviceID }?.name ?? plan.deviceID
                    return failed(String(localized: "Your action wasn't sent because control of \(deviceName) changed. Check the updated screen and do it again."))
                }
                authorizedControl = receipt
            }
        } catch { return failed(error.localizedDescription) }
        let outcome = await dispatch(tool, arguments: arguments, client: client, authorizedControl: authorizedControl, announce: announce)
        if let authorizedControl, ownershipByDevice[authorizedControl.deviceID]?.receipt == authorizedControl {
            ownershipByDevice[authorizedControl.deviceID]?.acted()
        }
        return outcome
    }
    /// The Take Control button: always asks the service, and always reports the result.
    @discardableResult
    func takeControl(deviceID: String? = nil) async -> TakeoverReceipt? {
        await acquireControl(of: deviceID, explicit: true)
    }
    /// Takes control of a device. A takeover that stopped nothing on an unchanged connection
    /// leaves the preview, its live updates and Activity alone; only an `explicit` request
    /// (the Take Control button) reports it. When something was stopped or the connection
    /// changed, the shown picture is out of date: reads on their way are discarded and a new
    /// picture is asked for.
    private func acquireControl(of deviceID: String?, explicit: Bool) async -> TakeoverReceipt? {
        guard let client, online else { issue = String(localized: "TapMesh isn't connected. Reconnect, then take control again."); return nil }
        guard let target = deviceID ?? selectedID else { issue = ManualControlError.missingTarget.localizedDescription; return nil }
        guard !takingControl else { return nil }
        // While taking control, new reads wait (capture checks takingControl): the service
        // invalidates pictures that are being taken when control changes.
        takingControl = true
        var restartPreview = false
        // Also restarts live updates if anything stopped them meanwhile (they don't start while taking control).
        defer { takingControl = false; if restartPreview || pollTask == nil { updatePolling() } }
        do {
            let receipt = try await client.takeover(sessionID: sessionID, deviceID: target)
            let knownEpoch = devices.first { $0.id == target }?.epoch
            let epochChanged = knownEpoch != receipt.epoch
            var control = ownershipByDevice[target] ?? ManualControlSession()
            let disruptive = control.tookControl(receipt, knownEpoch: knownEpoch)
            if disruptive, target == selectedID {
                // A read already on its way shows the screen from before the change: discard it.
                selectionRevision += 1; pollTask?.cancel(); pollTask = nil; restartPreview = true
                markPreviewStale()
                previewMessage = String(localized: "Control changed. Refresh the screen before tapping or typing.")
            }
            if epochChanged { await refreshDevices() }
            ownershipByDevice[target] = control
            guard explicit || disruptive else { return receipt }
            let name = devices.first { $0.id == target }?.name ?? target
            let stopped = receipt.cancelledRequests
            switch stopped {
            case 0: lastAction = String(localized: "You now control \(name). No pending actions needed stopping.")
            case 1: lastAction = String(localized: "You now control \(name). 1 pending action was stopped; actions already sent may have completed.")
            default: lastAction = String(localized: "You now control \(name). \(stopped) pending actions were stopped; actions already sent may have completed.")
            }
            let delivery = switch stopped {
            case 0: String(localized: "Actions sent earlier may already have completed.")
            case 1: String(localized: "1 pending action was stopped. Actions already sent may have completed.")
            default: String(localized: "\(stopped) pending actions were stopped. Actions already sent may have completed.")
            }
            activity.insert(ActivityEntry(tool: "take_control", status: "success", message: String(localized: "You took control of \(name)."), requestID: "",
                                          delivery: delivery,
                                          deliveryUncertain: stopped > 0,
                                          technical: String(localized: "Device \(target) · connection \(receipt.connectionEpoch) · generation \(receipt.ownershipGeneration) · \(stopped) interrupted requests")), at: 0)
            if activity.count > 300 { activity.removeLast(activity.count - 300) }
            return receipt
        } catch {
            reportIssue(error)
            activity.insert(ActivityEntry(tool: "take_control", status: "error", message: error.localizedDescription, requestID: ""), at: 0)
            return nil
        }
    }
    /// Dispatch performs the same approval, reporting, and artifact handling for
    /// both ordinary work and the independently available recording stop action.
    /// `announce: false` keeps problems out of the global banner and leaves it unchanged on success.
    private func dispatch(_ tool: String, arguments: [String: JSONValue], client: APIClient, authorizedControl: TakeoverReceipt? = nil, announce: Bool = true) async -> Outcome {
        func failed(_ message: String) -> Outcome { if announce { issue = message }; return Outcome(problem: message) }
        func ownershipStillCurrent() -> Bool {
            guard let expected = authorizedControl else { return true }
            return !takingControl && ownershipByDevice[expected.deviceID]?.receipt == expected
        }
        let controlChanged = String(localized: "Your action wasn't sent because control of the device changed. Check the current screen and do it again.")
        guard ownershipStillCurrent() else { return failed(controlChanged) }
        do {
            var response = try await client.call(sessionID: sessionID, tool: tool, arguments: arguments)
            foreignActivity.recordOwn(response.requestID)
            // An explicit local UI action authorizes only its immediate, server-bound request.
            // External/MCP requests and human-only handoffs always remain in the approvals view.
            if response.error?.code == "approval_required", response.error?.sideEffectState == "not_sent",
               let approvalID = response.error?.details?["approval_id"]?.stringValue,
               approvalID.allSatisfy({ $0.isLetter || $0.isNumber || $0 == "-" || $0 == "_" }) {
                guard ownershipStillCurrent() else { return failed(controlChanged) }
                let pending = try await client.request("api/approvals", approvals: true)["approvals"]?.arrayValue ?? []
                if let approval = pending.first(where: { $0["id"]?.stringValue == approvalID }),
                   LocalApprovalBinding.matches(response: response, record: approval, sessionID: sessionID, tool: tool) {
                    guard ownershipStillCurrent() else { return failed(controlChanged) }
                    _ = try await client.request("api/approvals/\(approvalID)", method: "POST", body: .object(["decision": .string("allow")]), approvals: true)
                    guard ownershipStillCurrent() else { return failed(controlChanged) }
                    response = try await client.call(sessionID: sessionID, tool: tool, arguments: arguments)
                    foreignActivity.recordOwn(response.requestID)
                }
            }
            record(response, tool: tool)
            var problem: String?
            if let error = response.error {
                problem = errorSummary(error)
                if announce {
                    issue = problem
                    issueCode = error.code
                }
                if error.code.contains("approval") || error.code == "protected_action_requires_human" { await refreshApprovals() }
                // The service no longer accepts the shown picture: wait for the next one before input.
                if error.code == "stale_observation", actsOnSelectedDevice(arguments) { markPreviewStale() }
            } else {
                if announce { issue = nil }
                let toolName = DisplayText.toolName(tool)
                if response.isSuccess { lastAction = String(localized: "\(toolName): done.") }
                else if response.status == "partial" { lastAction = String(localized: "\(toolName): only partly done. Check the result.") }
                else { lastAction = String(localized: "\(toolName): finished (\(response.status)).") }
                if let definition = tools.first(where: { $0.name == tool }), definition.mutation {
                    invalidateElements()
                    // Screen input (tap, swipe, typing…) leaves the shown picture usable on the service, so
                    // the person can act again before the next frame arrives (agent/core.py `_execute`).
                    // Other changes (rotation, appearance, apps) need a new picture first; the last one
                    // stays on screen meanwhile.
                    if !definition.capability.hasPrefix("input."), actsOnSelectedDevice(arguments) { markPreviewStale() }
                }
            }
            lastResult = response.data
            if let data = response.data { collectArtifacts(in: data) }
            if let details = response.error?.details { collectArtifacts(in: details) }
            return Outcome(envelope: response, problem: problem)
        } catch {
            // APIClient reports a cancelled URLSession request as CancellationError; the
            // extra checks keep TapMesh's own wording if a cancellation arrives another way.
            let cancelled = APIClient.isCancellation(error) || Task.isCancelled
            let failure: ToolError? = if case ClientError.service(let response) = error { response } else { nil }
            let problem = cancelled
                ? String(localized: "The request was cancelled, but it may already have reached the device. Check the device before retrying.")
                : failure.map(errorSummary) ?? error.localizedDescription
            if announce {
                issue = problem
                issueCode = failure?.code
            }
            // Only actions that change the device carry a delivery warning; a failed read changes nothing.
            let changesDevice = tools.first(where: { $0.name == tool })?.mutation == true
            let delivery = failure?.sideEffectState
            let message = cancelled ? String(localized: "The request was cancelled.") : error.localizedDescription
            activity.insert(ActivityEntry(tool: tool, status: "transport error", message: message, requestID: "", code: failure?.code,
                                          delivery: changesDevice ? DeliveryNote.sentence(for: delivery) : nil,
                                          deliveryUncertain: changesDevice && DeliveryNote.isUncertain(delivery)), at: 0)
            if activity.count > 300 { activity.removeLast(activity.count - 300) }
            return Outcome(problem: problem)
        }
    }
    private func actsOnSelectedDevice(_ arguments: [String: JSONValue]) -> Bool {
        guard let selectedID else { return false }
        return (arguments["device_id"]?.stringValue ?? selectedID) == selectedID
    }
    /// Records the outcome only: never the arguments or any typed text.
    private func record(_ envelope: ToolEnvelope, tool: String) {
        let entry: ActivityEntry
        if let error = envelope.error {
            entry = ActivityEntry(tool: tool, status: envelope.status, message: DisplayText.serviceError(error), requestID: envelope.requestID,
                                  code: error.code, delivery: DeliveryNote.sentence(for: error.sideEffectState), deliveryUncertain: DeliveryNote.isUncertain(error.sideEffectState))
        } else {
            let message: String
            if envelope.isSuccess { message = "" }
            else if envelope.status == "partial" { message = String(localized: "Only partly done. Check the device before continuing.") }
            else { message = String(localized: "Finished with the status “\(DisplayText.toolName(envelope.status))”. Check the device before continuing.") }
            entry = ActivityEntry(tool: tool, status: envelope.status, message: message, requestID: envelope.requestID)
        }
        activity.insert(entry, at: 0)
        if activity.count > 300 { activity.removeLast(activity.count - 300) }
    }
    /// Keep the delivery warning after the recommended next step. The error code appears in the tooltip.
    private func errorSummary(_ error: ToolError) -> String {
        "\(DisplayText.serviceError(error))\n\(DeliveryNote.sentence(for: error.sideEffectState))"
    }
    private func reportIssue(_ error: Error) {
        if case ClientError.service(let failure) = error {
            issue = errorSummary(failure)
            issueCode = failure.code
        } else {
            issue = error.localizedDescription
        }
    }
    func tap(_ point: PlanePoint, observation: Observation) async {
        guard let selectedDevice, observation.deviceID == selectedDevice.id, observation.epoch == selectedDevice.epoch else { issue = String(localized: "That screen image is out of date, so the tap wasn't sent. Refresh the screen and tap again."); return }
        _ = await run("tap", arguments: ["target": .object(["x": .number(point.x), "y": .number(point.y), "coordinate_space": .string("device_points"), "observation_id": .string(observation.id)])])
        await refreshAfterAction()
    }
    func inspect() async {
        if let result = await run("list_elements", arguments: ["source": .string("auto"), "max_elements": .number(200)]), result.error == nil, let data = result.data {
            elements = data["elements"]?.arrayValue ?? []; elementObservation = Observation(data)
            let count = elements.count
            if data["truncated"]?.boolValue == true { inspectorMessage = String(localized: "Showing the first \(count) items. There are more on screen than TapMesh could list.") }
            else if count == 1 { inspectorMessage = String(localized: "Found 1 item on screen.") }
            else { inspectorMessage = String(localized: "Found \(count) items on screen.") }
        }
    }
    func tapElement(_ element: JSONValue) async {
        guard let snapshot = elementObservation?.snapshotID, let ref = element["ref"]?.stringValue else { issue = String(localized: "This list is out of date, so nothing was tapped. Inspect the screen again, then choose the item."); return }
        _ = await run("tap", arguments: ["target": .object(["ref": .string(ref), "snapshot_id": .string(snapshot)])]); await refreshAfterAction()
    }
    /// Lists the development apps on the chosen device. Returns nil when the list was read,
    /// otherwise why it wasn't. With `announce: false` (automatic loading) a problem is only
    /// returned, so the caller can show it in place; the global banner is left as it was.
    @discardableResult
    func refreshApps(announce: Bool = true) async -> String? {
        let outcome = await perform("list_apps", arguments: ["include_system": .bool(false)], bindDevice: true, announce: announce)
        guard let result = outcome.envelope, result.error == nil else { return outcome.problem ?? String(localized: "TapMesh couldn't read the list of apps.") }
        apps = result.data?["apps"]?.arrayValue ?? result.data?.arrayValue ?? []
        return nil
    }
    func refreshApprovals() async {
        guard let client, online else { return }
        let revision = connectionRevision
        do {
            let result = try await client.request("api/approvals", approvals: true)
            guard revision == connectionRevision else { return }
            approvals = result["approvals"]?.arrayValue ?? []; approvalIssue = nil
        } catch {
            guard revision == connectionRevision, !APIClient.isCancellation(error) else { return }
            approvalIssue = error.localizedDescription; return
        }
        announceApprovals()
    }
    /// Notification text carries only a readable action name and a known device name.
    private func announceApprovals() {
        let pending = approvals.compactMap { approval -> PendingApprovalSummary? in
            guard let id = approval["id"]?.stringValue, !id.isEmpty else { return nil }
            let deviceName = approval["device_id"]?.stringValue.flatMap { id in devices.first { $0.id == id }?.name }
            return PendingApprovalSummary(id: id, action: approval["tool"]?.stringValue.map(DisplayText.toolName), deviceName: deviceName,
                                          handoff: approval["policy"]?.stringValue == "U", ownSession: approval["session_id"]?.stringValue == sessionID,
                                          deviceGrant: approval["policy"]?.stringValue == "A")
        }
        // Looking = TapMesh is frontmost with its window showing; the toolbar button covers that case.
        approvalNotifier.update(pending: pending, lookingAtApp: NSApp.isActive && windowVisible)
    }
    func decide(_ approval: JSONValue, allow: Bool) async {
        guard let client, online, !connecting, !updatePreparing, let id = approval["id"]?.stringValue,
              id.allSatisfy({ $0.isLetter || $0.isNumber || $0 == "-" || $0 == "_" }) else { return }
        let revision = connectionRevision
        let deviceGrant = approval["policy"]?.stringValue == "A"
        if deviceGrant {
            guard canChangeAgentControl else { return }
            controlSettingsRevision += 1
            controlSettingsBusy = true
        }
        defer { if deviceGrant, revision == connectionRevision { controlSettingsBusy = false } }
        do {
            _ = try await client.request("api/approvals/\(id)", method: "POST", body: .object(["decision": .string(allow ? "allow" : "deny")]), approvals: true)
            guard revision == connectionRevision else { return }
            await refreshApprovals()
            guard revision == connectionRevision else { return }
            if deviceGrant, allow {
                let settings = try await requestControlSettings(client)
                guard revision == connectionRevision else { return }
                applyControlSettings(settings)
                lastAction = String(localized: "AI control allowed for this device.")
            } else {
                lastAction = allow ? String(localized: "Request approved. If TapMesh asks, run the original action again.") : String(localized: "Request denied.")
            }
        } catch {
            guard revision == connectionRevision, !APIClient.isCancellation(error) else { return }
            reportIssue(error)
        }
    }
    func toggleRecording() async {
        if recording.active != nil {
            await stopRecording()
        } else {
            guard let deviceID = selectedID else { issue = String(localized: "Choose a device before starting a recording."); return }
            if let result = await run("start_recording", arguments: ["device_id": .string(deviceID), "max_duration_seconds": .number(600)], bindDevice: false), result.error == nil,
               !recording.started(response: result, requestedDeviceID: deviceID) {
                issue = String(localized: "A recording may have started, but TapMesh couldn't confirm which device it's on. Check your devices before starting another recording.")
            }
        }
    }
    func stopRecording() async {
        guard let client, online else { issue = String(localized: "TapMesh isn't connected, so the recording can't be stopped yet. Reconnect, then stop it."); return }
        guard let session = recording.beginStop() else { return }
        // Do not enter run(_:): an unrelated build or wait can hold busy for minutes.
        // This request never reads selectedID and never releases another request's busy state.
        let result = await dispatch("stop_recording", arguments: session.stopArguments, client: client).envelope
        recording.finishedStop(session, response: result)
    }
    func build(project: String, scheme: String, configuration: String, launch: Bool) {
        guard !buildRunning, !updatePreparing else { return }
        buildRunning = true; buildState = launch ? String(localized: "Waiting for approval, then building, installing and launching the app…") : String(localized: "Waiting for approval, then building and installing the app…"); cancellationState = nil
        // Replace the previous build's result now, so it can't be mistaken for this one's.
        lastBuild = BuildRun(deviceID: selectedDevice?.id, deviceName: selectedDevice?.name)
        buildTask = Task {
            let result = await run("build_and_run", arguments: ["project_path": .string(project), "scheme": .string(scheme), "configuration": .string(configuration), "launch": .bool(launch), "timeout_ms": .number(1_800_000)])
            buildState = result.map { result in result.error.map(DisplayText.serviceError) ?? (result.isSuccess ? String(localized: "Build finished. See each step below.") : String(localized: "Build finished (\(result.status)). See each step below.")) } ?? (Task.isCancelled ? String(localized: "Build cancelled.") : String(localized: "The build didn't report a result. Check the device and Activity before trying again."))
            finishBuild(result, cancelled: Task.isCancelled)
            buildRunning = false
        }
    }
    private func finishBuild(_ result: ToolEnvelope?, cancelled: Bool) {
        var run = lastBuild ?? BuildRun()
        run.finishedAt = Date()
        if let result {
            // A response that arrives despite a cancel request is still the service's account of what happened.
            run.phase = .finished
            run.report = BuildReport(envelope: result)
            run.summary = result.error.map(errorSummary)
            if let target = result.deviceID, target != run.deviceID {
                run.deviceID = target; run.deviceName = devices.first { $0.id == target }?.name ?? target
            }
        } else if cancelled {
            run.phase = .cancelled
            // Cancelling closes the request; the services treat that as a cancel and stop the build's
            // processes (server/app.py and agent/app.py `run_connected`, agent/process.py `Runner.run`).
            // Steps that already finished are not undone.
            run.summary = String(localized: "The build was cancelled before TapMesh received a result. TapMesh asked its services to stop the build, but steps that had already finished, such as installing the app, aren't undone. Check the device before building again.")
        } else {
            run.phase = .noResult
            run.summary = String(localized: "The build didn't report a result. If it was sent, some steps may already have run, so check the device and Activity before trying again.")
        }
        lastBuild = run
    }
    func cancelBuild() {
        buildTask?.cancel()
        cancellationState = String(localized: "Stopping the build… Steps that already finished, such as installing the app, aren't undone, so check the device before building again.")
    }
    var canClearSavedFiles: Bool { online && !busy && !updatePreparing }

    func refreshSavedFileUsage() async {
        guard online, let client else { savedFileUsage = nil; return }
        storageRevision += 1
        let revision = storageRevision, connection = connectionRevision
        do {
            let result = try await client.request("api/storage", timeout: 5)
            guard revision == storageRevision, connection == connectionRevision, !Task.isCancelled else { return }
            savedFileUsage = SavedFileUsage(result)
        } catch {
            guard revision == storageRevision, connection == connectionRevision, !APIClient.isCancellation(error) else { return }
            savedFileUsage = nil
        }
    }

    func clearSavedFiles() async -> Bool {
        guard canClearSavedFiles, let client else { return false }
        let connection = connectionRevision
        storageRevision += 1
        busy = true
        defer { busy = false }
        do {
            _ = try await client.request("api/storage/clear", method: "POST", approvals: true, timeout: 60)
            guard connection == connectionRevision else { return false }
            storageRevision += 1
            artifacts.removeAll()
            savedFileUsage = .empty
            lastAction = String(localized: "Saved files deleted.")
            return true
        } catch {
            if connection == connectionRevision, !APIClient.isCancellation(error) { reportIssue(error) }
            return false
        }
    }

    private func collectArtifacts(in value: JSONValue) {
        if let id = value["artifact_id"]?.stringValue, value["storage"]?.stringValue != "transfer_failed",
           !artifacts.contains(where: { $0.id == id }) {
            let path = value["local_path"]?.stringValue ?? ""
            artifacts.insert(ArtifactEntry(id: id, kind: value["kind"]?.stringValue ?? "artifact", state: value["state"]?.stringValue ?? "unknown", filename: path.isEmpty ? id : URL(fileURLWithPath: path).lastPathComponent, metadata: value), at: 0)
        }
        if let object = value.objectValue { for child in object.values { collectArtifacts(in: child) } }
        if let array = value.arrayValue { for child in array { collectArtifacts(in: child) } }
    }
    /// Returns where the person saved the file, or nil when nothing was saved.
    @discardableResult
    func saveArtifact(_ artifact: ArtifactEntry) async -> URL? {
        guard !busy, !updatePreparing else { return nil }
        busy = true; defer { busy = false }
        guard let client else { return nil }
        let panel = NSSavePanel(); panel.nameFieldStringValue = artifact.filename; panel.title = String(localized: "Save TapMesh Artifact")
        guard await panel.begin() == .OK, let url = panel.url else { return nil }
        do { try await client.artifact(artifact.id, saveTo: url); lastAction = String(localized: "Saved \(url.lastPathComponent)."); return url }
        catch { issue = error.localizedDescription; return nil }
    }
}
