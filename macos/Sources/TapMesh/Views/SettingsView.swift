import SwiftUI
import AppKit
import TapMeshCore

struct SettingsView: View {
    @ObservedObject var store: WorkspaceStore
    var body: some View {
        TabView {
            Form {
                Section("Local connection") {
                    LabeledContent("Mode", value: store.usingCustomConnection ? "Custom connection" : "Built-in local services")
                    LabeledContent("Server", value: store.endpoint)
                    LabeledContent("Mac agent", value: store.agentEndpoint)
                    LabeledContent("Status", value: store.connecting ? "Starting…" : store.online ? "Connected" : "Unavailable")
                    LabeledContent("Tool catalog", value: store.catalogVersion)
                    Button("Reconnect and Refresh") { Task { await store.connect() } }.disabled(store.connecting)
                }
                Section("Runtime setup") {
                    Text("TapMesh starts its bundled services on this Mac when you open the app. Your device data and credentials stay on this Mac. Keep TapMesh open while using its local HTTP or MCP connection; quitting stops the services it started.").font(.callout).foregroundStyle(.secondary)
                    Text("Install Xcode and open it once to finish installing Apple's development tools. Connect your iPhone or iPad, trust this Mac, enable Developer Mode, and leave the device unlocked. Simulators use the runtimes installed in Xcode.").font(.callout).foregroundStyle(.secondary)
                    Button("Show Connection Folder") { NSWorkspace.shared.open(URL(fileURLWithPath: store.connectionFilePath).deletingLastPathComponent()) }
                }
                Section("Advanced") {
                    if !store.usingCustomConnection {
                        DisclosureGroup("Build folder access") {
                            Text("Allow the project folders you want TapMesh to build. Each build still requires local approval. Folder changes take effect after restarting local services.").font(.callout).foregroundStyle(.secondary)
                            ForEach(store.grantedBuildFolders, id: \.self) { path in
                                HStack {
                                    Text(path).font(.caption.monospaced()).textSelection(.enabled)
                                    Spacer()
                                    Button("Remove") { store.removeBuildFolder(path) }
                                }
                            }
                            HStack {
                                Button("Allow Build Folder…") { Task { await store.grantBuildFolder() } }.disabled(!store.online)
                                Button("Restart Local Services") { Task { await store.restartLocalServices() } }
                            }
                            if store.localRestartRequired { Text("Restart required to apply folder access changes.").font(.caption).foregroundStyle(.orange) }
                        }.disabled(store.connecting || store.busy || store.buildRunning || store.recordingID != nil)
                    }
                    DisclosureGroup("Use an existing connection") {
                        Text("Choose a connection.json file to use separately managed local services, including an existing private setup. TapMesh leaves those services running when it quits.").font(.callout).foregroundStyle(.secondary)
                        Text(store.connectionFilePath).font(.caption.monospaced()).textSelection(.enabled)
                        if LocalRuntimeController.shared.usesEnvironmentSelection {
                            Text("This profile was selected by the launch environment. Restart without TAPMESH_CONNECTION_FILE or TAPMESH_PROFILE_DIR to change it here.").font(.caption).foregroundStyle(.secondary)
                        } else {
                            HStack {
                                Button("Choose Connection File…") { Task { await store.chooseConnectionFile() } }
                                if store.usingCustomConnection { Button("Use Built-in Services") { Task { await store.useBuiltInRuntime() } } }
                            }.disabled(store.connecting || store.busy || store.buildRunning || store.recordingID != nil)
                        }
                    }
                }
                Section("License and source") {
                    Text("TapMesh is free software under the GNU General Public License, version 3 or any later version. It comes without warranty. See the included license notices for the full terms.").font(.callout).foregroundStyle(.secondary)
                    HStack {
                        Button("Show Licenses") { showBundledFolder("Licenses") }
                        Button("Show Source") { showBundledFolder("Source") }
                    }
                }
            }.formStyle(.grouped).tabItem { Label("Connection", systemImage: "network") }
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    Text("Runtime diagnostics").font(.title2.weight(.semibold))
                    Text("Service health contains capability and prerequisite details. Credential contents are not displayed.").foregroundStyle(.secondary)
                    Text("Server").font(.headline)
                    if let health = store.health { JSONResultView(value: health) } else { Text("No server health response.").foregroundStyle(.secondary) }
                    Divider()
                    Text("Mac agent").font(.headline)
                    if let health = store.agentHealth { JSONResultView(value: health) } else { Text("The local agent did not answer its health endpoint.").foregroundStyle(.secondary) }
                    Divider()
                    LabeledContent("Local controller", value: store.sessionID).font(.caption.monospaced()).textSelection(.enabled)
                    Button("Refresh Diagnostics") { Task { await store.connect() } }
                }.padding(24)
            }.tabItem { Label("Diagnostics", systemImage: "stethoscope") }
        }.padding(12).frame(width: 660, height: 650)
    }
    private func showBundledFolder(_ name: String) {
        guard let resources = Bundle.main.resourceURL else { return }
        let folder = resources.appendingPathComponent(name, isDirectory: true)
        guard FileManager.default.fileExists(atPath: folder.path), NSWorkspace.shared.open(folder) else {
            store.issue = "The bundled \(name.lowercased()) folder is unavailable in this installation."
            return
        }
    }
}
