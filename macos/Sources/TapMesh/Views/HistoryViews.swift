import QuickLook
import SwiftUI
import TapMeshCore

struct ActivityView: View {
    @ObservedObject var store: WorkspaceStore
    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "Activity", subtitle: "Recent actions on your devices from this Mac. What was typed and other action details are not logged.", icon: "clock.arrow.circlepath") {
                Button("Clear") { store.activity = [] }.disabled(store.activity.isEmpty)
            }
            Divider()
            if store.activity.isEmpty { ContentUnavailableView("No recent activity", systemImage: "clock", description: Text("Actions on your devices appear here, including any that failed.")).frame(maxHeight: .infinity) }
            else {
                List(store.activity) { entry in
                    let failed = entry.status == "error" || entry.status == "transport error"
                    HStack(alignment: .top, spacing: 14) {
                        Image(systemName: failed ? "exclamationmark.circle" : "checkmark.circle")
                            .foregroundStyle(failed ? .orange : .secondary)
                            .padding(.top, 2)
                            .accessibilityLabel(failed ? Text("Failed") : Text("Finished"))
                        VStack(alignment: .leading, spacing: 5) {
                            HStack {
                                Text(DisplayText.toolName(entry.tool)).font(.headline)
                                Text(Self.plainStatus(entry.status)).font(.caption).foregroundStyle(.secondary)
                                Spacer()
                                Text(entry.date, style: .time).font(.caption).foregroundStyle(.secondary)
                            }
                            if !entry.message.isEmpty { Text(entry.message).font(.callout).foregroundStyle(.secondary) }
                            if let delivery = entry.delivery {
                                // Whether the device may have changed is the part to act on; keep it visible.
                                Label(delivery, systemImage: entry.deliveryUncertain ? "exclamationmark.triangle.fill" : "info.circle")
                                    .font(.callout)
                                    .foregroundStyle(entry.deliveryUncertain ? AnyShapeStyle(.orange) : AnyShapeStyle(.secondary))
                            }
                            let reference = [entry.code.map { String(localized: "Error code \($0)") }, entry.requestID.isEmpty ? nil : String(localized: "Request ID \(entry.requestID)"), entry.technical].compactMap { $0 }
                            if !reference.isEmpty {
                                Text(reference.joined(separator: " · "))
                                    .font(.caption2)
                                    .foregroundStyle(.tertiary)
                                    .textSelection(.enabled)
                                    .help("Include this if you report a problem with this action.")
                            }
                        }
                    }.padding(.vertical, 8)
                }.listStyle(.inset)
            }
        }
    }

    /// The service's status word in plain terms; unknown statuses are shown readably, never dropped.
    static func plainStatus(_ status: String) -> String {
        switch status {
        case "ok", "success", "complete", "completed": String(localized: "Done")
        case "error": String(localized: "Failed")
        case "transport error": String(localized: "Couldn't reach TapMesh")
        default: DisplayText.toolName(status)
        }
    }
}

