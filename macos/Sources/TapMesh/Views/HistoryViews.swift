import SwiftUI
import TapMeshCore

struct ActivityView: View {
    @ObservedObject var store: WorkspaceStore
    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "Activity", subtitle: "Recent actions from this local controller. Arguments and typed text are not logged.", icon: "clock.arrow.circlepath") {
                Button("Clear") { store.activity = [] }.disabled(store.activity.isEmpty)
            }
            Divider()
            if store.activity.isEmpty { ContentUnavailableView("No recent activity", systemImage: "clock", description: Text("Device actions appear here with their request identifiers and delivery errors.")).frame(maxHeight: .infinity) }
            else {
                List(store.activity) { entry in
                    HStack(alignment: .top, spacing: 14) {
                        Image(systemName: entry.status == "error" || entry.status == "transport error" ? "exclamationmark.circle" : "checkmark.circle").foregroundStyle(entry.status == "error" || entry.status == "transport error" ? .orange : .secondary).padding(.top, 2)
                        VStack(alignment: .leading, spacing: 5) {
                            HStack { Text(entry.tool).font(.headline); Text(entry.status).font(.caption).foregroundStyle(.secondary); Spacer(); Text(entry.date, style: .time).font(.caption).foregroundStyle(.secondary) }
                            Text(entry.message).font(.callout).foregroundStyle(.secondary)
                            if !entry.requestID.isEmpty { Text(entry.requestID).font(.caption.monospaced()).foregroundStyle(.tertiary).textSelection(.enabled) }
                        }
                    }.padding(.vertical, 8)
                }.listStyle(.inset)
            }
        }
    }
}

struct ArtifactsView: View {
    @ObservedObject var store: WorkspaceStore
    @State private var selected: String?
    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "Artifacts", subtitle: "Screenshots, recordings, logs, and exports created in this session.", icon: "photo.stack") { EmptyView() }
            Divider()
            if store.artifacts.isEmpty {
                ContentUnavailableView("Your captures appear here", systemImage: "photo.stack", description: Text("Save a screenshot, finish a recording, or run an export. Files remain in the managed runtime until you save a copy.")).frame(maxHeight: .infinity)
            } else {
                HSplitView {
                    List(store.artifacts, selection: $selected) { artifact in
                        HStack(spacing: 12) {
                            Image(systemName: artifact.kind.contains("video") || artifact.kind.contains("record") ? "video" : artifact.kind.contains("image") || artifact.kind.contains("screenshot") ? "photo" : "doc").font(.title2).foregroundStyle(.secondary)
                            VStack(alignment: .leading, spacing: 4) { Text(artifact.filename).lineLimit(1); Text("\(artifact.kind) · \(artifact.state)").font(.caption).foregroundStyle(.secondary) }
                            Spacer()
                            Button { Task { await store.saveArtifact(artifact) } } label: { Image(systemName: "square.and.arrow.down") }.buttonStyle(.borderless).help("Save artifact to your Mac")
                        }.padding(.vertical, 7).tag(artifact.id)
                    }.listStyle(.inset).frame(minWidth: 350)
                    if let artifact = store.artifacts.first(where: { $0.id == selected }) {
                        ScrollView { VStack(alignment: .leading, spacing: 18) { Text("Artifact details").font(.headline); JSONResultView(value: artifact.metadata); Button("Save a Copy…") { Task { await store.saveArtifact(artifact) } } }.padding(24) }.frame(minWidth: 260, idealWidth: 340)
                    }
                }
            }
        }
    }
}

struct ApprovalsView: View {
    @ObservedObject var store: WorkspaceStore
    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "Approvals & Handoffs", subtitle: "Review the exact requesting controller, device, and action before allowing it.", icon: "hand.raised") {
                Button("Refresh") { Task { await store.refreshApprovals() } }.disabled(!store.online)
            }
            Divider()
            if let issue = store.approvalIssue { IssueBanner(message: issue) { store.approvalIssue = nil } }
            if store.approvals.isEmpty { ContentUnavailableView("Nothing needs your approval", systemImage: "checkmark.shield", description: Text("Requests from other controllers and steps requiring human attention appear here. Approval is bound to one action and connection.")).frame(maxHeight: .infinity) }
            else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 18) {
                        ForEach(Array(store.approvals.enumerated()), id: \.offset) { _, approval in
                            GroupBox {
                                VStack(alignment: .leading, spacing: 14) {
                                    HStack { Label(approval["tool"]?.stringValue ?? "Requested action", systemImage: approval["policy"]?.stringValue == "U" ? "person.crop.circle.badge.exclamationmark" : "lock.shield").font(.headline); Spacer(); Text("Policy \(approval["policy"]?.scalarDescription ?? "unknown")").font(.caption).foregroundStyle(.secondary) }
                                    Text("Device: \(approval["device_id"]?.stringValue ?? "No device")\nController: \(approval["session_id"]?.stringValue ?? "Unknown")\nConnection: \(approval["connection_epoch"]?.scalarDescription ?? "Unknown")").font(.callout.monospaced()).textSelection(.enabled)
                                    if let summary = approval["argument_summary"] { JSONResultView(value: summary) }
                                    if approval["policy"]?.stringValue == "U" { Text("Complete the requested protected step on the device yourself. Allow only when you have finished. Do not enter account secrets into tool arguments.").font(.callout).foregroundStyle(.secondary) }
                                    if let digest = approval["digest"]?.stringValue { Text("Action digest \(digest)").font(.caption2.monospaced()).foregroundStyle(.tertiary).textSelection(.enabled) }
                                    HStack { Spacer(); Button("Deny", role: .destructive) { Task { await store.decide(approval, allow: false) } }; Button(approval["policy"]?.stringValue == "U" ? "I Completed This Step" : "Allow Once") { Task { await store.decide(approval, allow: true) } }.buttonStyle(.borderedProminent) }.disabled(!store.online)
                                }.padding(14).frame(maxWidth: .infinity, alignment: .leading)
                            }
                        }
                    }.padding(24)
                }
            }
        }
    }
}

struct JSONResultView: View {
    let value: JSONValue
    var body: some View { Text(displayValue(value).prettyPrinted.prefix(100_000)).font(.caption.monospaced()).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }
    private func displayValue(_ value: JSONValue) -> JSONValue {
        switch value {
        case .object(let object):
            return .object(object.mapValues { item in
                if let text = item.stringValue, text.utf8.count > 40_000 { return .string("[Large value omitted from the inspector]") }
                return displayValue(item)
            })
        case .array(let array): return .array(array.map(displayValue))
        default: return value
        }
    }
}
