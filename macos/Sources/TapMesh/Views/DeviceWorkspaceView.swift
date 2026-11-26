import SwiftUI
import TapMeshCore

struct DeviceWorkspaceView: View {
    @ObservedObject var store: WorkspaceStore
    @State private var keyboardSheet = false
    /// The element list is a developer tool, so it starts closed. The person's choice is kept across launches.
    @AppStorage("deviceWorkspace.showInspector") private var showInspector = false
    /// The inspector row that is selected or under the pointer, by element `ref`. Outlined on the screen image only.
    @State private var selectedElementRef: String?
    @State private var hoveredElementRef: String?
    var body: some View {
        VStack(spacing: 0) {
            DeviceHeader(store: store)
            Divider()
            controlBar
            Divider()
            HSplitView {
                VStack(spacing: 0) {
                    agentControlBanner
                    PreviewCanvas(store: store, highlightedElementRef: showInspector ? hoveredElementRef ?? selectedElementRef : nil)
                }
                .frame(minWidth: 340, maxWidth: .infinity, maxHeight: .infinity)
                if showInspector {
                    ElementsInspector(store: store, selectedRef: $selectedElementRef, hoveredRef: $hoveredElementRef)
                        .frame(minWidth: 250, idealWidth: 290, maxWidth: 380)
                }
            }
        }
        .sheet(isPresented: $keyboardSheet) { KeyboardSheet(store: store) }
        .onChange(of: store.elementObservation?.snapshotID) { _, snapshot in
            // A new or cleared snapshot has new references, so the old selection means nothing.
            selectedElementRef = nil; hoveredElementRef = nil
            // Device > Inspect Elements fills the list even while the inspector is hidden.
            // Show it, so the results the person asked for are visible.
            if snapshot != nil, !showInspector { showInspector = true }
        }
        .onChange(of: showInspector) { _, shown in
            if !shown { selectedElementRef = nil; hoveredElementRef = nil }
        }
    }

    @ViewBuilder private var agentControlBanner: some View {
        if store.agentBanner != .none, let deviceID = store.selectedID {
            let paused = store.agentBanner == .paused
            let title: LocalizedStringKey = paused ? "AI control is paused" : "An AI agent is controlling this device"
            let action: LocalizedStringKey = paused ? "Resume" : "Stop"
            HStack(spacing: 12) {
                Label(title, systemImage: paused ? "pause.circle" : "hand.raised")
                    .font(.callout.weight(.medium))
                Spacer(minLength: 8)
                Button(action) { Task { await store.setDeviceHeld(!paused, for: deviceID) } }
                    .buttonStyle(.borderedProminent)
                    .tint(paused ? Color.accentColor : Color.red)
                    .disabled(store.agentHoldPending || !store.online)
                    .fixedSize()
            }
            .padding(.horizontal, 18)
            .padding(.vertical, 8)
            .background(paused ? Color.secondary.opacity(0.08) : Color.orange.opacity(0.12))
            Divider()
        }
    }

    // MARK: Control bar

    /// How much of the bar can show titles. `ViewThatFits` picks the first layout that fits the width.
    private enum BarFit { case allTitles, mainTitles, iconsOnly, overflow }

    /// Every device action in one bar: navigation, device state, input, screen, then the inspector toggle.
    private var controlBar: some View {
        ViewThatFits(in: .horizontal) {
            controls(.allTitles)
            controls(.mainTitles)
            controls(.iconsOnly)
            controls(.overflow)
        }
        .buttonStyle(.borderless)
        .controlSize(.large)
        .padding(.horizontal, 18)
        .padding(.vertical, 10)
        // Attached once, outside ViewThatFits, so polling updates exactly once per change.
        .onChange(of: store.liveEnabled) { _, _ in store.updatePolling() }
    }