struct ArtifactsView: View {
    @ObservedObject var store: WorkspaceStore
    @State private var selected: String?
    @State private var confirmingClear = false
    @StateObject private var preview = ArtifactPreviewModel()
    private var selectedArtifact: ArtifactEntry? { store.artifacts.first { $0.id == selected } }
    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "Saved Files", subtitle: store.savedFileUsage?.summary ?? String(localized: "Screenshots, recordings, logs, and exports saved in TapMesh."), icon: "photo.stack") {
                Button("Delete All Saved Files…") { confirmingClear = true }
                    .disabled(!store.canClearSavedFiles)
            }
            Divider()
            if store.artifacts.isEmpty {
                ContentUnavailableView("Your captures appear here", systemImage: "photo.stack", description: Text("Save a screenshot, finish a recording, or run an export. Files stay inside TapMesh until you save a copy.")).frame(maxHeight: .infinity)
            } else {
                HSplitView {
                    List(store.artifacts, selection: $selected) { artifact in
                        HStack(spacing: 12) {
                            rowIcon(artifact)
                            VStack(alignment: .leading, spacing: 4) { Text(verbatim: artifact.filename).lineLimit(1); Text(verbatim: "\(DisplayText.toolName(artifact.kind)) · \(DisplayText.toolName(artifact.state))").font(.caption).foregroundStyle(.secondary) }
                            Spacer()
                            Button { save(artifact) } label: { Image(systemName: "square.and.arrow.down") }
                                .buttonStyle(.borderless)
                                .help("Save a copy to your Mac")
                                .accessibilityLabel("Save a copy of \(artifact.filename)")
                        }.padding(.vertical, 7).tag(artifact.id)
                    }
                    .listStyle(.inset)
                    .frame(minWidth: 350)
                    .onKeyPress(.space) {
                        guard let artifact = selectedArtifact else { return .ignored }
                        preview.quickLook(artifact, store: store)
                        return .handled
                    }
                    if let artifact = selectedArtifact {
                        ScrollView {
                            VStack(alignment: .leading, spacing: 18) {
                                Text(verbatim: artifact.filename).font(.headline).textSelection(.enabled)
                                ArtifactPreviewPane(artifact: artifact, model: preview, store: store)
                                if artifact.state == "partial" {
                                    Label("This recording stopped after an error, so part of it may be missing.", systemImage: "exclamationmark.triangle")
                                        .font(.callout).foregroundStyle(.secondary)
                                }
                                HStack(spacing: 10) {
                                    Button { preview.quickLook(artifact, store: store) } label: {
                                        if preview.quickLookLoading { ProgressView().controlSize(.small) } else { Text("Quick Look") }
                                    }
                                    .disabled(ArtifactPreviewPolicy.readiness(state: artifact.state) != .ready || preview.quickLookLoading)
                                    .help("Open in Quick Look (Space)")
                                    Button("Save a Copy…") { save(artifact) }
                                    if let saved = preview.savedLocation(for: artifact.id) {
                                        Button("Show in Finder") { preview.showInFinder(artifact.id) }
                                            .help("Show the copy you saved: \(saved.path)")
                                    }
                                }
                                artifactFacts(artifact)
                                TechnicalDetails { JSONResultView(value: artifact.metadata) }
                            }.padding(24)
                        }.frame(minWidth: 300, idealWidth: 420)
                    } else {
                        ContentUnavailableView("No artifact selected", systemImage: "doc.viewfinder", description: Text("Select a file in the list to preview it."))
                            .frame(minWidth: 300, idealWidth: 420, maxHeight: .infinity)
                    }
                }
            }
        }
        .quickLookPreview($preview.quickLookURL)
        .confirmationDialog("Delete all saved files?", isPresented: $confirmingClear, titleVisibility: .visible) {
            Button("Delete All Saved Files", role: .destructive) { Task { await clearFiles() } }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This deletes every screenshot, recording and export TapMesh saved on this Mac. Copies you exported elsewhere stay where you saved them.")
        }
        .task(id: store.online) { await store.refreshSavedFileUsage() }
        .onChange(of: store.artifacts.count) { _, _ in
            Task { await store.refreshSavedFileUsage() }
        }
        .onChange(of: selected, initial: true) { preview.show(selectedArtifact, store: store) }
        .onChange(of: store.online) { _, online in
            if online, case .offline = preview.phase { preview.show(selectedArtifact, store: store) }
        }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            // The person may have moved or deleted a saved copy in Finder meanwhile.
            preview.forgetMissingSavedCopies()
        }
        .onDisappear { preview.stop() }
    }

    private func clearFiles() async {
        guard await store.clearSavedFiles() else { return }
        selected = nil
        do { try await preview.clearSavedFilePreviews() }
        catch { store.issue = String(localized: "Some temporary previews could not be removed. Quit TapMesh and try again.") }
    }

    private func save(_ artifact: ArtifactEntry) {
        Task {
            if let url = await store.saveArtifact(artifact) { preview.recordSaved(url, for: artifact.id) }
        }
    }

    /// A thumbnail for an image already fetched for preview; otherwise a symbol for its kind.
    @ViewBuilder private func rowIcon(_ artifact: ArtifactEntry) -> some View {
        if let thumbnail = preview.thumbnails[artifact.id] {
            Image(nsImage: thumbnail)
                .resizable()
                .scaledToFit()
                .overlay(Rectangle().strokeBorder(.separator))
                .frame(width: 28, height: 28)
                .accessibilityHidden(true)
        } else {
            Image(systemName: ArtifactSymbol.name(for: artifact))
                .font(.title2)
                .foregroundStyle(.secondary)
                .frame(width: 28, height: 28)
                .accessibilityHidden(true)
        }
    }

    /// Readable facts from the artifact record (agent/artifacts.py `finalize`). Each key is optional.
    private func artifactFacts(_ artifact: ArtifactEntry) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            LabeledContent("Kind", value: DisplayText.toolName(artifact.kind))
            LabeledContent("State", value: DisplayText.toolName(artifact.state))
            if let bytes = ArtifactPreviewPolicy.sizeBytes(in: artifact.metadata) {
                LabeledContent("Size", value: ArtifactPreviewPolicy.sizeText(bytes))
            }
            if let created = artifact.metadata["created_at"]?.stringValue {
                LabeledContent("Created", value: Self.createdText(created))
            }
            if let deviceID = artifact.metadata["device_id"]?.stringValue {
                LabeledContent("Device", value: store.devices.first { $0.id == deviceID }?.name ?? deviceID)
            }
        }
    }

    /// The agent writes Python `isoformat()` timestamps (microseconds, "+00:00"); show the raw text if it does not parse.
    private static func createdText(_ raw: String) -> String {
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let whole = ISO8601DateFormatter()
        let trimmed = raw.replacingOccurrences(of: #"\.\d+"#, with: "", options: .regularExpression)
        guard let date = fractional.date(from: raw) ?? whole.date(from: trimmed) else { return raw }
        return date.formatted(date: .abbreviated, time: .shortened)
    }
}

