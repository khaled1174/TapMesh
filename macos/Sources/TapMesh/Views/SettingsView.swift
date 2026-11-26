import SwiftUI
import AppKit
import TapMeshCore

enum SettingsTab: Hashable {
    case general, aiAgents, buildFolders, advanced, diagnostics, about
}

struct SettingsView: View {
    @ObservedObject var store: WorkspaceStore
    @Binding var selection: SettingsTab
    /// The same conditions that disabled these controls before the settings were split into tabs.
    private var connectionLocked: Bool { store.connecting || store.busy || store.buildRunning || store.recordingID != nil }

    var body: some View {
        TabView(selection: $selection) {
            general.tabItem { Label("General", systemImage: "gearshape") }.tag(SettingsTab.general)
            AgentSetupView().tabItem { Label("AI Agents", systemImage: "terminal") }.tag(SettingsTab.aiAgents)
            buildFolders.tabItem { Label("Build Folders", systemImage: "folder") }.tag(SettingsTab.buildFolders)
            advanced.tabItem { Label("Advanced", systemImage: "slider.horizontal.3") }.tag(SettingsTab.advanced)
            diagnostics.tabItem { Label("Diagnostics", systemImage: "stethoscope") }.tag(SettingsTab.diagnostics)
            about.tabItem { Label("About", systemImage: "info.circle") }.tag(SettingsTab.about)
        }.padding(12).frame(width: 600, height: 520)
    }

    // MARK: General

    private var general: some View {
        Form {
            Section("Connection") {
                LabeledContent("Status", value: store.connecting ? String(localized: "Starting…") : store.online ? String(localized: "Connected") : String(localized: "Not connected"))
                LabeledContent("Using", value: store.usingCustomConnection ? String(localized: "A connection file you chose") : String(localized: "TapMesh's built-in services"))
                Button("Reconnect and Refresh") { Task { await store.connect() } }.disabled(store.connecting)
            }
            LanguageSection()
            Section {
                Toggle("Fast device control", isOn: Binding(
                    get: { store.fastControl },
                    set: { enabled in Task { await store.setFastControl(enabled) } }
                ))
                .disabled(!store.canChangeAgentControl)
                if let problem = store.controlSettingIssue { Text(problem).font(.callout).foregroundStyle(.orange) }
                if store.controlSettingsReady {
                    Text("Devices AI agents may control").font(.headline)
                    if store.agentDevices.isEmpty {
                        Text("None yet. When an agent first asks, you'll see a request in Approvals.")
                            .font(.callout).foregroundStyle(.secondary)
                    } else {
                        ForEach(store.agentDevices.sorted(), id: \.self) { id in
                            let name = store.devices.first { $0.id == id }?.name
                            HStack {
                                if let name { Text(verbatim: name) }
                                else { technical(id).font(.caption.monospaced()) }
                                Spacer()
                                Button("Remove") { Task { await store.setAgentControl(deviceID: id, allowed: false) } }
                                    .accessibilityLabel("Remove AI control for \(name ?? id)")
                                    .disabled(!store.canChangeAgentControl)
                            }
                        }
                    }
                }
            } header: {
                Text("Device control")
            } footer: {
                if store.fastControl {
                    Text("Simulators are always available to AI agents.").font(.callout).foregroundStyle(.secondary)
                } else {
                    Text("When off, every tap or keystroke from an agent waits for your approval in TapMesh.").font(.callout).foregroundStyle(.secondary)
                }
            }
            ApprovalNotificationSection(notifier: store.approvalNotifier)
            Section("How TapMesh runs") {
                Text("TapMesh starts its own services on this Mac when you open it. Your device data and credentials stay on this Mac.").font(.callout).foregroundStyle(.secondary)
                Text("Keep TapMesh open while AI clients use its local HTTP or MCP connection. Quitting TapMesh stops the services it started.").font(.callout).foregroundStyle(.secondary)
            }
            Section("Getting started") {
                Text("Install Xcode and open it once so it can finish installing Apple's developer tools. Simulators use the runtimes installed in Xcode.").font(.callout).foregroundStyle(.secondary)
                Text("For an iPhone or iPad: connect it, trust this Mac, turn on Developer Mode, and keep it unlocked.").font(.callout).foregroundStyle(.secondary)
            }
        }.formStyle(.grouped)
    }

    // MARK: Build folders

