import SwiftUI
import AppKit
import TapMeshCore

enum Destination: Hashable {
    case device(String), apps, builds, artifacts, activity, approvals, tools
}

struct ContentView: View {
    @ObservedObject var store: WorkspaceStore
    let selectAIAgentsSettings: () -> Void
    @State private var destination: Destination? = nil
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.openSettings) private var openSettings
    var body: some View {
        NavigationSplitView {
            SidebarView(store: store, destination: $destination)
                .navigationSplitViewColumnWidth(min: 210, ideal: 235, max: 300)
        } detail: {
            VStack(spacing: 0) {
                if let issue = store.issue { IssueBanner(message: issue, code: store.issueCode) { store.issue = nil } }
                detail.frame(maxWidth: .infinity, maxHeight: .infinity)
                Divider()
                HStack(spacing: 7) {
                    Circle().fill(store.online ? .green : .orange).frame(width: 6, height: 6)
                    Text(store.connecting ? String(localized: "Starting local services…") : store.online ? String(localized: "Connected") : String(localized: "Offline"))
                    Text("·").foregroundStyle(.tertiary)
                    Text(store.lastAction).lineLimit(1)
                    Spacer()
                    if store.busy { ProgressView().controlSize(.mini) }
                    if let receipt = store.controlReceipt {
                        Text(controlSummary(receipt))
                    }
                }
                .font(.caption).foregroundStyle(.secondary).padding(.horizontal, 14).padding(.vertical, 9)
                .help(statusDetails)
            }
            .background(WindowVisibilityReader { store.setVisibility(visible: $0) }.frame(width: 0, height: 0))
        }
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                Button { Task { await store.takeControl() } } label: { Label(store.takingControl ? String(localized: "Taking Control…") : String(localized: "Take Control"), systemImage: "hand.raised") }
                    .help("Stops anything running or waiting on the selected device, including AI client requests, so you can use it yourself")
                    .disabled(!store.online || store.selectedID == nil || store.takingControl)
                if store.recordingID != nil {
                    Button { Task { await store.stopRecording() } } label: { Label(store.stoppingRecording ? String(localized: "Stopping…") : String(localized: "Stop Recording"), systemImage: "stop.circle.fill") }
                        .tint(.red).help("Stops the screen recording on the device it started on, even while other work is still running")
                        .disabled(!store.online || store.stoppingRecording)
                }
                Button { Task { await store.connect() } } label: { Label("Reconnect", systemImage: "arrow.clockwise") }
                    .help("Restarts the connection to TapMesh on this Mac and looks for devices again")
                    .disabled(store.connecting)
                if !store.approvals.isEmpty {
                    Button { destination = .approvals } label: {
                        Label(store.approvals.count == 1 ? String(localized: "1 Request to Approve") : String(localized: "\(store.approvals.count) Requests to Approve"), systemImage: "checkmark.shield")
                    }
                    .help("Review actions that are waiting for your approval")
                }
                SettingsLink { Label("Settings", systemImage: "gearshape") }.help("Open TapMesh settings")
            }
        }
        .onChange(of: scenePhase) { _, phase in store.setVisibility(active: phase == .active) }
        .onReceive(store.$approvalsPageRequested) { if $0 { destination = .approvals; store.approvalsPageRequested = false } }
        .onChange(of: destination) { _, value in
            if case .device(let id) = value {
                Task { await store.select(id); if store.selectedID != id { destination = store.selectedID.map(Destination.device) } }
                store.setVisibility(canvas: true)
            }
            else { store.setVisibility(canvas: false) }
        }
    }
    /// Always-visible, plain summary of the last time this window took control of the selected device.
    private func controlSummary(_ receipt: TakeoverReceipt) -> String {
        switch receipt.cancelledRequests {
        case 0: return String(localized: "You have control")
        case 1: return String(localized: "You have control · 1 request stopped")
        default: return String(localized: "You have control · \(receipt.cancelledRequests) requests stopped")
        }
    }
    /// Technical values for debugging, shown only in the status bar tooltip.
    private var statusDetails: String {
        var lines = [String(localized: "This window’s session: \(store.sessionID) (separate from AI clients)")]
        if let receipt = store.controlReceipt {
            // Technical counters stay plain digits (passed as text) so they match logs and bug reports.
            let epoch = String(receipt.connectionEpoch), generation = String(receipt.ownershipGeneration), interrupted = String(receipt.cancelledRequests)
            lines.append(String(localized: "Last take control on \(receipt.deviceID): connection epoch \(epoch), ownership generation \(generation), \(interrupted) interrupted."))
            lines.append(String(localized: "A device action that was already delivered may have completed."))
        }
        return lines.joined(separator: "\n")
    }
    @ViewBuilder private var detail: some View {
        switch destination {
        case .device: DeviceWorkspaceView(store: store)
        case .apps: AppsView(store: store)
        case .builds: BuildsView(store: store)
        case .artifacts: ArtifactsView(store: store)
        case .activity: ActivityView(store: store)
        case .approvals: ApprovalsView(store: store)
        case .tools: ToolExplorerView(store: store)
        case nil where store.online && store.xcodeMissing:
            SetupGuideView(issue: .xcodeMissing, onRecheck: { await store.refreshDevices() }, actionsDisabled: store.busy)
        case nil:
            VStack(spacing: 20) {
                Image(systemName: "iphone.gen3.radiowaves.left.and.right").font(.system(size: 56, weight: .light)).foregroundStyle(.tint).accessibilityHidden(true)
                Text("Your devices, within reach").font(.largeTitle.weight(.semibold))
                Text("Select an iPhone, iPad, or simulator in the sidebar.\nAI clients choose their own devices, so they never change your selection.").multilineTextAlignment(.center).foregroundStyle(.secondary)
                if store.online {
                    Button("Discover Devices") { Task { await store.refreshDevices() } }
                        .buttonStyle(.borderedProminent)
                        .disabled(store.connecting || store.busy)
                        .help("Looks again for connected iPhones, iPads and simulators")
                } else {
                    Button(store.connecting ? String(localized: "Starting TapMesh…") : String(localized: "Reconnect")) { Task { await store.connect() } }
                        .buttonStyle(.borderedProminent)
                        .disabled(store.connecting)
                        .help("Starts TapMesh on this Mac and looks for devices")
                }
                if !store.online {
                    if store.connecting { ProgressView().controlSize(.small) }
                    Text("TapMesh starts its services on this Mac automatically.\nInstall and open Xcode once, then connect and trust your device.\nFor an iPhone or iPad, enable Developer Mode and unlock it.").font(.callout).foregroundStyle(.secondary).multilineTextAlignment(.center)
                }
                Button("Connect an AI Agent…") {
                    selectAIAgentsSettings()
                    openSettings()
                }
                .buttonStyle(.link)
            }.padding(40)
        }
    }
}

