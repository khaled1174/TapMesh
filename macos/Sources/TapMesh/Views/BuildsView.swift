import SwiftUI
import AppKit
import TapMeshCore

struct BuildsView: View {
    @ObservedObject var store: WorkspaceStore
    @State private var project = ""
    @State private var scheme = ""
    @State private var configuration = "Debug"
    @State private var launch = true
    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "Builds", subtitle: store.selectedDevice.map { "Build, install, and run on \($0.name)." } ?? "Select the destination device before starting a build.", icon: "hammer") { EmptyView() }
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    Form {
                        LabeledContent("Project or workspace") {
                            HStack { TextField("Approved .xcodeproj or .xcworkspace path", text: $project); Button("Choose…") { chooseProject() } }
                        }
                        TextField("Scheme", text: $scheme)
                        Picker("Configuration", selection: $configuration) { Text("Debug").tag("Debug"); Text("Release").tag("Release") }
                        Toggle("Launch after installation", isOn: $launch)
                    }.formStyle(.grouped)
                    HStack {
                        Text("Projects must be in an agent-approved directory. Host execution requires local approval.").font(.callout).foregroundStyle(.secondary)
                        Spacer()
                        if store.buildRunning { Button("Cancel Build", role: .destructive) { store.cancelBuild() } }
                        else { Button("Build and Run") { store.build(project: project, scheme: scheme, configuration: configuration, launch: launch) }.buttonStyle(.borderedProminent).disabled(project.isEmpty || scheme.isEmpty || !store.canControl) }
                    }
                    GroupBox {
                        VStack(alignment: .leading, spacing: 14) {
                            HStack { if store.buildRunning { ProgressView().controlSize(.small) }; Text(store.buildState).font(.headline) }
                            if store.buildRunning { ProgressView().progressViewStyle(.linear); Text("The service returns stage results and a bounded build log when the operation completes.").font(.callout).foregroundStyle(.secondary) }
                            if let cancellation = store.cancellationState { Text(cancellation).foregroundStyle(.orange).font(.callout) }
                            if let result = store.lastResult, !store.buildRunning { Text(result.prettyPrinted).font(.caption.monospaced()).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }
                        }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
                    }
                }.padding(24)
            }
        }
    }
    private func chooseProject() {
        let panel = NSOpenPanel(); panel.title = "Choose Xcode Project or Workspace"; panel.canChooseDirectories = true; panel.canChooseFiles = true; panel.treatsFilePackagesAsDirectories = false
        if panel.runModal() == .OK { project = panel.url?.path ?? "" }
    }
}
