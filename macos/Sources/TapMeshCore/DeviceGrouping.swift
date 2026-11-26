import Foundation

public enum DeviceSection: CaseIterable, Hashable, Sendable {
    case connected, needsSetup, runningSimulators, otherSimulators
}

public enum DeviceGrouping {
    public static func sections(_ devices: [Device]) -> [(section: DeviceSection, devices: [Device])] {
        let grouped = Dictionary(grouping: devices, by: section)
        return DeviceSection.allCases.compactMap { section in
            guard let members = grouped[section] else { return nil }
            return (section, members.sorted(by: precedes))
        }
    }

    private static func section(for device: Device) -> DeviceSection {
        switch (device.backend, device.ready) {
        case ("physical", true): .connected
        case ("physical", false): .needsSetup
        case ("simulator", true): .runningSimulators
        default: .otherSimulators
        }
    }

    private static func precedes(_ first: Device, _ second: Device) -> Bool {
        let name = first.name.localizedCaseInsensitiveCompare(second.name)
        if name != .orderedSame { return name == .orderedAscending }

        let version = (first.osVersion ?? "").compare(second.osVersion ?? "", options: .numeric)
        if version != .orderedSame { return version == .orderedDescending }

        return first.id < second.id
    }
}