    private func controls(_ fit: BarFit) -> some View {
        let main = fit == .allTitles || fit == .mainTitles
        let secondary = fit == .allTitles
        return HStack(spacing: 10) {
            // Navigation
            action("Home", "house", titled: main, help: "Goes to the device’s Home Screen", tool: "press_button", arguments: ["button": .string("home")])
            action("Back", "chevron.backward", titled: main, help: "Goes back one screen in the current app", tool: "go_back")
            groupDivider
            // Device state
            if fit != .overflow {
                rotateMenu(titled: secondary)
                appearanceMenu(titled: secondary)
                groupDivider
            }
            // Input
            Button { keyboardSheet = true } label: { barLabel("Type Text", "keyboard", titled: main) }
                .help("Types text into the field that is selected on the device")
                .accessibilityLabel("Type Text")
                .disabled(!store.canControl)
            groupDivider
            // Screen
            Toggle("Live", isOn: $store.liveEnabled).toggleStyle(.switch).controlSize(.small)
                .help("Keeps the preview up to date, asking for a new picture as soon as the last one arrives. Pauses while the window is hidden or in the background.")
            Button { Task { await store.capture() } } label: { barLabel("Refresh Screen", "camera", titled: main) }
                .help("Updates the preview with the current device screen. Nothing is saved.")
                .accessibilityLabel("Refresh Screen")
                .disabled(!store.canControl)
            if fit != .overflow { saveScreenshotButton(titled: secondary) }
            Button { Task { await store.toggleRecording() } } label: { barLabel(recordTitle, store.recordingID == nil ? "record.circle" : "stop.circle.fill", titled: main) }
                .help(store.recordingID == nil ? String(localized: "Starts recording the device screen, for up to 10 minutes") : String(localized: "Stops the screen recording"))
                .accessibilityLabel(recordTitle)
                .tint(store.recordingID == nil ? nil : .red).disabled(!store.online || store.stoppingRecording || (store.recordingID == nil && !store.canControl))
            if fit == .overflow { moreMenu }
            Spacer(minLength: 12)
            Button { showInspector.toggle() } label: { barLabel("Inspector", "sidebar.right", titled: secondary) }
                .help(showInspector ? String(localized: "Hides the list of items on the device screen") : String(localized: "Shows the list of items on the device screen"))
                .accessibilityLabel("Inspector")
                .accessibilityValue(showInspector ? String(localized: "Shown") : String(localized: "Hidden"))
        }
    }

    private var groupDivider: some View { Divider().frame(height: 20).padding(.horizontal, 4) }

    private var recordTitle: LocalizedStringKey { store.recordingID == nil ? "Record" : store.stoppingRecording ? "Stopping…" : "Stop Recording" }

    @ViewBuilder private func barLabel(_ title: LocalizedStringKey, _ image: String, titled: Bool) -> some View {
        if titled { Label(title, systemImage: image).labelStyle(.titleAndIcon) } else { Label(title, systemImage: image).labelStyle(.iconOnly) }
    }

    private func rotateMenu(titled: Bool) -> some View {
        Menu { rotateItems } label: { barLabel("Rotate", "rotate.right", titled: titled) }
            .fixedSize()
            .help("Turns the device to portrait or landscape")
            .accessibilityLabel("Rotate")
            .disabled(!store.canControl)
    }

    private func appearanceMenu(titled: Bool) -> some View {
        Menu { appearanceItems } label: { barLabel("Appearance", "circle.lefthalf.filled", titled: titled) }
            .fixedSize()
            .help("Switches the device between light and dark mode")
            .accessibilityLabel("Appearance")
            .disabled(!store.canControl)
    }

    /// Orientation values sent to the device, with their titles. Left and right name the device's physical
    /// orientation, so they keep their meaning (and their order) in right-to-left languages.
    private static let orientations: [(value: String, title: LocalizedStringKey)] = [
        ("portrait", "Portrait"), ("landscape_left", "Landscape Left"), ("landscape_right", "Landscape Right"), ("portrait_upside_down", "Portrait Upside Down"),
    ]

    private static let appearances: [(value: String, title: LocalizedStringKey)] = [("light", "Light"), ("dark", "Dark"), ("system", "System")]

    private var rotateItems: some View {
        ForEach(Self.orientations, id: \.value) { item in
            Button(item.title) { Task { await store.run("rotate", arguments: ["orientation": .string(item.value)]); await store.refreshAfterAction() } }
        }
    }

    private var appearanceItems: some View {
        ForEach(Self.appearances, id: \.value) { item in Button(item.title) { Task { await store.run("set_appearance", arguments: ["appearance": .string(item.value)]); await store.refreshAfterAction() } } }
    }

