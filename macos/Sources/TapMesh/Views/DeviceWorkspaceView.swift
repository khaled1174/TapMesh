import SwiftUI
import TapMeshCore

struct DeviceWorkspaceView: View {
    @ObservedObject var store: WorkspaceStore
    @State private var keyboardSheet = false
    @State private var showInspector = true
    var body: some View {
        VStack(spacing: 0) {
            DeviceHeader(store: store)
            Divider()
            HStack(spacing: 12) {
                action("Home", "house", tool: "press_button", arguments: ["button": .string("home")])
                action("Back", "chevron.backward", tool: "go_back")
                Menu { ForEach(["portrait", "landscape_left", "landscape_right", "portrait_upside_down"], id: \.self) { value in
                    Button(value.replacingOccurrences(of: "_", with: " ").capitalized) { Task { await store.run("rotate", arguments: ["orientation": .string(value)]); await store.capture() } }
                } } label: { Label("Rotate", systemImage: "rotate.right") }.disabled(!store.canControl)
                Menu { ForEach(["light", "dark", "system"], id: \.self) { value in Button(value.capitalized) { Task { await store.run("set_appearance", arguments: ["appearance": .string(value)]); await store.capture() } } } } label: { Label("Appearance", systemImage: "circle.lefthalf.filled") }.disabled(!store.canControl)
                Button { keyboardSheet = true } label: { Label("Type", systemImage: "keyboard") }.disabled(!store.canControl)
                Spacer(minLength: 0)
                Button { showInspector.toggle() } label: { Label("Inspector", systemImage: "sidebar.right") }.help("Show or hide the element inspector")
            }.labelStyle(.iconOnly).buttonStyle(.borderless).controlSize(.large).padding(.horizontal, 18).padding(.vertical, 10)
            Divider()
            HSplitView {
                VStack(spacing: 0) {
                    PreviewCanvas(store: store).frame(minWidth: 340, maxWidth: .infinity, maxHeight: .infinity)
                    Divider()
                    HStack(spacing: 14) {
                        Toggle("Live", isOn: $store.liveEnabled).toggleStyle(.switch).controlSize(.small).onChange(of: store.liveEnabled) { _, _ in store.updatePolling() }.help("At most one screenshot per second; pauses when hidden or inactive")
                        Button { Task { await store.capture() } } label: { Label("Capture", systemImage: "camera") }.disabled(!store.canControl)
                        Button { Task { await store.run("save_screenshot", arguments: ["format": .string("png"), "full_resolution": .bool(true)]) } } label: { Label("Save", systemImage: "square.and.arrow.down") }.disabled(!store.canControl)
                        Button { Task { await store.toggleRecording() } } label: { Label(store.recordingID == nil ? "Record" : store.stoppingRecording ? "Stopping…" : "Stop Recording", systemImage: store.recordingID == nil ? "record.circle" : "stop.circle.fill") }.tint(store.recordingID == nil ? nil : .red).disabled(!store.online || store.stoppingRecording || (store.recordingID == nil && !store.canControl))
                        Spacer(minLength: 0)
                    }.buttonStyle(.borderless).font(.callout).padding(14)
                }
                if showInspector { ElementsInspector(store: store).frame(minWidth: 250, idealWidth: 290, maxWidth: 380) }
            }
        }
        .sheet(isPresented: $keyboardSheet) { KeyboardSheet(store: store) }
    }
    private func action(_ title: String, _ image: String, tool: String, arguments: [String: JSONValue] = [:]) -> some View {
        Button { Task { await store.run(tool, arguments: arguments); await store.capture() } } label: { Label(title, systemImage: image) }.help(title).disabled(!store.canControl)
    }
}

struct DeviceHeader: View {
    @ObservedObject var store: WorkspaceStore
    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "iphone.gen3").font(.title2).foregroundStyle(.secondary).accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 4) {
                Text(store.selectedDevice?.name ?? "Select a device").font(.title3.weight(.semibold))
                Text(store.selectedDevice.map { "\($0.backend.capitalized) · \($0.ready ? "Ready" : "Not ready")" } ?? "No active target").font(.caption).foregroundStyle(.secondary)
                if let receipt = store.controlReceipt {
                    Text("Local takeover · epoch \(receipt.connectionEpoch) · generation \(receipt.ownershipGeneration) · \(receipt.cancelledRequests) interrupted").font(.caption).foregroundStyle(.secondary)
                }
            }
            Spacer()
            if let id = store.recordingID { Label("Recording", systemImage: "record.circle.fill").foregroundStyle(.red).help("Recording \(id) on \(store.recordingDeviceID ?? "device")").font(.callout) }
            if let device = store.selectedDevice {
                Text(device.id).font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled)
            }
        }.padding(18)
    }
}