struct SidebarView: View {
    @ObservedObject var store: WorkspaceStore
    @Binding var destination: Destination?
    @AppStorage("sidebar.otherSimulatorsExpanded") private var otherSimulatorsExpanded = false

    var body: some View {
        List(selection: $destination) {
            if store.devices.isEmpty {
                Section("Devices") {
                    Text(store.online ? String(localized: "No devices discovered") : String(localized: "Connect to discover devices"))
                        .foregroundStyle(.secondary).font(.callout)
                }
            }
            ForEach(DeviceGrouping.sections(store.devices), id: \.section) { group in
                if group.section == .otherSimulators {
                    Section(isExpanded: $otherSimulatorsExpanded) {
                        deviceRows(group.devices)
                    } header: {
                        Text(group.section.sidebarTitle)
                    }
                } else {
                    Section {
                        deviceRows(group.devices)
                    } header: {
                        Text(group.section.sidebarTitle)
                    }
                }
            }
            // Pages that act on the current device are grouped under a header that names it.
            Section {
                Label("Apps", systemImage: "square.grid.2x2").tag(Destination.apps)
                    .accessibilityHint(currentDeviceHint)
                Label("Builds", systemImage: "hammer").tag(Destination.builds)
                    .accessibilityHint(currentDeviceHint)
            } header: {
                Text(store.selectedDevice.map { String(localized: "On \($0.name)") } ?? String(localized: "On Current Device"))
                    .lineLimit(1).truncationMode(.tail)
                    .help(store.selectedDevice.map { String(localized: "Apps and Builds act on \($0.name)") } ?? String(localized: "Choose a device above. Apps and Builds act on the current device."))
            }
            Section("Across Devices") {
                Label("Saved Files", systemImage: "photo.stack").tag(Destination.artifacts)
                Label("Activity", systemImage: "clock.arrow.circlepath").tag(Destination.activity)
                Label("Approvals", systemImage: "checkmark.shield").badge(store.approvals.count).tag(Destination.approvals)
            }
            Section("Advanced") { Label("Tool Catalog", systemImage: "curlybraces").tag(Destination.tools) }
        }
        .listStyle(.sidebar)
        .safeAreaInset(edge: .bottom) {
            HStack {
                Button { Task { await store.refreshDevices() } } label: { Label("Discover Devices", systemImage: "arrow.triangle.2.circlepath") }
                    .buttonStyle(.borderless)
                    .disabled(!store.online || store.busy)
                    .help("Looks again for connected iPhones, iPads and simulators")
                Spacer()
            }.padding(14)
        }
    }
    private func deviceRows(_ devices: [Device]) -> some View {
        ForEach(devices) { device in
            let subtitle = deviceSubtitle(device)
            let setup = SetupGuidance.issue(for: device)?.title
            let isCurrent = device.id == store.selectedID
            let readiness = device.ready ? String(localized: "ready") : String(localized: "not ready")
            let selection = isCurrent ? String(localized: "current device") : nil
            HStack(spacing: 6) {
                Label {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(verbatim: device.name).lineLimit(1).fontWeight(isCurrent ? .semibold : .regular)
                        Text(verbatim: subtitle).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                        if let setup {
                            Text(verbatim: setup)
                                .font(.caption).foregroundStyle(.secondary)
                                .lineLimit(1).truncationMode(.tail)
                                .help(setup)
                        }
                    }
                } icon: {
                    Image(systemName: device.backend == "simulator" ? "iphone.gen3" : "cable.connector")
                        .foregroundStyle(device.ready ? Color.accentColor : Color.secondary)
                }
                Spacer(minLength: 4)
                if isCurrent {
                    Image(systemName: "checkmark")
                        .font(.callout.weight(.semibold))
                        .foregroundStyle(.secondary)
                        .help("Current device. Apps and Builds act on this device.")
                }
            }
            .tag(Destination.device(device.id))
            .disabled(store.busy)
            .contextMenu {
                if device.backend == "physical" { DeviceAgentControlToggle(store: store, device: device) }
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel([device.name, subtitle, readiness, setup, selection].compactMap { $0 }.joined(separator: ", "))
        }
    }