    private func saveScreenshotButton(titled: Bool) -> some View {
        Button { Task { await store.run("save_screenshot", arguments: ["format": .string("png"), "full_resolution": .bool(true)]) } } label: { barLabel("Save Screenshot", "square.and.arrow.down", titled: titled) }
            .help("Saves a full-size screenshot to Saved Files")
            .accessibilityLabel("Save Screenshot")
            .disabled(!store.canControl)
    }

    /// Narrowest layout only: the lower-priority actions move here, each keeping its own rule for when it is available.
    private var moreMenu: some View {
        Menu {
            Menu { rotateItems } label: { Label("Rotate", systemImage: "rotate.right") }
                .help("Turns the device to portrait or landscape")
                .disabled(!store.canControl)
            Menu { appearanceItems } label: { Label("Appearance", systemImage: "circle.lefthalf.filled") }
                .help("Switches the device between light and dark mode")
                .disabled(!store.canControl)
            Divider()
            saveScreenshotButton(titled: true)
        } label: { Label("More", systemImage: "ellipsis.circle").labelStyle(.iconOnly) }
            .fixedSize()
            .help("More device actions: Rotate, Appearance and Save Screenshot")
            .accessibilityLabel("More device actions")
    }

    private func action(_ title: LocalizedStringKey, _ image: String, titled: Bool, help: LocalizedStringKey, tool: String, arguments: [String: JSONValue] = [:]) -> some View {
        Button { Task { await store.run(tool, arguments: arguments); await store.refreshAfterAction() } } label: { barLabel(title, image, titled: titled) }
            .help(help)
            .accessibilityLabel(title)
            .disabled(!store.canControl)
    }
}

struct DeviceHeader: View {
    @ObservedObject var store: WorkspaceStore
    @State private var showDetails = false
    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "iphone.gen3").font(.title2).foregroundStyle(.secondary).accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 4) {
                Text(store.selectedDevice?.name ?? String(localized: "Select a device")).font(.title3.weight(.semibold))
                Text(store.selectedDevice.map { device in
                    let kind = DisplayText.deviceKind(device.backend)
                    return device.ready ? String(localized: "\(kind) · Ready") : String(localized: "\(kind) · Not ready")
                } ?? String(localized: "Choose a device in the sidebar")).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            if let id = store.recordingID {
                Label("Recording", systemImage: "record.circle.fill").foregroundStyle(.red).font(.callout)
                    .help(store.recordingDeviceID.map { String(localized: "A screen recording is in progress (recording \(id) on \($0))") } ?? String(localized: "A screen recording is in progress (recording \(id))"))
            }
            if let device = store.selectedDevice {
                if device.backend == "physical" {
                    Menu {
                        DeviceAgentControlToggle(store: store, device: device)
                    } label: {
                        Label("Device settings", systemImage: "ellipsis.circle")
                            .labelStyle(.iconOnly)
                    }
                    .menuStyle(.borderlessButton)
                    .fixedSize()
                    .accessibilityLabel("Device settings")
                    .help("Device settings")
                }
                Button { showDetails.toggle() } label: { Image(systemName: "info.circle") }
                    .buttonStyle(.borderless)
                    .help("Show technical details about this device")
                    .accessibilityLabel("Technical details")
                    .popover(isPresented: $showDetails, arrowEdge: .bottom) {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Technical details").font(.headline)
                            DetailRow(label: "Device ID", value: device.id)
                            DetailRow(label: "Connection epoch", value: device.epoch)
                            if let receipt = store.controlReceipt {
                                DetailRow(label: "Take control epoch", value: String(receipt.connectionEpoch))
                                DetailRow(label: "Ownership generation", value: String(receipt.ownershipGeneration))
                                DetailRow(label: "Requests interrupted", value: String(receipt.cancelledRequests))
                            }
                        }
                        .padding(16)
                        .frame(minWidth: 280, alignment: .leading)
                    }
            }
        }.padding(18)
    }
}

struct PreviewCanvas: View {
    @ObservedObject var store: WorkspaceStore
    /// The inspector element to outline, by `ref`. Drawn only when it provably matches the displayed frame.
    var highlightedElementRef: String? = nil
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// Short-lived marks showing where a click or drag was sent. Visual only.
    @State private var inputMarks: [InputMark] = []
    @State private var cableHintTarget: PicturePreviewTarget?

