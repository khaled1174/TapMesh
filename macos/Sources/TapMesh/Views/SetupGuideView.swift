import SwiftUI
import TapMeshCore

struct SetupGuideView: View {
    let issue: SetupIssue
    let onRecheck: () async -> Void
    var onStart: (() async -> Void)?
    var actionsDisabled = false
    @State private var working = false

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Label(issue.title, systemImage: issue == .xcodeMissing ? "hammer" : "iphone.slash")
                .font(.title2.weight(.semibold))
                .accessibilityAddTraits(.isHeader)

            VStack(alignment: .leading, spacing: 8) {
                ForEach(issue.steps.indices, id: \.self) { index in
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(verbatim: "\((index + 1).formatted()).")
                            .monospacedDigit()
                        Text(issue.steps[index])
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .accessibilityElement(children: .combine)
                }
            }
            .foregroundStyle(.secondary)

            HStack(spacing: 10) {
                if let onStart, issue == .simulatorShutdown {
                    Button("Start") { perform(onStart) }.buttonStyle(.borderedProminent)
                    Button("Check Again") { perform(onRecheck) }
                } else {
                    Button("Check Again") { perform(onRecheck) }.buttonStyle(.borderedProminent)
                }
                if working { ProgressView().controlSize(.small) }
            }
            .disabled(actionsDisabled || working)
        }
        .padding(24)
        .frame(maxWidth: 420, alignment: .leading)
        .background(.background, in: .rect(cornerRadius: 12))
        .overlay {
            RoundedRectangle(cornerRadius: 12).strokeBorder(.quaternary)
        }
        .padding(20)
    }

    private func perform(_ action: @escaping () async -> Void) {
        working = true
        Task {
            await action()
            working = false
        }
    }
}

extension SetupIssue {
    var title: String {
        switch self {
        case .xcodeMissing: String(localized: "Xcode is required")
        case .unpaired: String(localized: "Pair this device with your Mac")
        case .developerModeOff: String(localized: "Turn on Developer Mode")
        case .notBooted, .unavailable: String(localized: "This device isn't reachable")
        case .simulatorShutdown: String(localized: "This simulator is off")
        case .runtimeMissing: String(localized: "This simulator's iOS version isn't installed")
        }
    }

    fileprivate var steps: [LocalizedStringKey] {
        switch self {
        case .xcodeMissing:
            ["Install Xcode from the App Store.",
             "Open Xcode once and accept its license.",
             "Come back and choose Check Again."]
        case .unpaired:
            ["Connect the device with a cable and unlock it.",
             "Tap Trust on the device and enter its passcode.",
             "In Xcode, open Window › Devices and Simulators and wait until the device appears as connected."]
        case .developerModeOff:
            ["On the device, open Settings › Privacy & Security › Developer Mode.",
             "Turn it on and restart the device when asked.",
             "After the restart, unlock the device and confirm."]
        case .notBooted, .unavailable:
            ["Unlock the device and keep it connected by cable.",
             "If it still doesn't appear, unplug the cable and plug it in again."]
        case .simulatorShutdown:
            ["Choose Start to boot it."]
        case .runtimeMissing:
            ["Install it in Xcode › Settings › Components."]
        }
    }
}
