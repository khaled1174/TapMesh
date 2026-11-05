import SwiftUI
import AppKit
import TapMeshCore

enum Destination: Hashable {
    case device(String), apps, builds, artifacts, activity, approvals, tools
}

struct ContentView: View {
    @ObservedObject var store: WorkspaceStore
    @State private var destination: Destination? = nil
    @Environment(\.scenePhase) private var scenePhase
    var body: some View {
        NavigationSplitView {
            SidebarView(store: store, destination: $destination)
                .navigationSplitViewColumnWidth(min: 210, ideal: 235, max: 300)
        } detail: {
            VStack(spacing: 0) {
                if let issue = store.issue { IssueBanner(message: issue) { store.issue = nil } }
                detail.frame(maxWidth: .infinity, maxHeight: .infinity)
                Divider()
                HStack(spacing: 7) {
                    Circle().fill(store.online ? .green : .orange).frame(width: 6, height: 6)
                    Text(store.connecting ? "Starting local services…" : store.online ? "Connected" : "Offline")
                    Text("·").foregroundStyle(.tertiary)
                    Text(store.lastAction).lineLimit(1)
                    Spacer()
                    if store.busy { ProgressView().controlSize(.mini) }
                    if let receipt = store.controlReceipt {
                        Text("Local control · e\(receipt.connectionEpoch) · g\(receipt.ownershipGeneration) · \(receipt.cancelledRequests) interrupted")
                            .help("Last acknowledged takeover for \(receipt.deviceID). A delivered device action may already have completed.")
                    }
                    Text("Controller \(store.sessionID.prefix(8))").monospaced().help("This window uses a local session distinct from MCP clients.")
                }.font(.caption).foregroundStyle(.secondary).padding(.horizontal, 14).padding(.vertical, 9)
            }
            .background(WindowVisibilityReader { store.setVisibility(visible: $0) }.frame(width: 0, height: 0))
        }
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                Button { Task { await store.takeControl() } } label: { Label(store.takingControl ? "Taking Control…" : "Take Control", systemImage: "hand.raised") }
                    .help("Interrupt active and queued commands for the selected device and take local control")
                    .disabled(!store.online || store.selectedID == nil || store.takingControl)
                if store.recordingID != nil {
                    Button { Task { await store.stopRecording() } } label: { Label(store.stoppingRecording ? "Stopping…" : "Stop Recording", systemImage: "stop.circle.fill") }
                        .tint(.red).help("Stop the recording on its original device, even while another request is pending")
                        .disabled(!store.online || store.stoppingRecording)
                }
                Button { Task { await store.connect() } } label: { Label("Reconnect", systemImage: "arrow.clockwise") }.help("Reconnect services and refresh devices").disabled(store.connecting)
                if !store.approvals.isEmpty {
                    Button { destination = .approvals } label: { Label("\(store.approvals.count) Approvals", systemImage: "hand.raised") }
                }
                SettingsLink { Label("Settings", systemImage: "gearshape") }
            }
        }
        .onChange(of: scenePhase) { _, phase in store.setVisibility(active: phase == .active) }
        .onChange(of: destination) { _, value in
            if case .device(let id) = value {
                Task { await store.select(id); if store.selectedID != id { destination = store.selectedID.map(Destination.device) } }
                store.setVisibility(canvas: true)
            }
            else { store.setVisibility(canvas: false) }
        }
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
        case nil:
            VStack(spacing: 20) {
                Image(systemName: "iphone.gen3.radiowaves.left.and.right").font(.system(size: 56, weight: .light)).foregroundStyle(.tint).accessibilityHidden(true)
                Text("Your devices, within reach").font(.largeTitle.weight(.semibold))
                Text("Select an iPhone, iPad, or simulator in the sidebar.\nTapMesh keeps each controller’s device selection separate.").multilineTextAlignment(.center).foregroundStyle(.secondary)
                Button(store.connecting ? "Starting TapMesh…" : "Discover Devices") { Task { await store.connect() } }.buttonStyle(.borderedProminent).disabled(store.connecting)
                if !store.online {
                    if store.connecting { ProgressView().controlSize(.small) }
                    Text("TapMesh starts its services on this Mac automatically.\nInstall and open Xcode once, then connect and trust your device.\nFor an iPhone or iPad, enable Developer Mode and unlock it.").font(.callout).foregroundStyle(.secondary).multilineTextAlignment(.center)
                }
            }.padding(40)
        }
    }
}

struct SidebarView: View {
    @ObservedObject var store: WorkspaceStore
    @Binding var destination: Destination?
    var body: some View {
        List(selection: $destination) {
            Section("Devices") {
                if store.devices.isEmpty { Text(store.online ? "No devices discovered" : "Connect to discover devices").foregroundStyle(.secondary).font(.callout) }
                ForEach(store.devices) { device in
                    Label {
                        VStack(alignment: .leading, spacing: 3) {
                            Text(device.name).lineLimit(1)
                            Text(device.ready ? (device.backend == "simulator" ? "Simulator · Ready" : "iPhone / iPad · Ready") : "Not ready").font(.caption).foregroundStyle(.secondary)
                        }
                    } icon: { Image(systemName: device.backend == "simulator" ? "iphone.gen3" : "cable.connector").foregroundStyle(device.ready ? Color.accentColor : Color.secondary) }
                    .tag(Destination.device(device.id))
                    .disabled(store.busy)
                    .accessibilityLabel("\(device.name), \(device.backend), \(device.ready ? "ready" : "not ready")")
                }
            }
            Section("Workspace") {
                Label("Apps", systemImage: "square.grid.2x2").tag(Destination.apps)
                Label("Builds", systemImage: "hammer").tag(Destination.builds)
                Label("Artifacts", systemImage: "photo.stack").tag(Destination.artifacts)
                Label("Activity", systemImage: "clock.arrow.circlepath").tag(Destination.activity)
                Label("Approvals", systemImage: "hand.raised").badge(store.approvals.count).tag(Destination.approvals)
            }
            Section("Advanced") { Label("Tool Catalog", systemImage: "curlybraces").tag(Destination.tools) }
        }
        .listStyle(.sidebar)
        .safeAreaInset(edge: .bottom) {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Label(store.online ? "Server available" : "Server unavailable", systemImage: store.online ? "network" : "network.slash").font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    if store.connecting { ProgressView().controlSize(.small) }
                }
                Button { Task { await store.refreshDevices() } } label: { Label("Discover Devices", systemImage: "arrow.triangle.2.circlepath") }.buttonStyle(.borderless).disabled(!store.online || store.busy)
            }.padding(14)
        }
    }
}

struct IssueBanner: View {
    let message: String
    let dismiss: () -> Void
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange).accessibilityHidden(true)
            Text(message).font(.callout).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
            Button(action: dismiss) { Image(systemName: "xmark") }.buttonStyle(.plain).accessibilityLabel("Dismiss message")
        }.padding(12).background(.orange.opacity(0.09))
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