struct PreviewCanvas: View {
    @ObservedObject var store: WorkspaceStore
    var body: some View {
        ZStack {
            Rectangle().fill(.quaternary.opacity(0.3))
            if let image = store.image, let observation = store.observation {
                VStack(spacing: 16) {
                    GeometryReader { proxy in
                        Image(nsImage: image).resizable().interpolation(.high).aspectRatio(contentMode: .fit)
                            .frame(width: proxy.size.width, height: proxy.size.height)
                            .contentShape(Rectangle())
                            .gesture(SpatialTapGesture().onEnded { event in
                                guard let point = observation.geometry?.devicePoint(at: .init(x: event.location.x, y: event.location.y), in: .init(width: proxy.size.width, height: proxy.size.height)) else { return }
                                Task { await store.tap(point, observation: observation) }
                            })
                            .gesture(DragGesture(minimumDistance: 14).onEnded { event in
                                guard let geometry = observation.geometry,
                                      let start = geometry.devicePoint(at: .init(x: event.startLocation.x, y: event.startLocation.y), in: .init(width: proxy.size.width, height: proxy.size.height)),
                                      let end = geometry.devicePoint(at: .init(x: event.location.x, y: event.location.y), in: .init(width: proxy.size.width, height: proxy.size.height)) else { return }
                                let target: (PlanePoint) -> JSONValue = { .object(["x": .number($0.x), "y": .number($0.y), "coordinate_space": .string("device_points"), "observation_id": .string(observation.id)]) }
                                Task { await store.run("swipe", arguments: ["start": target(start), "end": target(end), "duration_ms": .number(300)]); await store.capture() }
                            })
                            .allowsHitTesting(store.canControl && observation.geometry != nil)
                            .accessibilityLabel("Live screen of \(store.selectedDevice?.name ?? "selected device")")
                            .accessibilityHint("Use the inspector to activate accessible elements. Mouse users can click to tap and drag to swipe.")
                    }
                    Text(store.previewMessage).font(.caption).foregroundStyle(.secondary).multilineTextAlignment(.center)
                }.padding(24)
            } else {
                VStack(spacing: 14) {
                    Image(systemName: store.selectedDevice?.ready == false ? "iphone.slash" : "iphone.gen3").font(.system(size: 48, weight: .ultraLight)).foregroundStyle(.secondary).accessibilityHidden(true)
                    Text(store.selectedDevice?.ready == false ? "Device needs attention" : "Screen preview").font(.title2.weight(.medium))
                    Text(store.selectedDevice?.ready == false ? store.selectedDevice?.reasons.joined(separator: "\n") ?? "Check the device connection." : store.previewMessage).foregroundStyle(.secondary).multilineTextAlignment(.center).frame(maxWidth: 380)
                    if store.selectedDevice?.backend == "simulator", store.selectedDevice?.ready == false {
                        Button("Boot Simulator") { Task { await store.bootSelected() } }.buttonStyle(.borderedProminent).disabled(!store.online || store.busy)
                    } else if store.canControl { Button("Capture Screen") { Task { await store.capture() } }.buttonStyle(.borderedProminent) }
                }.padding(32)
            }
        }
    }
}

struct TextEntryTarget: Identifiable {
    let id = UUID()
    let deviceID: String
    let epoch: String
    let label: String
    let target: JSONValue
}

