import Accessibility
import AppKit
import SwiftUI
import TapMeshCore

struct AgentSetupView: View {
    private let launcherPath = AgentSetup.launcherPath(bundleURL: Bundle.main.bundleURL)

    var body: some View {
        Form {
            if FileManager.default.isExecutableFile(atPath: launcherPath) {
                Section("Copy the configuration") {
                    AgentSetupSnippet(title: "MCP configuration", copyLabel: "Copy MCP configuration",
                                      code: AgentSetup.mcpConfigJSON(launcherPath: launcherPath))
                    AgentSetupSnippet(title: "Claude Code command", copyLabel: "Copy Claude Code command",
                                      code: AgentSetup.claudeCodeCommand(launcherPath: launcherPath))
                }
                Section("Add it to your AI app") {
                    Text("Paste the JSON into your app's MCP settings, or run the Claude Code command in Terminal.")
                    Text("No key is needed.").foregroundStyle(.secondary)
                }
                Section("Ask the agent to list devices") {
                    Text("Then ask it to select the device you want to use.")
                    Text("An agent can't approve its own requests. On an iPhone or iPad you decide once whether agents may control it.")
                        .foregroundStyle(.secondary)
                }
            } else {
                Section("Connect an AI Agent…") {
                    Text("Install the complete TapMesh app to get the MCP launcher.")
                }
            }
        }
        .formStyle(.grouped)
    }
}

private struct AgentSetupSnippet: View {
    private enum CopyFeedback: Equatable { case copied(UUID), failed }

    let title: LocalizedStringKey
    let copyLabel: LocalizedStringKey
    let code: String
    @State private var feedback: CopyFeedback?

    private var copied: Bool {
        if case .copied? = feedback { return true }
        return false
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 12) {
                Text(title).font(.callout.weight(.semibold))
                Spacer()
                Button(action: copy) {
                    if copied {
                        Label("Copied", systemImage: "checkmark").frame(minWidth: 70)
                    } else {
                        Label("Copy", systemImage: "doc.on.doc").frame(minWidth: 70)
                    }
                }
                .accessibilityLabel(copyLabel)
                .accessibilityValue(copied ? Text("Copied") : Text(""))
            }
            ScrollView(.horizontal) {
                Text(verbatim: code)
                    .font(.system(.callout, design: .monospaced))
                    .fixedSize(horizontal: true, vertical: true)
                    .textSelection(.enabled)
                    .padding(10)
            }
            .environment(\.layoutDirection, .leftToRight)
            .background(Color(nsColor: .textBackgroundColor), in: .rect(cornerRadius: 6))
            if feedback == .failed {
                Text("Couldn't copy. Select the text and press Command-C.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 4)
        .task(id: feedback) {
            guard case .copied? = feedback else { return }
            do { try await Task.sleep(for: .seconds(2)) } catch { return }
            feedback = nil
        }
    }

    private func copy() {
        let clipboard = NSPasteboard.general
        clipboard.clearContents()
        if clipboard.setString(code, forType: .string) {
            feedback = .copied(UUID())
            AccessibilityNotification.Announcement(String(localized: "Copied")).post()
        } else {
            feedback = .failed
            AccessibilityNotification.Announcement(String(localized: "Couldn't copy. Select the text and press Command-C.")).post()
        }
    }
}