    private struct PicturePreviewTarget: Equatable {
        let deviceID: String
        let epoch: String
    }

    private var picturePreviewTarget: PicturePreviewTarget? {
        guard store.online, store.liveEnabled, !store.liveVideoShowing, store.image != nil,
              let device = store.selectedDevice, device.backend == "physical", device.ready,
              let observation = store.observation, observation.deviceID == device.id, observation.epoch == device.epoch else { return nil }
        return PicturePreviewTarget(deviceID: device.id, epoch: device.epoch)
    }

    var body: some View {
        ZStack {
            Rectangle().fill(.quaternary.opacity(0.3))
            if let device = store.selectedDevice, let issue = SetupGuidance.issue(for: device) {
                SetupGuideView(issue: issue, onRecheck: { await store.refreshDevices() },
                               onStart: { await store.bootSelected() }, actionsDisabled: !store.online || store.busy)
            } else if let image = store.image, let observation = store.observation {
                VStack(spacing: 16) {
                    // The screen image, its marks and the click/drag coordinate math are pinned left to right:
                    // a point on the device is a physical place, whatever the app's language.
                    GeometryReader { proxy in
                        let live = liveLayout(observation: observation, image: image, container: proxy.size)
                        ZStack(alignment: .topLeading) {
                            // An out-of-date picture stays visible (dimmed, not clickable) until the next one
                            // arrives, so the preview never flashes empty between frames. Live video shows
                            // the current screen itself, so it is never dimmed.
                            Image(nsImage: image).resizable().interpolation(.high).aspectRatio(contentMode: .fit)
                                // A picture of another shape than the playing video (just rotated) is hidden, not shown around it.
                                .opacity(store.liveVideoShowing ? (live?.clickable == false ? 0 : 1) : store.previewStale ? 0.7 : 1)
                                .frame(width: proxy.size.width, height: proxy.size.height)
                                .contentShape(Rectangle())
                                .gesture(SpatialTapGesture().onEnded { event in
                                    guard let point = observation.geometry?.devicePoint(at: .init(x: event.location.x, y: event.location.y), in: .init(width: proxy.size.width, height: proxy.size.height)) else { return }
                                    let mark = InputMark(start: event.location, end: nil)
                                    showMark(mark)
                                    Task {
                                        let marker = store.activity.first?.id
                                        await store.tap(point, observation: observation)
                                        if wasNotSent("tap", since: marker) { showNotSent(mark) }
                                    }
                                })
                                .gesture(DragGesture(minimumDistance: 14).onEnded { event in
                                    guard let geometry = observation.geometry,
                                          let start = geometry.devicePoint(at: .init(x: event.startLocation.x, y: event.startLocation.y), in: .init(width: proxy.size.width, height: proxy.size.height)),
                                          let end = geometry.devicePoint(at: .init(x: event.location.x, y: event.location.y), in: .init(width: proxy.size.width, height: proxy.size.height)) else { return }
                                    let mark = InputMark(start: event.startLocation, end: event.location)
                                    showMark(mark)
                                    let target: (PlanePoint) -> JSONValue = { .object(["x": .number($0.x), "y": .number($0.y), "coordinate_space": .string("device_points"), "observation_id": .string(observation.id)]) }
                                    Task {
                                        let marker = store.activity.first?.id
                                        await store.run("swipe", arguments: ["start": target(start), "end": target(end), "duration_ms": .number(300)])
                                        if wasNotSent("swipe", since: marker) { showNotSent(mark) }
                                        await store.refreshAfterAction()
                                    }
                                })
                                .allowsHitTesting(store.canControl && !store.previewStale && observation.geometry != nil && live?.clickable != false)
                                .accessibilityLabel(store.selectedDevice.map { String(localized: "Live screen of \($0.name)") } ?? String(localized: "Live screen of the selected device"))
                                .accessibilityHint("Use the inspector to activate accessible elements. Mouse users can click to tap and drag to swipe.")
                            // Live video over the picture, on the picture's own rectangle (its crop is the
                            // screenshot's crop), so clicks map through the picture's geometry unchanged. It is
                            // in place, but transparent, until its first frame is decoded: no blank flash.
                            if let session = store.liveVideo, let live {
                                LiveVideoView(session: session, format: store.liveFormat)
                                    .frame(width: live.rect.width, height: live.rect.height)
                                    .position(x: live.rect.midX, y: live.rect.midY)
                                    .opacity(store.liveVideoShowing ? 1 : 0)
                                    .allowsHitTesting(false)
                                    .accessibilityHidden(true)
                            }
                            // Same frame and origin as the image, so gesture locations line up with the marks.
                            ZStack(alignment: .topLeading) {
                                if live?.clickable != false, let rect = highlightRect(observation: observation, container: proxy.size) {
                                    ElementOutline(rect: rect)
                                }
                                ForEach(inputMarks) { mark in InputMarkView(mark: mark, reduceMotion: reduceMotion) }
                            }
                            .frame(width: proxy.size.width, height: proxy.size.height)
                            .allowsHitTesting(false)
                            .accessibilityHidden(true)
                            if store.previewStale, store.liveEnabled, !store.liveVideoShowing {
                                ProgressView().controlSize(.small)
                                    .frame(width: proxy.size.width, height: proxy.size.height)
                                    .allowsHitTesting(false)
                                    .accessibilityLabel("Updating the screen…")
                            }
                        }
                    }
                    .environment(\.layoutDirection, .leftToRight)
                    HStack(spacing: 10) {
                        if store.liveVideoShowing { LiveBadge() }
                        previewStatus.font(.caption).foregroundStyle(.secondary).multilineTextAlignment(.center)
                    }
                    if let cableHintTarget, cableHintTarget == picturePreviewTarget {
                        Text("Connect this device with a cable for live video.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.center)
                    }
                }.padding(24)
            } else {
                VStack(spacing: 14) {
                    Image(systemName: "iphone.gen3").font(.system(size: 48, weight: .ultraLight)).foregroundStyle(.secondary).accessibilityHidden(true)
                    Text("Screen preview").font(.title2.weight(.medium))
                    previewStatus.foregroundStyle(.secondary).multilineTextAlignment(.center).frame(maxWidth: 380)
                    if store.canControl {
                        Button("Show Screen") { Task { await store.capture() } }.buttonStyle(.borderedProminent)
                            .help("Shows the current device screen here. Nothing is saved.")
                    }
                }.padding(32)
            }
        }
        .task(id: picturePreviewTarget) {
            cableHintTarget = nil
            guard let target = picturePreviewTarget else { return }
            do { try await Task.sleep(for: .seconds(10)) }
            catch { return }
            guard !Task.isCancelled, target == picturePreviewTarget else { return }
            cableHintTarget = target
        }
    }

    @ViewBuilder private var previewStatus: some View {
        let text = Text(verbatim: store.previewMessage)
        if let code = store.previewErrorCode { text.help(Text(verbatim: code)) }
        else { text }
    }

    /// Where live video goes, or nil when there is no stream. Normally the picture's rectangle; when
    /// the video has another shape (just rotated, picture not yet replaced) it is fitted on its own
    /// and clicks wait for a matching picture, because they map through the picture's geometry.
    private func liveLayout(observation: Observation, image: NSImage, container: CGSize) -> (rect: CGRect, clickable: Bool)? {
        guard store.liveVideo != nil else { return nil }
        let picture = observation.geometry?.image ?? PlaneSize(width: image.size.width, height: image.size.height)
        let box = PlaneSize(width: container.width, height: container.height)
        var clickable = true
        var content = picture
        if let format = store.liveFormat, !format.matchesAspect(of: picture) {
            content = PlaneSize(width: format.crop.width, height: format.crop.height)
            // Only while the video is what the person sees; before that the picture is shown as usual.
            clickable = !store.liveVideoShowing
        }
        guard let rect = aspectFitRect(content: content, in: box) else { return nil }
        return (CGRect(x: rect.x, y: rect.y, width: rect.width, height: rect.height), clickable)
    }

    /// Called only after a gesture passed the view's own guards. The store can still refuse to send;
    /// `showNotSent` then replaces the mark.
    private func showMark(_ mark: InputMark) {
        inputMarks.append(mark)
        let id = mark.id
        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(mark.notSent ? 1000 : reduceMotion ? 450 : 600))
            inputMarks.removeAll { $0.id == id }
        }
    }

    /// Swaps the ordinary mark (if still shown) for a red "not sent" mark at the same place.
    private func showNotSent(_ mark: InputMark) {
        inputMarks.removeAll { $0.id == mark.id }
        showMark(InputMark(start: mark.start, end: mark.end, notSent: true))
    }

    /// True when the store sent nothing for `tool` since `marker` (the newest Activity entry before the call).
    /// The store reaches the service only through `dispatch`, which always adds an Activity entry; the early
    /// refusals (out-of-date frame, busy, control changed, not connected) add none. A service error that says
    /// `not_sent` also counts. A transport failure stays an ordinary mark, because it may have reached the device.
    private func wasNotSent(_ tool: String, since marker: UUID?) -> Bool {
        guard let entry = store.activity.prefix(while: { $0.id != marker }).first(where: { $0.tool == tool }) else { return true }
        return entry.code != nil && entry.delivery == DeliveryNote.sentence(for: "not_sent")
    }

    /// The selected inspector element's outline, or nil unless `ElementHighlight` can prove the
    /// element snapshot and the displayed frame describe the same screen of the selected device.
    private func highlightRect(observation: Observation, container: CGSize) -> CGRect? {
        guard let ref = highlightedElementRef,
              let element = store.elements.first(where: { $0["ref"]?.stringValue == ref }),
              let rect = ElementHighlight.rect(bounds: element["bounds"], snapshot: store.elementObservation, frame: observation,
                                               deviceID: store.selectedDevice?.id, epoch: store.selectedDevice?.epoch,
                                               container: .init(width: container.width, height: container.height)) else { return nil }
        return CGRect(x: rect.x, y: rect.y, width: rect.width, height: rect.height)
    }
}

