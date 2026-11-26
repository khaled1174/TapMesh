import SwiftUI
import TapMeshCore

/// Shows which device a page acts on and lets the person switch without leaving the page.
/// Pages such as Apps and Builds put this in their header so the target is never implicit.
struct DeviceContextMenu: View {
    @ObservedObject var store: WorkspaceStore
    var body: some View {
        Menu {
            if store.devices.isEmpty { Text("No devices found") }
            ForEach(store.devices) { device in
                Button { Task { await store.select(device.id) } } label: {
                    let kind = DisplayText.deviceKind(device.backend)
                    Label(device.ready ? String(localized: "\(device.name) · \(kind)") : String(localized: "\(device.name) · \(kind) · Not ready"),
                          systemImage: device.id == store.selectedID ? "checkmark" : (device.backend == "simulator" ? "iphone.gen3" : "cable.connector"))
                }
            }
        } label: {
            Label(store.selectedDevice?.name ?? String(localized: "Choose a Device"), systemImage: "iphone.gen3")
        }
        .menuStyle(.borderlessButton)
        .fixedSize()
        .disabled(store.busy || !store.online)
        .help(store.selectedDevice.map { String(localized: "This page works on \($0.name). Click to switch.") } ?? String(localized: "Choose the device this page works on"))
        .accessibilityLabel(store.selectedDevice.map { String(localized: "Device: \($0.name)") } ?? String(localized: "Device: none selected"))
    }
}

struct DeviceAgentControlToggle: View {
    @ObservedObject var store: WorkspaceStore
    let device: Device

    var body: some View {
        Toggle("Allow AI agents to control this device", isOn: Binding(
            get: { store.agentDevices.contains(device.id) },
            set: { allowed in Task { await store.setAgentControl(deviceID: device.id, allowed: allowed) } }
        ))
        .disabled(!store.canChangeAgentControl)
    }
}
