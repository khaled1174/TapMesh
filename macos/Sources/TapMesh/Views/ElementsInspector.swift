import SwiftUI
import AppKit
import TapMeshCore

struct ElementsInspector: View {
    @ObservedObject var store: WorkspaceStore
    /// Owned by DeviceWorkspaceView, which outlines this element (by `ref`) on the screen image.
    @Binding var selectedRef: String?
    @Binding var hoveredRef: String?
    @State private var tab = "Elements"
    @State private var search = ""
    @State private var textTarget: TextEntryTarget?
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Picker("Inspector", selection: $tab) { Text("Elements").tag("Elements"); Text("Capabilities").tag("Capabilities") }.pickerStyle(.segmented).padding(14)
            if tab == "Elements" {
                HStack {
                    Text("Screen elements").font(.headline)
                    Spacer()
                    Button { Task { await store.inspect() } } label: { Image(systemName: "arrow.clockwise") }
                        .help("Refresh element snapshot")
                        .accessibilityLabel("Refresh elements")
                        .disabled(!store.canControl)
                }.padding(.horizontal, 14).padding(.bottom, 10)
                TextField("Filter elements", text: $search).textFieldStyle(.roundedBorder).padding(.horizontal, 14).padding(.bottom, 12)
                Divider()
                if store.elements.isEmpty {
                    VStack(alignment: .leading, spacing: 12) {
                        Text(store.inspectorMessage).foregroundStyle(.secondary)
                        Button("Inspect Screen") { Task { await store.inspect() } }.disabled(!store.canControl)
                        Text("Elements found through Accessibility and through text recognition (OCR) are labeled separately. Text recognition finds words on screen but can’t tell what kind of control they belong to.").font(.caption).foregroundStyle(.secondary)
                    }.padding(16)
                    Spacer()
                } else {
                    List(selection: $selectedRef) { ForEach(Array(filtered.enumerated()), id: \.offset) { _, element in
                        let name = Self.displayName(element)
                        let ref = element["ref"]?.stringValue
                        VStack(alignment: .leading, spacing: 6) {
                            HStack(alignment: .firstTextBaseline) {
                                Text(verbatim: name).font(.callout.weight(.medium))
                                Spacer()
                                Button { Task { await store.tapElement(element) } } label: { Image(systemName: "hand.tap") }
                                    .buttonStyle(.borderless)
                                    .help("Tap this element on the device")
                                    .accessibilityLabel("Tap \(name)")
                                    .disabled(!store.canControl || store.elementObservation?.snapshotID == nil)
                            }
                            HStack(spacing: 6) {
                                Self.sourceLabel(element["source"]?.stringValue)
                                Text("·").accessibilityHidden(true)
                                Text(Self.roleText(element["role"]?.stringValue))
                            }.font(.caption).foregroundStyle(.secondary)
                            if let value = element["value"]?.stringValue, !value.isEmpty { Text(value).font(.caption).lineLimit(2).foregroundStyle(.secondary) }
                        }.padding(.vertical, 5)
                        .help(Self.technicalSummary(element))
                        .contextMenu {
                            Button("Replace Text…") {
                                if let snapshot = store.elementObservation, let snapshotID = snapshot.snapshotID,
                                   let deviceID = snapshot.deviceID, let epoch = snapshot.epoch, let ref = element["ref"]?.stringValue {
                                    textTarget = TextEntryTarget(deviceID: deviceID, epoch: epoch, label: element["label"]?.stringValue ?? String(localized: "Field"), target: .object(["ref": .string(ref), "snapshot_id": .string(snapshotID)]))
                                }
                            }.disabled(!store.canControl || store.elementObservation?.snapshotID == nil)
                            Button("Copy Reference") {
                                if let ref = element["ref"]?.stringValue {
                                    NSPasteboard.general.clearContents()
                                    NSPasteboard.general.setString(ref, forType: .string)
                                }
                            }.disabled(element["ref"]?.stringValue == nil)
                        }
                        .tag(ref)
                        .onHover { inside in
                            if inside { hoveredRef = ref } else if hoveredRef == ref { hoveredRef = nil }
                        }
                    } }.listStyle(.plain)
                    .onDisappear { hoveredRef = nil }
                    Text(store.inspectorMessage).font(.caption).foregroundStyle(.secondary).padding(12)
                }
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        if let device = store.selectedDevice {
                            ForEach(device.capabilities.keys.sorted(), id: \.self) { key in
                                if let capability = device.capabilities[key] {
                                    VStack(alignment: .leading, spacing: 5) {
                                        HStack {
                                            Text(Self.capabilityName(key)).font(.callout.weight(.medium))
                                            Spacer()
                                            Image(systemName: capability.status == "supported" ? "checkmark.circle.fill" : "info.circle")
                                                .foregroundStyle(capability.status == "supported" ? .green : .secondary)
                                                .accessibilityHidden(true)
                                        }
                                        Text(DisplayText.toolName(capability.status)).font(.caption).foregroundStyle(.secondary)
                                        Text(capability.reason).font(.caption).foregroundStyle(.secondary)
                                        TechnicalDetails {
                                            DetailRow(label: "Key", value: key)
                                            if let limits = capability.limits { Text(verbatim: limits.prettyPrinted).font(.caption2.monospaced()).foregroundStyle(.tertiary).textSelection(.enabled).environment(\.layoutDirection, .leftToRight) }
                                        }
                                    }
                                }
                            }
                            TechnicalDetails(title: "Connection details") { DetailRow(label: "Connection", value: device.epoch) }
                        } else { Text("Select a device to see what TapMesh can do with it.").foregroundStyle(.secondary) }
                    }.padding(16).frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }.sheet(item: $textTarget) { target in KeyboardSheet(store: store, fillTarget: target) }
    }
    /// Same choice as before (label, then identifier), but an empty label no longer shows a blank row.
    private static func displayName(_ element: JSONValue) -> String {
        if let label = element["label"]?.stringValue, !label.isEmpty { return label }
        if let identifier = element["identifier"]?.stringValue, !identifier.isEmpty { return identifier }
        return String(localized: "Unlabeled element")
    }

    /// Accessibility and text recognition stay visibly distinct: different words and different symbols.
    @ViewBuilder private static func sourceLabel(_ source: String?) -> some View {
        switch source ?? "" {
        case "accessibility", "ax": Label("Accessibility", systemImage: "accessibility")
        case "ocr": Label("Text recognition (OCR)", systemImage: "text.viewfinder")
        case "": Label("Unknown source", systemImage: "questionmark.circle")
        default: Label(DisplayText.toolName(source ?? ""), systemImage: "questionmark.circle")
        }
    }

    /// "AXStaticText" or "static_text" becomes "Static text".
    private static func roleText(_ role: String?) -> String {
        guard var raw = role, !raw.isEmpty else { return String(localized: "Role not available") }
        if raw == "unknown" || raw == "unclassified" { return String(localized: "Unknown role") }
        if raw.hasPrefix("AX") { raw.removeFirst(2) }
        var words = ""
        for character in raw {
            if character.isUppercase, let last = words.last, last.isLowercase { words.append(" ") }
            words.append(character)
        }
        return words.isEmpty ? String(localized: "Unknown role") : DisplayText.toolName(words.lowercased())
    }

    /// "screen.record" becomes "Screen record"; the raw key stays in Details.
    private static func capabilityName(_ key: String) -> String {
        DisplayText.toolName(key.replacingOccurrences(of: ".", with: " "))
    }

    /// The raw source, role and reference, available on hover.
    private static func technicalSummary(_ element: JSONValue) -> String {
        // The raw values stay as the service sent them; only the labels are translated.
        let source = element["source"]?.stringValue ?? "unknown", role = element["role"]?.stringValue ?? "unclassified", ref = element["ref"]?.stringValue ?? "none"
        return String(localized: "Source: \(source)\nRole: \(role)\nReference: \(ref)")
    }

    private var filtered: [JSONValue] { store.elements.filter { search.isEmpty || ($0["label"]?.stringValue ?? "").localizedCaseInsensitiveContains(search) || ($0["role"]?.stringValue ?? "").localizedCaseInsensitiveContains(search) } }
}