/// Shown while the preview is live video. Falling back to pictures is silent: the badge just goes away.
private struct LiveBadge: View {
    var body: some View {
        HStack(spacing: 5) {
            Circle().fill(.green).frame(width: 7, height: 7)
            Text("Live video")
        }
        .font(.caption.weight(.medium))
        .foregroundStyle(.secondary)
        .padding(.horizontal, 8).padding(.vertical, 3)
        .background(.quaternary.opacity(0.6), in: Capsule())
        .help("The preview is live video from the device. Click to tap and drag to swipe as usual.")
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Live video")
    }
}

/// Outline of the inspector's selected element. Purely visual: never hit-tested, never animated.
private struct ElementOutline: View {
    let rect: CGRect
    var body: some View {
        RoundedRectangle(cornerRadius: 3)
            .fill(Color.accentColor.opacity(0.12))
            .overlay(RoundedRectangle(cornerRadius: 3).stroke(Color.accentColor, lineWidth: 2))
            .shadow(color: .black.opacity(0.35), radius: 1)
            .frame(width: max(rect.width, 4), height: max(rect.height, 4))
            .position(x: rect.midX, y: rect.midY)
    }
}

/// Where a click (no `end`) or a drag (with `end`) landed, in the preview image's own coordinates.
private struct InputMark: Identifiable {
    let id = UUID()
    let start: CGPoint
    let end: CGPoint?
    /// The store refused the action, so nothing reached the device.
    var notSent = false
}

