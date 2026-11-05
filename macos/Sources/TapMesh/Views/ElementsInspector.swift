import SwiftUI
import TapMeshCore

struct ElementsInspector: View {
    @ObservedObject var store: WorkspaceStore
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
                    Button { Task { await store.inspect() } } label: { Image(systemName: "arrow.clockwise") }.help("Refresh element snapshot").disabled(!store.canControl)
                }.padding(.horizontal, 14).padding(.bottom, 10)
                TextField("Filter elements", text: $search).textFieldStyle(.roundedBorder).padding(.horizontal, 14).padding(.bottom, 12)
                Divider()
                if store.elements.isEmpty {
                    VStack(alignment: .leading, spacing: 12) {
                        Text(store.inspectorMessage).foregroundStyle(.secondary)
                        Button("Inspect Screen") { Task { await store.inspect() } }.disabled(!store.canControl)
                        Text("Accessibility data and OCR are labeled separately. OCR does not supply native accessibility roles.").font(.caption).foregroundStyle(.secondary)
                    }.padding(16)
                    Spacer()
                } else {
                    List(Array(filtered.enumerated()), id: \.offset) { _, element in
                        VStack(alignment: .leading, spacing: 6) {
                            HStack(alignment: .firstTextBaseline) {
                                Text(element["label"]?.stringValue ?? element["identifier"]?.stringValue ?? "Unlabeled element").font(.callout.weight(.medium))
                                Spacer()
                                Button { Task { await store.tapElement(element) } } label: { Image(systemName: "hand.tap") }.buttonStyle(.borderless).help("Tap element using its snapshot reference").disabled(!store.canControl || store.elementObservation?.snapshotID == nil)
                            }
                            Text("\(element["source"]?.stringValue ?? "unknown") · \(element["role"]?.stringValue ?? "unclassified")").font(.caption).foregroundStyle(.secondary)
                            if let value = element["value"]?.stringValue, !value.isEmpty { Text(value).font(.caption).lineLimit(2).foregroundStyle(.secondary) }
                            if let ref = element["ref"]?.stringValue { Text(ref).font(.caption2.monospaced()).foregroundStyle(.tertiary).textSelection(.enabled) }
                        }.padding(.vertical, 5)
                        .contextMenu {
                            Button("Replace Text…") {
                                if let snapshot = store.elementObservation, let snapshotID = snapshot.snapshotID,
                                   let deviceID = snapshot.deviceID, let epoch = snapshot.epoch, let ref = element["ref"]?.stringValue {
                                    textTarget = TextEntryTarget(deviceID: deviceID, epoch: epoch, label: element["label"]?.stringValue ?? "Field", target: .object(["ref": .string(ref), "snapshot_id": .string(snapshotID)]))
                                }
                            }.disabled(!store.canControl || store.elementObservation?.snapshotID == nil)
                        }
                    }.listStyle(.plain)
                    Text(store.inspectorMessage).font(.caption).foregroundStyle(.secondary).padding(12)
                }
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        if let device = store.selectedDevice {
                            Text("Connection \(device.epoch)").font(.caption.monospaced()).textSelection(.enabled)
                            ForEach(device.capabilities.keys.sorted(), id: \.self) { key in
                                if let capability = device.capabilities[key] {
                                    VStack(alignment: .leading, spacing: 5) {
                                        HStack { Text(key).font(.callout.weight(.medium)); Spacer(); Image(systemName: capability.status == "supported" ? "checkmark.circle.fill" : "info.circle").foregroundStyle(capability.status == "supported" ? .green : .secondary) }
                                        Text(capability.status.replacingOccurrences(of: "_", with: " ").capitalized).font(.caption).foregroundStyle(.secondary)
                                        Text(capability.reason).font(.caption).foregroundStyle(.secondary)
                                        if let limits = capability.limits { Text(limits.prettyPrinted).font(.caption2.monospaced()).foregroundStyle(.tertiary).textSelection(.enabled) }
                                    }
                                }
                            }
                        } else { Text("Select a device to view its qualified capabilities.").foregroundStyle(.secondary) }
                    }.padding(16).frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }.sheet(item: $textTarget) { target in KeyboardSheet(store: store, fillTarget: target) }
    }
    private var filtered: [JSONValue] { store.elements.filter { search.isEmpty || ($0["label"]?.stringValue ?? "").localizedCaseInsensitiveContains(search) || ($0["role"]?.stringValue ?? "").localizedCaseInsensitiveContains(search) } }
}