    private var buildFolders: some View {
        Form {
            if store.usingCustomConnection {
                Section("Build folders") {
                    Text("Build folders are managed by the connection file you chose, not by TapMesh. Switch back to the built-in services in Advanced to manage them here.").font(.callout).foregroundStyle(.secondary)
                }
            } else {
                Section {
                    if store.grantedBuildFolders.isEmpty {
                        Text("No folders allowed yet.").foregroundStyle(.secondary)
                    }
                    ForEach(store.grantedBuildFolders, id: \.self) { path in
                        HStack {
                            Text(verbatim: path).font(.caption.monospaced()).textSelection(.enabled).environment(\.layoutDirection, .leftToRight)
                            Spacer()
                            Button("Remove") { store.removeBuildFolder(path) }
                        }
                    }
                } header: {
                    Text("Allowed folders")
                } footer: {
                    Text("TapMesh can only build projects inside these folders, so add one before your first build. Each build still asks for your approval.").font(.callout).foregroundStyle(.secondary)
                }
                Section {
                    HStack {
                        Button("Allow Build Folder…") { Task { await store.grantBuildFolder() } }.disabled(!store.online)
                        Button("Restart Local Services") { Task { await store.restartLocalServices() } }
                    }
                    if store.localRestartRequired { Text("Restart local services to apply your folder changes.").font(.callout).foregroundStyle(.orange) }
                } footer: {
                    Text("Folder changes take effect after you restart local services.").font(.callout).foregroundStyle(.secondary)
                }
            }
        }.formStyle(.grouped).disabled(!store.usingCustomConnection && connectionLocked)
    }

    // MARK: Advanced

    private var advanced: some View {
        Form {
            Section("Use an existing connection") {
                Text("Choose a connection.json file to use local services you manage yourself, such as an existing private setup. TapMesh leaves those services running when it quits.").font(.callout).foregroundStyle(.secondary)
                LabeledContent("Connection file") { Text(verbatim: store.connectionFilePath).font(.caption.monospaced()).textSelection(.enabled).environment(\.layoutDirection, .leftToRight) }
                if LocalRuntimeController.shared.usesEnvironmentSelection {
                    Text("This connection was chosen when TapMesh was launched. To change it here, restart TapMesh without TAPMESH_CONNECTION_FILE or TAPMESH_PROFILE_DIR set.").font(.caption).foregroundStyle(.secondary)
                } else {
                    HStack {
                        Button("Choose Connection File…") { Task { await store.chooseConnectionFile() } }
                        if store.usingCustomConnection { Button("Use Built-in Services") { Task { await store.useBuiltInRuntime() } } }
                    }.disabled(connectionLocked)
                }
                Button("Show Connection Folder") { NSWorkspace.shared.open(URL(fileURLWithPath: store.connectionFilePath).deletingLastPathComponent()) }
            }
            Section("Addresses") {
                LabeledContent("Server") { technical(store.endpoint) }
                LabeledContent("Mac agent") { technical(store.agentEndpoint) }
                LabeledContent("Tool catalog version") { technical(store.catalogVersion) }
            }
        }.formStyle(.grouped)
    }

    // MARK: Diagnostics

    private var diagnostics: some View {
        Form {
            Section {
                LabeledContent("Server", value: healthSummary(store.health))
                if let version = store.health?["version"]?.scalarDescription { LabeledContent("Server version") { technical(version) } }
                LabeledContent("Mac agent", value: healthSummary(store.agentHealth))
                if let version = store.agentHealth?["version"]?.scalarDescription { LabeledContent("Mac agent version") { technical(version) } }
                ForEach(appleTools, id: \.key) { tool in
                    LabeledContent(tool.label, value: tool.value)
                }
            } header: {
                Text("Summary")
            } footer: {
                Text("These health reports describe what the services can do and what they still need. They never include your credentials.").font(.callout).foregroundStyle(.secondary)
            }
            Section("Technical details") {
                TechnicalDetails(title: "Server health report") {
                    if let health = store.health { JSONResultView(value: health) } else { Text("The server didn't send a health report.") }
                }
                TechnicalDetails(title: "Mac agent health report") {
                    if let health = store.agentHealth { JSONResultView(value: health) } else { Text("The Mac agent didn't answer its health check.") }
                }
                TechnicalDetails(title: "Session") {
                    DetailRow(label: "Local controller", value: store.sessionID)
                }
            }
            Section {
                HStack {
                    Button("Refresh Diagnostics") { Task { await store.connect() } }
                    Button("Export Diagnostics…") { Task { await SupportExporter.export(store: store) } }
                }
            }
        }.formStyle(.grouped)
    }

    /// Plain-language status for a service health response; unexpected values are shown as-is.
    private func healthSummary(_ health: JSONValue?) -> String {
        guard let health else { return String(localized: "No response") }
        guard let status = health["status"]?.scalarDescription else { return String(localized: "Responded") }
        return status == "ok" ? String(localized: "Running") : status
    }

