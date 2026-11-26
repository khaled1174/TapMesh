public enum SetupIssue: String, Equatable, Sendable {
    case xcodeMissing, unpaired, developerModeOff, notBooted, unavailable, simulatorShutdown, runtimeMissing
}

public enum SetupGuidance {
    public static func issue(readinessCode: String?) -> SetupIssue? {
        switch readinessCode {
        case nil: nil
        case "unpaired": .unpaired
        case "developer_mode_off": .developerModeOff
        case "not_booted": .notBooted
        case "simulator_shutdown": .simulatorShutdown
        case "runtime_missing": .runtimeMissing
        default: .unavailable
        }
    }

    public static func xcodeMissing(tools: [String: String]) -> Bool {
        tools["simctl"] == "unavailable" && tools["devicectl"] == "unavailable"
    }

    public static func issue(for device: Device) -> SetupIssue? {
        guard !device.ready else { return nil }
        return issue(readinessCode: device.readinessCode)
            ?? (device.backend == "simulator" ? .simulatorShutdown : .unavailable)
    }
}
