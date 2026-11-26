import SwiftUI
import AppKit
import TapMeshCore

struct AppsView: View {
    @ObservedObject var store: WorkspaceStore
    @State private var search = ""
    @State private var bundleID = ""
    @State private var installPath = ""
    @State private var pendingRemoval: String?
    @State private var showingMoreOptions = false
    @State private var dropTargeted = false
    /// True while this page is listing apps (automatically or from Refresh Apps).
    @State private var loading = false
    /// The device whose app list was last fetched successfully, even if that list was empty.
    @State private var loadedDeviceID: String?
    /// The device this page already tried to load automatically. Prevents repeated automatic attempts.
    @State private var autoLoadAttemptedFor: String?
    /// Why the last attempt to list apps didn't work, shown in the empty state instead of a banner.
    @State private var loadProblem: String?
    /// A dropped app waiting for the person to confirm the install, with the device it was dropped on.
    @State private var pendingDrop: PendingDrop?

    private struct PendingDrop { let url: URL; let deviceID: String; let deviceName: String }

    private static var installNote: String { String(localized: "Choose an .app, or an .ipa for a physical iPhone or iPad, inside a folder you allowed under Settings › Build Folders.") }

    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "Apps", subtitle: store.selectedDevice.map { String(localized: "Manage development apps on \($0.name).") } ?? String(localized: "Choose a device to manage its apps."), icon: "square.grid.2x2") {
                HStack(spacing: 10) {
                    DeviceContextMenu(store: store)
                    Button("Refresh Apps") { Task { await loadApps(automatic: false) } }
                        .disabled(!store.canControl || loading)
                        .help("Lists the development apps installed on the current device again")
                    Button { showingMoreOptions = true } label: { Label("More Options", systemImage: "ellipsis.circle") }
                        .labelStyle(.iconOnly)
                        .help("Launch an app by its bundle ID, or install from a path you type")
                        .accessibilityLabel("More options: launch by bundle ID or install from a path")
                        .popover(isPresented: $showingMoreOptions, arrowEdge: .bottom) { moreOptions }
                    Button("Install App…") { chooseAndInstall() }
                        .buttonStyle(.borderedProminent)
                        .disabled(!store.canControl)
                        .help(store.selectedDevice.map { String(localized: "Choose an .app or .ipa and install it on \($0.name)") } ?? String(localized: "Choose a device first"))
                }
            }
            Divider()
            VStack(alignment: .leading, spacing: 14) {
                TextField("Search installed apps", text: $search).textFieldStyle(.roundedBorder)
                    .disabled(store.apps.isEmpty)
                Group {
                    if filtered.isEmpty { emptyState.frame(maxWidth: .infinity, maxHeight: .infinity) }
                    else { appList }
                }
                .dropDestination(for: URL.self) { urls, _ in
                    guard store.canControl, let device = store.selectedDevice, let url = urls.first, Self.isInstallable(url) else { return false }
                    // A drop can be accidental, so ask before installing anything.
                    pendingDrop = PendingDrop(url: url, deviceID: device.id, deviceName: device.name)
                    return true
                } isTargeted: { dropTargeted = $0 }
                .overlay {
                    if dropTargeted {
                        RoundedRectangle(cornerRadius: 8)
                            .strokeBorder(Color.accentColor, lineWidth: 2)
                            .overlay(alignment: .bottom) {
                                Text(dropHint)
                                    .font(.callout).padding(8)
                                    .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 6))
                                    .padding(10)
                            }
                            .allowsHitTesting(false)
                    }
                }
                .confirmationDialog(Text(dropConfirmationTitle),
                                    isPresented: Binding(get: { pendingDrop != nil }, set: { if !$0 { pendingDrop = nil } }), titleVisibility: .visible) {
                    Button("Install") { confirmDrop() }
                    Button("Cancel", role: .cancel) { pendingDrop = nil }
                } message: { Text("The app must be inside a folder you allowed under Settings › Build Folders.") }
                Text("Installing or removing an app may ask for your approval on this Mac. Apps to install must be inside a folder you allowed under Settings › Build Folders. Uninstalling also deletes the app’s data.")
                    .font(.caption).foregroundStyle(.secondary)
            }.padding(24)
        }
        .onAppear { scheduleAutoLoad() }
        .onChange(of: store.selectedID) { _, _ in loadedDeviceID = nil; loadProblem = nil; search = "" }
        .onChange(of: AutoLoadKey(deviceID: store.selectedID, canControl: store.canControl)) { _, _ in scheduleAutoLoad() }
        .confirmationDialog(Text(pendingRemoval.map { String(localized: "Uninstall \($0)?") } ?? String(localized: "Uninstall App?")), isPresented: Binding(get: { pendingRemoval != nil }, set: { if !$0 { pendingRemoval = nil } }), titleVisibility: .visible) {
            Button("Uninstall App and Delete Its Data", role: .destructive) { if let id = pendingRemoval { uninstall(id) }; pendingRemoval = nil }
            Button("Cancel", role: .cancel) { pendingRemoval = nil }
        } message: { Text("This removes the app from the selected device. Its data on the device is deleted and can’t be recovered.") }
    }

    private var appList: some View {
        List(Array(filtered.enumerated()), id: \.offset) { _, app in
            let id = app["bundle_id"]?.stringValue ?? app["bundle_identifier"]?.stringValue ?? app["CFBundleIdentifier"]?.stringValue ?? ""
            HStack(spacing: 12) {
                Image(systemName: "app").font(.title2).foregroundStyle(.secondary).accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 4) { Text(verbatim: app["name"]?.stringValue ?? app["display_name"]?.stringValue ?? id).font(.headline); Text(verbatim: id).font(.caption.monospaced()).foregroundStyle(.secondary).environment(\.layoutDirection, .leftToRight) }
                Spacer()
                Button("Launch") { launch(id) }.disabled(!store.canControl || id.isEmpty)
                Menu {
                    Button("Terminate App") { Task { await store.run("terminate_app", arguments: ["bundle_id": .string(id)]) } }
                    Button("Uninstall App…", role: .destructive) { pendingRemoval = id }
                } label: { Image(systemName: "ellipsis.circle") }.menuStyle(.borderlessButton).fixedSize().disabled(!store.canControl || id.isEmpty)
                    .accessibilityLabel("More actions for \(app["name"]?.stringValue ?? id)")
            }.padding(.vertical, 7)
        }.listStyle(.inset)
    }

    /// One truthful message for each reason the list can be empty.
    @ViewBuilder private var emptyState: some View {
        if let device = store.selectedDevice {
            if !store.online {
                ContentUnavailableView("TapMesh Isn’t Connected", systemImage: "bolt.horizontal.circle", description: Text("Reconnect to list the apps on \(device.name)."))
            } else if !device.ready {
                ContentUnavailableView("\(device.name) Isn’t Ready", systemImage: "exclamationmark.triangle", description: Text(device.reasons.first ?? String(localized: "Apps can be listed once the device is ready.")))
            } else if loading {
                VStack(spacing: 10) {
                    ProgressView().controlSize(.small)
                    Text("Loading apps on \(device.name)…").foregroundStyle(.secondary)
                }
            } else if !store.apps.isEmpty {
                ContentUnavailableView.search(text: search)
            } else if loadedDeviceID == device.id {
                ContentUnavailableView("No Apps Installed", systemImage: "app.dashed", description: Text("No development apps are installed on \(device.name). Use Install App… or drop an .app or .ipa here."))
            } else if store.busy || store.takingControl {
                ContentUnavailableView("Apps Not Loaded Yet", systemImage: "hourglass", description: Text("Another action is running. The list loads when it finishes."))
            } else {
                ContentUnavailableView("Apps Not Loaded", systemImage: "app.dashed", description: Text(loadProblem.map { String(localized: "The list couldn’t be loaded. \($0)") } ?? String(localized: "Press Refresh Apps to list the development apps on \(device.name).")))
            }
        } else if store.selectedID != nil {
            ContentUnavailableView("Device Not Available", systemImage: "iphone.slash", description: Text("The chosen device is no longer connected. Reconnect it or choose another device."))
        } else {
            ContentUnavailableView("No Device Chosen", systemImage: "iphone.gen3", description: Text("Choose a device in the sidebar or from the device menu above to see its apps."))
        }
    }

    // MARK: Moving expert actions out of the way

    private var moreOptions: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Launch an app").font(.headline)
            TextField("Bundle ID, for example com.example.MyApp", text: $bundleID).textFieldStyle(.roundedBorder).accessibilityLabel("App bundle identifier")
                .onSubmit { if !bundleID.isEmpty && store.canControl { launch(bundleID) } }
            HStack { Spacer(); Button("Launch App") { launch(bundleID) }.disabled(bundleID.isEmpty || !store.canControl) }
            Divider()
            Text("Install from a path").font(.headline)
            Text(Self.installNote).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            TextField("Path to an .app or .ipa", text: $installPath).textFieldStyle(.roundedBorder).accessibilityLabel("Path to an app to install")
            HStack {
                Button("Choose…") { if let path = choosePath() { installPath = path } }
                Spacer()
                Button("Install") { install(installPath); showingMoreOptions = false }.disabled(installPath.isEmpty || !store.canControl)
            }
            Text(store.selectedDevice.map { String(localized: "Acts on \($0.name).") } ?? String(localized: "Choose a device first.")).font(.caption).foregroundStyle(.secondary)
        }
        .padding(16)
        .frame(width: 320)
    }

    // MARK: Actions

    private var dropHint: String {
        guard store.canControl else { return String(localized: "Choose a ready device before installing") }
        if let name = store.selectedDevice?.name { return String(localized: "Drop to install on \(name)") }
        return String(localized: "Drop to install on the current device")
    }
    private var dropConfirmationTitle: String {
        guard let drop = pendingDrop else { return String(localized: "Install App?") }
        return String(localized: "Install \(drop.url.lastPathComponent) on \(drop.deviceName)?")
    }

    private var filtered: [JSONValue] { store.apps.filter { search.isEmpty || $0.prettyPrinted.localizedCaseInsensitiveContains(search) } }
    private func launch(_ id: String) { Task { await store.run("launch_app", arguments: ["bundle_id": .string(id)]) } }
    private func install(_ path: String) {
        Task {
            let result = await store.run("install_app", arguments: ["artifact_path": .string(path), "replace": .bool(false)])
            // The follow-up refresh must not hide why the install didn't work.
            await store.refreshApps(announce: result != nil && result?.error == nil)
        }
    }
    private func uninstall(_ id: String) {
        Task {
            let result = await store.run("uninstall_app", arguments: ["bundle_id": .string(id)])
            // A failed uninstall keeps its banner; the follow-up refresh only updates the list.
            await store.refreshApps(announce: result != nil && result?.error == nil)
        }
    }
    /// Installs a confirmed drop, but only on the device it was dropped on.
    private func confirmDrop() {
        guard let drop = pendingDrop else { return }
        pendingDrop = nil
        guard store.selectedID == drop.deviceID, store.canControl else {
            store.issue = String(localized: "\(drop.url.lastPathComponent) wasn't installed because the device changed or isn't ready. Drop it again to install it.")
            return
        }
        install(drop.url.path)
    }
    private func chooseAndInstall() {
        if let path = choosePath() { installPath = path; install(path) }
    }
    private func choosePath() -> String? {
        let panel = NSOpenPanel(); panel.title = String(localized: "Choose Development App"); panel.canChooseFiles = true; panel.canChooseDirectories = true; panel.allowsMultipleSelection = false; panel.treatsFilePackagesAsDirectories = false
        panel.message = Self.installNote
        panel.prompt = store.selectedDevice.map { String(localized: "Install on \($0.name)") } ?? String(localized: "Choose")
        return panel.runModal() == .OK ? panel.url?.path : nil
    }
    private static func isInstallable(_ url: URL) -> Bool {
        url.isFileURL && ["app", "ipa"].contains(url.pathExtension.lowercased())
    }

    // MARK: Automatic loading

    private struct AutoLoadKey: Equatable { let deviceID: String?; let canControl: Bool }

    /// Runs outside the view's own task so a view update can't cancel a request that already reached the device.
    private func scheduleAutoLoad() { Task { await loadApps(automatic: true) } }

    /// Automatic loading happens at most once per chosen device, only when nothing else is running,
    /// and never leaves an error banner behind; a problem is shown in the empty state instead.
    private func loadApps(automatic: Bool) async {
        guard let id = store.selectedID, store.canControl, !loading else { return }
        if automatic {
            guard store.apps.isEmpty, autoLoadAttemptedFor != id, store.tools.contains(where: { $0.name == "list_apps" }) else { return }
            autoLoadAttemptedFor = id
        }
        loading = true; loadProblem = nil
        let failure = await store.refreshApps(announce: !automatic)
        loading = false
        guard store.selectedID == id else { return }
        if let failure { loadProblem = failure } else { loadedDeviceID = id }
    }
}

/// A page's title row. Literal titles and subtitles are localization keys; a `String` subtitle
/// is shown as given, so callers build it with `String(localized:)`.
struct PageHeader<Actions: View>: View {
    let title: LocalizedStringKey
    let subtitle: Text
    let icon: String
    @ViewBuilder let actions: () -> Actions

    init(title: LocalizedStringKey, subtitle: LocalizedStringKey, icon: String, @ViewBuilder actions: @escaping () -> Actions) {
        self.title = title; self.subtitle = Text(subtitle); self.icon = icon; self.actions = actions
    }

    @_disfavoredOverload
    init(title: LocalizedStringKey, subtitle: String, icon: String, @ViewBuilder actions: @escaping () -> Actions) {
        self.title = title; self.subtitle = Text(verbatim: subtitle); self.icon = icon; self.actions = actions
    }

    var body: some View {
        HStack(spacing: 14) {
            Image(systemName: icon).font(.title2).foregroundStyle(.secondary).accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 5) { Text(title).font(.title2.weight(.semibold)); subtitle.font(.callout).foregroundStyle(.secondary) }
            Spacer()
            actions()
        }.padding(24)
    }
}
