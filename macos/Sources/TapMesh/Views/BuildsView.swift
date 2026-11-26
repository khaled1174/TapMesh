import SwiftUI
import AppKit
import TapMeshCore

struct BuildsView: View {
    @ObservedObject var store: WorkspaceStore
    // Kept across page switches and launches. Paths and names only; nothing secret is stored here.
    @AppStorage("builds.projectPath") private var project = ""
    @AppStorage("builds.scheme") private var scheme = ""
    @AppStorage("builds.configuration") private var configuration = "Debug"
    @AppStorage("builds.launch") private var launch = true
    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "Builds", subtitle: store.selectedDevice.map { String(localized: "Build, install, and run on \($0.name).") } ?? String(localized: "Select the destination device before starting a build."), icon: "hammer") {
                DeviceContextMenu(store: store)
            }
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    Form {
                        LabeledContent("Project or workspace") {
                            HStack { TextField("Path to an .xcodeproj or .xcworkspace", text: $project); Button("Choose…") { chooseProject() } }
                        }
                        TextField("Scheme", text: $scheme, prompt: Text("Usually your app’s name"))
                        Picker("Configuration", selection: $configuration) { Text(verbatim: "Debug").tag("Debug"); Text(verbatim: "Release").tag("Release") }
                        Toggle("Launch after installation", isOn: $launch)
                    }.formStyle(.grouped).disabled(store.buildRunning)
                    HStack {
                        Text("The project folder must be allowed under Settings › Build Folders. Each build also asks for your approval on this Mac.").font(.callout).foregroundStyle(.secondary)
                        Spacer()
                        if store.buildRunning { Button("Cancel Build", role: .destructive) { store.cancelBuild() } }
                        else { Button("Build and Run") { store.build(project: project, scheme: scheme, configuration: configuration, launch: launch) }.buttonStyle(.borderedProminent).disabled(project.isEmpty || scheme.isEmpty || !store.canControl) }
                    }
                    GroupBox {
                        VStack(alignment: .leading, spacing: 14) {
                            if let run = store.lastBuild { BuildRunView(run: run, running: store.buildRunning, progress: store.buildState, cancellation: store.cancellationState) }
                            else { Text("No build yet in this session. The result of each step appears here.").foregroundStyle(.secondary) }
                        }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
                    }
                }.padding(24)
            }
        }
    }
    private func chooseProject() {
        let panel = NSOpenPanel(); panel.title = String(localized: "Choose Xcode Project or Workspace"); panel.canChooseDirectories = true; panel.canChooseFiles = true; panel.treatsFilePackagesAsDirectories = false
        if panel.runModal() == .OK { project = panel.url?.path ?? "" }
    }
}

/// The last build: what is happening now, or what happened, step by step.
/// The steps come from `BuildReport` (agent/backends.py `_build`); the complete raw payload
/// is always kept under "Full result", and is all that is shown when the shape is unexpected.
private struct BuildRunView: View {
    let run: BuildRun
    let running: Bool
    let progress: String
    let cancellation: String?
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                if running { ProgressView().controlSize(.small) }
                else { Image(systemName: headlineIcon.name).foregroundStyle(headlineIcon.color).accessibilityHidden(true) }
                Text(headline).font(.headline)
            }
            if let device = run.deviceName { Text("On \(device) · started \(run.startedAt.formatted(date: .omitted, time: .shortened))").font(.callout).foregroundStyle(.secondary) }
            if running {
                ProgressView().progressViewStyle(.linear)
                Text("The result of each step appears here when the build finishes. The build log is in Saved Files.").font(.callout).foregroundStyle(.secondary)
            }
            if let cancellation { Text(cancellation).foregroundStyle(.orange).font(.callout) }
            if !running, let summary = run.summary { Text(summary).font(.callout).textSelection(.enabled) }
            if !running, let report = run.report { BuildResultView(report: report) }
        }
    }
    private var headline: String {
        if running { return progress }
        switch run.phase {
        case .running: return String(localized: "Build in progress")
        case .cancelled: return String(localized: "Build cancelled")
        case .noResult: return String(localized: "No result from the build")
        case .finished:
            guard let report = run.report else { return String(localized: "Build finished") }
            switch report.outcome {
            case .succeeded: return String(localized: "Build succeeded")
            case .partial: return String(localized: "Build only partly finished")
            case .failed:
                switch report.failedStage?.name {
                case "build"?: return String(localized: "Build failed: Xcode couldn’t build the app")
                case "install"?: return String(localized: "Build failed: the app couldn’t be installed")
                case "launch"?: return String(localized: "Build failed: the app couldn’t be opened")
                case let other?: return String(localized: "Build failed at the “\(DisplayText.toolName(other))” step")
                case nil: return String(localized: "Build failed")
                }
            }
        }
    }
    private var headlineIcon: (name: String, color: Color) {
        switch (run.phase, run.report?.outcome) {
        case (.finished, .succeeded?): ("checkmark.circle.fill", .green)
        case (.finished, .failed?): ("xmark.octagon.fill", .red)
        default: ("exclamationmark.triangle.fill", .orange)
        }
    }
}

private struct BuildResultView: View {
    let report: BuildReport
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let stages = report.stages {
                ForEach(Array(stages.enumerated()), id: \.offset) { _, stage in stageRow(stage) }
                if report.failedAfterListedStages {
                    Label("The steps above finished, but the build stopped at the next step.", systemImage: "xmark.octagon.fill")
                        .font(.callout).foregroundStyle(.red)
                }
            } else {
                Text("TapMesh couldn't read the individual steps from this result. Open Full result to see everything the service returned.").font(.callout).foregroundStyle(.secondary)
            }
            if let bundleID = report.bundleID {
                LabeledContent("App") { Text(verbatim: bundleID).textSelection(.enabled).environment(\.layoutDirection, .leftToRight) }
            }
            if report.logArtifactID != nil {
                Text("The build log is in Saved Files.").font(.callout).foregroundStyle(.secondary)
            }
            if let raw = report.raw {
                TechnicalDetails(title: "Full result") {
                    if let code = report.errorCode { DetailRow(label: "Error code", value: code) }
                    Text(verbatim: raw.prettyPrinted).font(.caption.monospaced()).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading).environment(\.layoutDirection, .leftToRight)
                }
            }
        }
    }
    private func stageRow(_ stage: BuildStage) -> some View {
        Label {
            HStack(spacing: 6) {
                Text(DisplayText.toolName(stage.name)).font(.callout.weight(.medium))
                Text(stage.succeeded ? String(localized: "Succeeded") : stage.failed ? String(localized: "Failed") : DisplayText.toolName(stage.status)).font(.callout).foregroundStyle(.secondary)
            }
        } icon: {
            Image(systemName: stage.succeeded ? "checkmark.circle.fill" : stage.failed ? "xmark.octagon.fill" : "circle")
                .foregroundStyle(stage.succeeded ? .green : stage.failed ? .red : .secondary)
        }
    }
}