    private func deviceSubtitle(_ device: Device) -> String {
        let kind = DisplayText.deviceKind(device.backend)
        guard let version = device.osVersion else { return kind }
        if device.backend == "simulator" { return String(localized: "\(kind) · iOS \(version)") }
        return String(localized: "\(kind) · \(version)")
    }

    private var currentDeviceHint: String {
        store.selectedDevice.map { String(localized: "Acts on \($0.name)") } ?? String(localized: "No device chosen yet")
    }
}

private extension DeviceSection {
    var sidebarTitle: LocalizedStringKey {
        switch self {
        case .connected: "Your devices"
        case .needsSetup: "Needs setup"
        case .runningSimulators: "Running simulators"
        case .otherSimulators: "Other simulators"
        }
    }
}

struct IssueBanner: View {
    let message: String
    var code: String? = nil
    let dismiss: () -> Void
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange).accessibilityHidden(true)
            messageView
            Button(action: dismiss) { Image(systemName: "xmark") }.buttonStyle(.plain).accessibilityLabel("Dismiss message")
        }.padding(12).background(.orange.opacity(0.09))
    }

    @ViewBuilder private var messageView: some View {
        let text = Text(message).font(.callout).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
        if let code { text.help(Text(verbatim: code)) }
        else { text }
    }
}

/// Window occlusion changes stop network capture even when the app remains active.
struct WindowVisibilityReader: NSViewRepresentable {
    let change: @MainActor (Bool) -> Void
    func makeNSView(context: Context) -> VisibilityView { VisibilityView(change: change) }
    func updateNSView(_ nsView: VisibilityView, context: Context) { }
    final class VisibilityView: NSView {
        let change: @MainActor (Bool) -> Void
        var observers: [NSObjectProtocol] = []
        init(change: @escaping @MainActor (Bool) -> Void) { self.change = change; super.init(frame: .zero) }
        required init?(coder: NSCoder) { fatalError("init(coder:) is unavailable") }
        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            for observer in observers { NotificationCenter.default.removeObserver(observer) }; observers = []
            for name in [NSWindow.didChangeOcclusionStateNotification, NSWindow.didMiniaturizeNotification, NSWindow.didDeminiaturizeNotification, NSWindow.willCloseNotification] {
                observers.append(NotificationCenter.default.addObserver(forName: name, object: window, queue: .main) { [weak self] notification in
                    let closing = notification.name == NSWindow.willCloseNotification
                    Task { @MainActor [weak self] in
                        guard let self else { return }; if closing { self.change(false) } else { self.report() }
                    }
                })
            }
            report()
        }
        func report() { change(window?.isVisible == true && window?.isMiniaturized == false && window?.occlusionState.contains(.visible) == true) }
    }
}
