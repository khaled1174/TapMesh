import SwiftUI
import AppKit
import TapMeshCore

struct AppsView: View {
    @ObservedObject var store: WorkspaceStore
    @State private var search = ""
    @State private var bundleID = ""
    @State private var installPath = ""
    @State private var pendingRemoval: String?
    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "Apps", subtitle: store.selectedDevice.map { "Manage development apps on \($0.name)." } ?? "Select a device in the sidebar to manage its apps.", icon: "square.grid.2x2") {
                Button("Refresh Apps") { Task { await store.refreshApps() } }.disabled(!store.canControl)
            }
            Divider()
            HStack(alignment: .top, spacing: 26) {
                VStack(alignment: .leading, spacing: 14) {
                    TextField("Search installed apps", text: $search).textFieldStyle(.roundedBorder)
                    if filtered.isEmpty {
                        ContentUnavailableView(store.apps.isEmpty ? "No apps loaded" : "No matching apps", systemImage: "app.dashed", description: Text("Refresh to list installed development apps. Backend restrictions are shown when an action is unavailable.")).frame(maxHeight: .infinity)
                    } else {
                        List(Array(filtered.enumerated()), id: \.offset) { _, app in
                            let id = app["bundle_id"]?.stringValue ?? app["bundle_identifier"]?.stringValue ?? app["CFBundleIdentifier"]?.stringValue ?? ""
                            HStack(spacing: 12) {
                                Image(systemName: "app").font(.title2).foregroundStyle(.secondary)
                                VStack(alignment: .leading, spacing: 4) { Text(app["name"]?.stringValue ?? app["display_name"]?.stringValue ?? id).font(.headline); Text(id).font(.caption.monospaced()).foregroundStyle(.secondary) }
                                Spacer()
                                Button("Launch") { launch(id) }.disabled(!store.canControl || id.isEmpty)
                                Menu {
                                    Button("Terminate App") { Task { await store.run("terminate_app", arguments: ["bundle_id": .string(id)]) } }
                                    Button("Uninstall App…", role: .destructive) { pendingRemoval = id }
                                } label: { Image(systemName: "ellipsis.circle") }.menuStyle(.borderlessButton).fixedSize().disabled(!store.canControl || id.isEmpty)
                            }.padding(.vertical, 7)
                        }.listStyle(.inset)
                    }
                }.frame(maxWidth: .infinity)
                VStack(alignment: .leading, spacing: 18) {
                    Text("Launch an app").font(.headline)
                    TextField("Bundle identifier", text: $bundleID).textFieldStyle(.roundedBorder).accessibilityLabel("App bundle identifier")
                    Button("Launch App") { launch(bundleID) }.disabled(bundleID.isEmpty || !store.canControl)
                    Divider().padding(.vertical, 6)
                    Text("Install a development app").font(.headline)
                    Text("Choose an app or archive inside a directory granted to the Mac agent.").font(.callout).foregroundStyle(.secondary)
                    TextField("Approved local artifact path", text: $installPath).textFieldStyle(.roundedBorder)
                    HStack {
                        Button("Choose…") { chooseInstall() }
                        Spacer()
                        Button("Install") { Task { await store.run("install_app", arguments: ["artifact_path": .string(installPath), "replace": .bool(false)]); await store.refreshApps() } }.buttonStyle(.borderedProminent).disabled(installPath.isEmpty || !store.canControl)
                    }
                    Text("Installation and removal may need local approval. Uninstalling removes the app’s data.").font(.caption).foregroundStyle(.secondary)
                    Spacer()
                }.frame(width: 270)
            }.padding(24)
        }
        .confirmationDialog("Uninstall \(pendingRemoval ?? "app")?", isPresented: Binding(get: { pendingRemoval != nil }, set: { if !$0 { pendingRemoval = nil } }), titleVisibility: .visible) {
            Button("Uninstall App and Delete Its Data", role: .destructive) { if let id = pendingRemoval { Task { await store.run("uninstall_app", arguments: ["bundle_id": .string(id)]); await store.refreshApps() } }; pendingRemoval = nil }
            Button("Cancel", role: .cancel) { pendingRemoval = nil }
        } message: { Text("This removes the named app from the selected device. This operation cannot restore its local data.") }
    }
    private var filtered: [JSONValue] { store.apps.filter { search.isEmpty || $0.prettyPrinted.localizedCaseInsensitiveContains(search) } }
    private func launch(_ id: String) { Task { await store.run("launch_app", arguments: ["bundle_id": .string(id)]) } }
    private func chooseInstall() {
        let panel = NSOpenPanel(); panel.title = "Choose Development App"; panel.canChooseFiles = true; panel.canChooseDirectories = true; panel.allowsMultipleSelection = false; panel.treatsFilePackagesAsDirectories = false
        if panel.runModal() == .OK { installPath = panel.url?.path ?? "" }
    }
}

struct PageHeader<Actions: View>: View {
    let title: String
    let subtitle: String
    let icon: String
    @ViewBuilder let actions: () -> Actions
    var body: some View {
        HStack(spacing: 14) {
            Image(systemName: icon).font(.title2).foregroundStyle(.secondary).accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 5) { Text(title).font(.title2.weight(.semibold)); Text(subtitle).font(.callout).foregroundStyle(.secondary) }
            Spacer()
            actions()
        }.padding(24)
    }
}
