import Foundation

public struct ControlSettings: Equatable, Sendable {
    public var fastControl: Bool
    public var agentDevices: Set<String>

    public init?(_ value: JSONValue) {
        guard let fastControl = value["fast_control"]?.boolValue,
              let devices = value["agent_devices"]?.arrayValue else { return nil }
        let identifiers = devices.compactMap(\.stringValue)
        guard identifiers.count == devices.count else { return nil }
        self.fastControl = fastControl
        self.agentDevices = Set(identifiers)
    }

    /// An explicit legacy opt-out migrates only to the bundled profile. The
    /// service remains authoritative for every later connection.
    public static func legacyFastControlOverride(_ preference: Bool?, bundled: Bool, migrated: Bool) -> Bool? {
        bundled && !migrated && preference == false ? false : nil
    }
}

public struct DeviceActivity: Equatable, Sendable {
    public let deviceID: String
    public let active: Bool
    public let secondsSinceAction: Double?
    public let tool: String?
    public let held: Bool

    public init?(_ value: JSONValue) {
        guard let deviceID = value["device_id"]?.stringValue, !deviceID.isEmpty,
              let active = value["active"]?.boolValue, let held = value["held"]?.boolValue else { return nil }
        self.deviceID = deviceID
        self.active = active
        self.secondsSinceAction = value["seconds_since_action"]?.doubleValue
        self.tool = value["tool"]?.stringValue
        self.held = held
    }
}

public enum AgentBanner: Equatable, Sendable {
    case none, controlling, paused

    public static func banner(for activity: DeviceActivity?, recentWindow: Double = 5) -> Self {
        guard let activity else { return .none }
        if activity.held { return .paused }
        if activity.active || activity.secondsSinceAction.map({ $0 < recentWindow }) == true { return .controlling }
        return .none
    }
}

public enum DeviceGrantCopy {
    public static func title(for deviceName: String?) -> String {
        if let deviceName, !deviceName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return String(localized: "Allow AI agents to control \(deviceName)?")
        }
        return String(localized: "Allow AI agents to control this device?")
    }

    public static let explanation = String(localized: "Agents will be able to tap, type and open apps on this device without asking each time. You can stop them at any time.")
}