/// A ring for a click, a line with start and end dots for a drag. With Reduce Motion it is a plain
/// mark that disappears without scaling or fading. A refused action shows a red X instead (dashed line for a drag).
private struct InputMarkView: View {
    let mark: InputMark
    let reduceMotion: Bool
    @State private var settled = false
    var body: some View {
        ZStack(alignment: .topLeading) {
            if mark.notSent {
                if let end = mark.end {
                    Path { path in path.move(to: mark.start); path.addLine(to: end) }
                        .stroke(Color.red.opacity(0.8), style: StrokeStyle(lineWidth: 2, lineCap: .round, dash: [5, 5]))
                }
                Image(systemName: "xmark.circle.fill")
                    .font(.system(size: 24, weight: .semibold))
                    .foregroundStyle(.white, .red)
                    .position(mark.end ?? mark.start)
            } else if let end = mark.end {
                Path { path in path.move(to: mark.start); path.addLine(to: end) }
                    .stroke(Color.accentColor.opacity(0.8), style: StrokeStyle(lineWidth: 3, lineCap: .round))
                Circle().fill(Color.accentColor).frame(width: 10, height: 10).position(mark.start)
                Circle().stroke(Color.accentColor, lineWidth: 2).frame(width: 18, height: 18).position(end)
            } else {
                Circle().fill(Color.accentColor.opacity(0.25))
                    .overlay(Circle().stroke(Color.accentColor, lineWidth: 2))
                    .frame(width: 30, height: 30)
                    .scaleEffect(reduceMotion || settled ? 1 : 0.55)
                    .position(mark.start)
            }
        }
        .shadow(color: .black.opacity(0.3), radius: 1)
        .opacity(!reduceMotion && settled ? 0 : 1)
        .onAppear {
            guard !reduceMotion else { return }
            withAnimation(.easeOut(duration: mark.notSent ? 0.9 : 0.45)) { settled = true }
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
    /// Type or Paste and Hide Keyboard start in the collapsed options.
    @State private var showMoreOptions = false
    @FocusState private var editorFocused: Bool

    private var isPhysical: Bool { store.selectedDevice?.backend == "physical" }
    private var deviceName: String? { store.selectedDevice?.name }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            header
            editor
            if !fillTargetIsCurrent {
                Label("This field is out of date. Close this and choose the field again in the Inspector.", systemImage: "exclamationmark.triangle")
                    .font(.callout).foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if method == .clipboard {
                // Shown outside More Options, so the effect of Paste is visible even when the options are closed.
                Label("Paste replaces the device’s clipboard, and the text may also reach your Mac or nearby Apple devices.", systemImage: "doc.on.clipboard")
                    .font(.callout).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if isPhysical { confirmations }
            moreOptions
            footer
        }
        .padding(24)
        .frame(minWidth: 460, idealWidth: 540, maxWidth: 680, alignment: .leading)
        .defaultFocus($editorFocused, true)
        .onAppear { editorFocused = true }
        .onChange(of: store.controlReceipt?.ownershipGeneration) { _, _ in
            if (store.controlReceipt?.cancelledRequests ?? 0) > 0 { confirmedUSLayout = false; confirmedOrdinaryField = false }
        }
    }

    // MARK: Parts

    /// The first thing read: where the text goes.
    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(fillTarget.map { String(localized: "Replace text in “\($0.label)”") } ?? deviceName.map { String(localized: "Type on \($0)") } ?? String(localized: "Type on the device"))
                .font(.title2.weight(.semibold))
                .fixedSize(horizontal: false, vertical: true)
            Text(fillTarget == nil
                 ? String(localized: "Goes into the field that’s selected on the device. Tap a field first.")
                 : deviceName.map { String(localized: "On \($0). The field’s current text is replaced; nothing is submitted.") }
                    ?? String(localized: "On the device. The field’s current text is replaced; nothing is submitted."))
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isHeader)
    }