struct KeyboardSheet: View {
    @ObservedObject var store: WorkspaceStore
    var fillTarget: TextEntryTarget? = nil
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""
    @State private var method: TextInputMethod = .keyboard
    @State private var confirmedUSLayout = false
    @State private var confirmedOrdinaryField = false
    @State private var allowClockAndCaretChanges = false
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text(fillTarget.map { "Replace Text in \($0.label)" } ?? "Type on \(store.selectedDevice?.name ?? "Device")").font(.title2.weight(.semibold))
            Text(fillTarget == nil ? "Text is sent to the focused field. Tap a field on the device first." : "Replace the referenced field’s existing value. The request stays bound to this element snapshot and does not submit the form.").foregroundStyle(.secondary)
            Picker("Input method", selection: $method) {
                Text("Keyboard").tag(TextInputMethod.keyboard)
                Text("Clipboard").tag(TextInputMethod.clipboard)
            }.pickerStyle(.segmented)
            if method == .clipboard {
                Text("Paste replaces the device clipboard. Universal Clipboard may propagate this text to your Mac or other nearby Apple devices. Choosing Paste Text explicitly authorizes this method.").font(.callout).foregroundStyle(.secondary)
            } else if store.selectedDevice?.backend == "physical" {
                Text("Hardware keyboard input requires the US keyboard layout. For Unicode paste, choose Clipboard explicitly.").font(.callout).foregroundStyle(.secondary)
                Toggle("I confirmed the device uses the US keyboard layout", isOn: $confirmedUSLayout).toggleStyle(.checkbox)
            }
            if store.selectedDevice?.backend == "physical" {
                Toggle("This is an ordinary app field, not a device/account authentication prompt", isOn: $confirmedOrdinaryField).toggleStyle(.checkbox)
                Text("For device unlock, passwords, or account authentication, complete the step directly on the device. This confirmation does not override protected or uninspectable states.").font(.caption).foregroundStyle(.secondary)
                if fillTarget != nil {
                    Toggle("Allow clock, status icon and cursor blinking changes", isOn: $allowClockAndCaretChanges).toggleStyle(.checkbox)
                    Text("The field’s visible text and position must stay the same. Other screen changes stop replacement.").font(.caption).foregroundStyle(.secondary)
                }
            }
            TextEditor(text: $text).font(.body).frame(height: 150).border(.quaternary).accessibilityLabel("Text to type on device")
            if !fillTargetIsCurrent { Text("This field snapshot expired. Close this sheet and inspect the current screen to choose the field again.").font(.callout).foregroundStyle(.orange) }
            HStack {
                Button("Hide Device Keyboard") { Task { await store.run("hide_keyboard") } }.disabled(!store.canControl)
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }.keyboardShortcut(.cancelAction)
                Button(method == .clipboard ? "Paste Text" : fillTarget == nil ? "Send Text" : "Replace Text") { sendText() }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction).disabled((text.isEmpty && fillTarget == nil) || !store.canControl || !fillTargetIsCurrent || (store.selectedDevice?.backend == "physical" && (!confirmedOrdinaryField || (method == .keyboard && !confirmedUSLayout))))
            }
        }.padding(24).frame(width: 560)
        .onChange(of: store.controlReceipt?.ownershipGeneration) { _, _ in
            if (store.controlReceipt?.cancelledRequests ?? 0) > 0 { confirmedOrdinaryField = false; allowClockAndCaretChanges = false }
        }
    }
    private var fillTargetIsCurrent: Bool {
        guard let target = fillTarget else { return true }
        return target.deviceID == store.selectedID && target.epoch == store.selectedDevice?.epoch && target.target["snapshot_id"]?.stringValue == store.elementObservation?.snapshotID
    }
    private func sendText() {
        if !fillTargetIsCurrent {
            store.issue = "The field snapshot belongs to a different device connection. Inspect the current screen again before replacing text."
            return
        }
        let layout: KeyboardLayoutDeclaration? = store.selectedDevice?.backend == "physical" && confirmedUSLayout ? .us : nil
        let context: TextFieldContextDeclaration? = store.selectedDevice?.backend == "physical" && confirmedOrdinaryField ? .ordinaryApp : nil
        let validation: TextFrameValidation = store.selectedDevice?.backend == "physical" && allowClockAndCaretChanges ? .textTarget : .exact
        let arguments = fillTarget.map { method.fillArguments(text: text, target: $0.target, keyboardLayout: layout, fieldContext: context, frameValidation: validation) } ?? method.typeArguments(text: text, keyboardLayout: layout, fieldContext: context)
        let tool = fillTarget == nil ? "type_text" : "fill"
        Task { if let result = await store.run(tool, arguments: arguments), result.error == nil { text = ""; dismiss(); await store.capture() } }
    }
}