    /// The Mac agent's Apple tool checks, e.g. simctl and devicectl, in readable terms.
    private var appleTools: [(key: String, label: String, value: String)] {
        let checks = store.agentHealth?["backend_health"]?.objectValue ?? [:]
        return checks.keys.sorted().map { key in
            let label: String
            switch key {
            case "simctl": label = String(localized: "Simulator tools")
            case "devicectl": label = String(localized: "iPhone and iPad tools")
            default: label = key
            }
            let raw = checks[key]?.scalarDescription ?? ""
            let value = raw == "available" ? String(localized: "Available") : raw == "unavailable" ? String(localized: "Not available") : raw
            return (key: key, label: label, value: value)
        }
    }

    // MARK: About

    private var about: some View {
        Form {
            Section("License and source") {
                Text("TapMesh is free software under the GNU General Public License, version 3 or any later version. It comes without warranty. See the included license notices for the full terms.").font(.callout).foregroundStyle(.secondary)
                HStack {
                    Button("Show Licenses") { showBundledFolder("Licenses") }
                    Button("Show Source") { showBundledFolder("Source") }
                }
            }
        }.formStyle(.grouped)
    }

    /// Addresses, versions and other technical values: never translated, always left-to-right.
    private func technical(_ value: String) -> some View {
        Text(verbatim: value).textSelection(.enabled).environment(\.layoutDirection, .leftToRight)
    }

    private func showBundledFolder(_ name: String) {
        guard let resources = Bundle.main.resourceURL else { return }
        let folder = resources.appendingPathComponent(name, isDirectory: true)
        guard FileManager.default.fileExists(atPath: folder.path), NSWorkspace.shared.open(folder) else {
            store.issue = name == "Licenses"
                ? String(localized: "The licenses folder isn't included in this copy of TapMesh.")
                : String(localized: "The source folder isn't included in this copy of TapMesh.")
            return
        }
    }
}

/// The approval notification setting. Showing it reads the system permission but never asks for it.
private struct ApprovalNotificationSection: View {
    @ObservedObject var notifier: ApprovalNotifier
    @AppStorage(ApprovalNotifier.enabledKey) private var notify = true

    var body: some View {
        Section {
            Toggle("Notify me when a request needs approval", isOn: $notify)
            if notify, notifier.permission == .denied {
                HStack {
                    Text("Notifications are turned off for TapMesh in System Settings.").font(.callout).foregroundStyle(.secondary)
                    Spacer()
                    Button("Open Notification Settings") { notifier.openSystemNotificationSettings() }
                }
            }
        } header: {
            Text("Approvals")
        } footer: {
            Text("When TapMesh isn't in front, it shows a notification for new requests. The Dock icon always shows how many requests are waiting.").font(.callout).foregroundStyle(.secondary)
        }
        .task { await notifier.refreshPermission() }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in Task { await notifier.refreshPermission() } }
    }
}

/// The app's own language. Uses the standard per-app `AppleLanguages` override, which macOS
/// reads at launch, so a change applies the next time TapMesh opens.
private struct LanguageSection: View {
    enum Choice: Hashable { case system, english, arabic }

    private static let key = "AppleLanguages"
    /// What this copy of TapMesh launched with; read once, before any change in this session.
    private static let launchChoice = storedChoice()
    @State private var choice = LanguageSection.storedChoice()

    var body: some View {
        Section {
            Picker("Language", selection: $choice) {
                Text("System Default").tag(Choice.system)
                // Each language is named in itself, so it can be found whatever the current language is.
                Text(verbatim: "English").tag(Choice.english)
                Text(verbatim: "العربية").tag(Choice.arabic)
            }
            .onChange(of: choice) { _, newValue in Self.store(newValue) }
            if choice != Self.launchChoice {
                Text("Restart TapMesh to apply the new language.").font(.callout).foregroundStyle(.secondary)
            }
        }
    }

    /// Only TapMesh's own setting counts here, not the language list inherited from the system.
    private static func storedChoice() -> Choice {
        let domain = UserDefaults.standard.persistentDomain(forName: Bundle.main.bundleIdentifier ?? "")
        guard let first = (domain?[key] as? [String])?.first else { return .system }
        if first.hasPrefix("ar") { return .arabic }
        if first.hasPrefix("en") { return .english }
        return .system
    }

    private static func store(_ choice: Choice) {
        switch choice {
        case .system: UserDefaults.standard.removeObject(forKey: key)
        case .english: UserDefaults.standard.set(["en"], forKey: key)
        case .arabic: UserDefaults.standard.set(["ar"], forKey: key)
        }
    }
}