    private var editor: some View {
        TextEditor(text: $text)
            .font(.body)
            .focused($editorFocused)
            .scrollContentBackground(.hidden)
            .padding(6)
            .frame(minHeight: 110, idealHeight: 150, maxHeight: 320)
            .background(RoundedRectangle(cornerRadius: 6).fill(Color(nsColor: .textBackgroundColor)))
            .overlay(RoundedRectangle(cornerRadius: 6).stroke(.quaternary))
            .overlay(alignment: .topLeading) {
                if text.isEmpty {
                    Text(fillTarget == nil ? String(localized: "Text to type") : String(localized: "New text (leave empty to clear the field)"))
                        .foregroundStyle(.tertiary)
                        .padding(.horizontal, 11).padding(.vertical, 6)
                        .allowsHitTesting(false)
                        .accessibilityHidden(true)
                }
            }
            .accessibilityLabel(fillTarget.map { String(localized: "New text for \($0.label)") } ?? deviceName.map { String(localized: "Text to type on \($0)") } ?? String(localized: "Text to type on the device"))
    }

    /// Per-action declarations for a real iPhone or iPad. Always visible, never pre-ticked, never remembered.
    private var confirmations: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Before sending, confirm:").font(.headline)
            if method == .keyboard {
                declaration("The device uses the US keyboard layout",
                            note: "Other layouts can type the wrong characters, so use Paste in More Options instead.",
                            isOn: $confirmedUSLayout)
            }
            declaration("This is a normal app field, not a passcode, password or sign-in prompt",
                        note: "Enter passcodes and passwords on the device itself; this box doesn’t get around that.",
                        isOn: $confirmedOrdinaryField)
        }
    }

    private func declaration(_ title: LocalizedStringKey, note: LocalizedStringKey, isOn: Binding<Bool>) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Toggle(isOn: isOn) { Text(title).fixedSize(horizontal: false, vertical: true) }
                .toggleStyle(.checkbox)
                .accessibilityLabel(title)
                .accessibilityHint(note)
            Text(note).font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.leading, 20)
                .accessibilityHidden(true)
        }
    }

    private var moreOptions: some View {
        DisclosureGroup("More Options", isExpanded: $showMoreOptions) {
            VStack(alignment: .leading, spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    Picker("Send as", selection: $method) {
                        Text("Type").tag(TextInputMethod.keyboard)
                        Text("Paste").tag(TextInputMethod.clipboard)
                    }
                    .pickerStyle(.segmented)
                    .fixedSize()
                    .accessibilityHint("Type sends key presses. Paste uses the device’s clipboard and works with any characters.")
                    Text("Paste works with any characters, such as emoji or accented letters.")
                        .font(.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Button("Hide Device Keyboard") { Task { await store.run("hide_keyboard"); await store.capture() } }
                    .disabled(!store.canControl)
                    .help("Closes the on-screen keyboard on the device")
            }
            .padding(.top, 8)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var footer: some View {
        HStack {
            Spacer()
            Button("Cancel", role: .cancel) { dismiss() }
                .keyboardShortcut(.cancelAction)
            Button(primaryTitle) { sendText() }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
                .disabled(!canSend)
                .help(primaryHelp)
                .background {
                    // Cmd+Return sends from inside the text box (plain Return adds a new line there).
                    // Same action and the same disabled rule as the visible button, so it can't skip a confirmation.
                    Button(primaryTitle) { sendText() }
                        .keyboardShortcut(.return, modifiers: .command)
                        .disabled(!canSend)
                        .opacity(0)
                        .allowsHitTesting(false)
                        .accessibilityHidden(true)
                }
        }
    }

    private var primaryTitle: LocalizedStringKey { method == .clipboard ? "Paste Text" : fillTarget == nil ? "Send Text" : "Replace Text" }

    private var primaryHelp: String {
        if !fillTargetIsCurrent { return String(localized: "This field is out of date. Choose it again in the Inspector.") }
        if isPhysical && (!confirmedOrdinaryField || (method == .keyboard && !confirmedUSLayout)) { return String(localized: "Tick the boxes above first.") }
        if text.isEmpty && fillTarget == nil { return String(localized: "Enter some text first.") }
        return String(localized: "Sends the text (⌘Return)")
    }

    /// Unchanged rule: the button (and both shortcuts) work only when every check passes.
    private var canSend: Bool {
        !((text.isEmpty && fillTarget == nil) || !store.canControl || !fillTargetIsCurrent || (isPhysical && (!confirmedOrdinaryField || (method == .keyboard && !confirmedUSLayout))))
    }

    private var fillTargetIsCurrent: Bool {
        guard let target = fillTarget else { return true }
        return target.deviceID == store.selectedID && target.epoch == store.selectedDevice?.epoch && target.target["snapshot_id"]?.stringValue == store.elementObservation?.snapshotID
    }
    private func sendText() {
        if !fillTargetIsCurrent {
            store.issue = String(localized: "This field is out of date. Choose it again in the Inspector before replacing text.")
            return
        }
        guard canSend else { return }
        let layout: KeyboardLayoutDeclaration? = store.selectedDevice?.backend == "physical" && confirmedUSLayout ? .us : nil
        let context: TextFieldContextDeclaration? = store.selectedDevice?.backend == "physical" && confirmedOrdinaryField ? .ordinaryApp : nil
        let arguments = fillTarget.map { method.fillArguments(text: text, target: $0.target, keyboardLayout: layout, fieldContext: context) }
            ?? method.typeArguments(text: text, keyboardLayout: layout, fieldContext: context)
        let tool = fillTarget == nil ? "type_text" : "fill"
        Task { if let result = await store.run(tool, arguments: arguments), result.error == nil { text = ""; dismiss(); await store.capture() } }
    }
}