struct ApprovalsView: View {
    @ObservedObject var store: WorkspaceStore
    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "Approvals & Handoffs", subtitle: "Check what is being asked, and on which device, before you allow it.", icon: "hand.raised") {
                Button("Refresh") { Task { await store.refreshApprovals() } }.disabled(!store.online)
            }
            Divider()
            if let issue = store.approvalIssue { IssueBanner(message: issue) { store.approvalIssue = nil } }
            if store.approvals.isEmpty { ContentUnavailableView("Nothing needs your approval", systemImage: "checkmark.shield", description: Text("Requests from apps and AI agents appear here when they need your permission or need you to act on a device.")).frame(maxHeight: .infinity) }
            else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 18) {
                        ForEach(Array(store.approvals.enumerated()), id: \.offset) { _, approval in
                            approvalCard(approval)
                        }
                    }.padding(24)
                }
            }
        }
    }

    private func approvalCard(_ approval: JSONValue) -> some View {
        let handoff = approval["policy"]?.stringValue == "U"
        let deviceGrant = approval["policy"]?.stringValue == "A"
        let allow: LocalizedStringKey = deviceGrant ? "Allow" : handoff ? "I Completed This Step" : "Allow Once"
        return GroupBox {
            VStack(alignment: .leading, spacing: 14) {
                Label(deviceGrant ? DeviceGrantCopy.title(for: deviceName(approval)) : requestSentence(approval, handoff: handoff),
                      systemImage: handoff ? "person.crop.circle.badge.exclamationmark" : "lock.shield")
                    .font(.headline)
                if deviceGrant {
                    Text(DeviceGrantCopy.explanation).font(.callout).foregroundStyle(.secondary)
                } else {
                    if handoff {
                        Text("Complete the requested protected step on the device yourself. Allow only when you have finished. Do not enter account secrets into tool arguments.").font(.callout).foregroundStyle(.secondary)
                    } else {
                        Text("Allow it only if you expected this request. Allowing it permits this one action, once.").font(.callout).foregroundStyle(.secondary)
                    }
                    if let summary = approval["argument_summary"]?.objectValue, !summary.isEmpty {
                        Text("What it will do (some values are hidden for privacy)").font(.caption).foregroundStyle(.secondary)
                        VStack(alignment: .leading, spacing: 6) {
                            ForEach(summary.keys.sorted(), id: \.self) { key in
                                if let value = summary[key] {
                                    LabeledContent {
                                        Text(verbatim: argumentValue(value)).textSelection(.enabled)
                                    } label: {
                                        Text(DisplayText.toolName(key))
                                    }
                                }
                            }
                        }
                        .font(.callout)
                    }
                    TechnicalDetails(title: "Request details") { approvalDetails(approval) }
                }
                HStack {
                    Spacer()
                    Button("Deny", role: .destructive) { Task { await store.decide(approval, allow: false) } }
                    Button(allow) { Task { await store.decide(approval, allow: true) } }
                        .buttonStyle(.borderedProminent)
                }
                .disabled(!store.online || (deviceGrant && !store.canChangeAgentControl))
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func argumentValue(_ value: JSONValue) -> String {
        switch value {
        case .string("[redacted]"), .string("[structured]"), .object, .array, .null:
            String(localized: "Hidden")
        case let .bool(flag):
            flag ? String(localized: "Yes") : String(localized: "No")
        case .string, .number:
            value.scalarDescription
        }
    }

    /// One plain sentence: what is asked, and on which device.
    private func requestSentence(_ approval: JSONValue, handoff: Bool) -> String {
        let device = deviceName(approval)
        let tool = approval["tool"]?.stringValue.map(DisplayText.toolName)
        // Whole sentences per case, so each language can order the words its own way.
        switch (handoff, tool, device) {
        case (true, let tool?, let device?): return String(localized: "You need to finish “\(tool)” yourself on \(device).")
        case (true, let tool?, nil): return String(localized: "You need to finish “\(tool)” yourself on the device.")
        case (true, nil, let device?): return String(localized: "You need to finish a protected step yourself on \(device).")
        case (true, nil, nil): return String(localized: "You need to finish a protected step yourself on the device.")
        case (false, let tool?, let device?): return String(localized: "Another app or AI client asks to run “\(tool)” on \(device).")
        case (false, let tool?, nil): return String(localized: "Another app or AI client asks to run “\(tool)”.")
        case (false, nil, let device?): return String(localized: "Another app or AI client asks to run an action on \(device).")
        case (false, nil, nil): return String(localized: "Another app or AI client asks to run an action.")
        }
    }

    /// The device's display name when it is known, otherwise its identifier.
    private func deviceName(_ approval: JSONValue) -> String? {
        guard let id = approval["device_id"]?.stringValue else { return nil }
        return store.devices.first { $0.id == id }?.name ?? id
    }

    /// Everything the approval record carries, unchanged and selectable.
    @ViewBuilder private func approvalDetails(_ approval: JSONValue) -> some View {
        DetailRow(label: "Action", value: approval["tool"]?.stringValue ?? String(localized: "Unknown"))
        DetailRow(label: "Device ID", value: approval["device_id"]?.stringValue ?? String(localized: "No device"))
        DetailRow(label: "Controller", value: approval["session_id"]?.stringValue ?? String(localized: "Unknown"))
        DetailRow(label: "Connection", value: approval["connection_epoch"]?.scalarDescription ?? String(localized: "Unknown"))
        DetailRow(label: "Policy", value: Self.policyText(approval["policy"]))
        if let digest = approval["digest"]?.stringValue { DetailRow(label: "Action digest", value: digest) }
    }

    /// Policy letter plus its meaning from docs/TOOL_CONTRACTS.md; unknown letters are shown as-is.
    private static func policyText(_ value: JSONValue?) -> String {
        guard let raw = value?.scalarDescription else { return String(localized: "unknown") }
        let meanings: [String: String] = [
            "O": String(localized: "observe only"),
            "C": String(localized: "ordinary control"),
            "S": String(localized: "sensitive"),
            "D": String(localized: "destructive"),
            "H": String(localized: "runs a command on this Mac"),
            "U": String(localized: "you must do this step on the device"),
        ]
        return meanings[raw].map { "\(raw) (\($0))" } ?? raw
    }
}

struct JSONResultView: View {
    let value: JSONValue
    // JSON stays in English and left-to-right in every language.
    var body: some View {
        Text(verbatim: String(displayValue(value).prettyPrinted.prefix(100_000)))
            .font(.caption.monospaced()).textSelection(.enabled)
            .frame(maxWidth: .infinity, alignment: .leading)
            .environment(\.layoutDirection, .leftToRight)
    }
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
