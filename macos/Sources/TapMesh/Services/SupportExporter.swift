import AppKit
import Foundation
import TapMeshCore
import UniformTypeIdentifiers

@MainActor
enum SupportExporter {
    static func export(store: WorkspaceStore) async {
        let panel = NSSavePanel()
        panel.title = "Export TapMesh Diagnostics"
        panel.message = "Save app versions, service status, counts, and recent diagnostic references as a local JSON file."
        panel.nameFieldStringValue = "TapMesh-Diagnostics.json"
        panel.allowedContentTypes = [.json]
        panel.allowsOtherFileTypes = false
        panel.canCreateDirectories = true
        guard await panel.begin() == .OK, let destination = panel.url else { return }

        let appVersion = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "unknown"
        let appBuild = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "unknown"
        let os = ProcessInfo.processInfo.operatingSystemVersion
        let osVersion = "\(os.majorVersion).\(os.minorVersion).\(os.patchVersion)"
        #if arch(arm64)
        let architecture = "arm64"
        #elseif arch(x86_64)
        let architecture = "x86_64"
        #else
        let architecture = "unknown"
        #endif
        let serverHealth = store.health, agentHealth = store.agentHealth
        let counts = ["devices": store.devices.count, "ready_devices": store.devices.filter(\.ready).count,
                      "tools": store.tools.count, "pending_approvals": store.approvals.count,
                      "session_artifacts": store.artifacts.count, "session_activity": store.activity.count]
        // Derive only the fixed log directory. Never read connection.json or its
        // credential paths while preparing a support export.
        let logs = URL(fileURLWithPath: store.connectionFilePath).deletingLastPathComponent()
            .appendingPathComponent("agent-state/logs", isDirectory: true)
        do {
            try await Task.detached(priority: .utility) {
                let events = try? SupportReport.recentDiagnostics(in: logs)
                let report = SupportReport(appVersion: appVersion, appBuild: appBuild, operatingSystemVersion: osVersion,
                                           architecture: architecture, serverHealth: serverHealth, agentHealth: agentHealth,
                                           counts: counts, diagnosticEvents: events ?? [], diagnosticsAvailable: events != nil)
                try report.write(to: destination)
            }.value
            store.lastAction = "Diagnostic report saved."
        } catch {
            store.issue = "TapMesh could not save the diagnostic report. Choose a local JSON file and try again."
        }
    }
}
