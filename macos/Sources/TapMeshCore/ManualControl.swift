import Foundation

public enum ManualControlError: LocalizedError {
    case missingTarget, invalidResponse, differentTarget
    public var errorDescription: String? {
        switch self {
        case .missingTarget: String(localized: "Select the exact device before taking control.")
        case .invalidResponse: String(localized: "The takeover response did not confirm device identity, connection epoch, and ownership generation. No device action was sent.")
        case .differentTarget: String(localized: "The takeover response belongs to a different device. No device action was sent.")
        }
    }
}

public struct TakeoverReceipt: Equatable, Sendable {
    public let deviceID: String
    public let connectionEpoch: Int
    public let cancelledRequests: Int
    public let ownershipGeneration: Int
    public var epoch: String { String(connectionEpoch) }
    public init(response: JSONValue, requestedDeviceID: String) throws {
        guard response["status"]?.stringValue == "success", let data = response["data"],
              let deviceID = data["device_id"]?.stringValue else { throw ManualControlError.invalidResponse }
        guard deviceID == requestedDeviceID else { throw ManualControlError.differentTarget }
        func integer(_ field: String) throws -> Int {
            guard let number = data[field]?.doubleValue, number.isFinite, number >= 0, number.rounded() == number, number < Double(Int.max) else { throw ManualControlError.invalidResponse }
            return Int(number)
        }
        self.deviceID = deviceID; connectionEpoch = try integer("connection_epoch")
        cancelledRequests = try integer("cancelled_requests"); ownershipGeneration = try integer("ownership_generation")
    }
}

public struct ManualControlPlan: Sendable {
    public let deviceID: String
    public let observedEpoch: String?
    public let screenBound: Bool
    public static func make(tool: ToolDefinition, arguments: [String: JSONValue], selectedDeviceID: String?, epoch: String?) throws -> Self? {
        guard tool.mutation, tool.name != "stop_recording" else { return nil }
        let explicit = arguments["device_id"]?.stringValue
        let simulator = tool.name == "start_simulator" ? arguments["simulator_id"]?.stringValue.map { $0.hasPrefix("sim:") ? $0 : "sim:\($0)" } : nil
        guard let deviceID = explicit ?? simulator ?? (tool.deviceBound ? selectedDeviceID : nil) else {
            if tool.deviceBound { throw ManualControlError.missingTarget }
            return nil // A newly created simulator has no existing device to take over.
        }
        let screenBound = hasScreenBinding(.object(arguments)) || ["type_text", "fill"].contains(tool.name)
        return Self(deviceID: deviceID, observedEpoch: epoch, screenBound: screenBound)
    }
    public func requiresFreshInput(after receipt: TakeoverReceipt) -> Bool {
        guard receipt.deviceID == deviceID else { return true }
        return screenBound && (receipt.cancelledRequests > 0 || observedEpoch != receipt.epoch)
    }
    private static func hasScreenBinding(_ value: JSONValue) -> Bool {
        if let object = value.objectValue {
            if object["observation_id"] != nil || object["snapshot_id"] != nil { return true }
            return object.values.contains(where: hasScreenBinding)
        }
        return value.arrayValue?.contains(where: hasScreenBinding) ?? false
    }
}

/// The person's control of one device, and when it must be taken again.
///
/// Taking control stops whatever other sessions (AI clients) are running on the device, so it is
/// needed before the person's first action, after the connection changed, and whenever another
/// session may have acted since. Consecutive actions by the person reuse the same control: asking
/// the service again for every tap would only slow input down.
///
/// An interrupted screen remains unusable across later zero-interruption
/// takeovers until a new observation from the current generation is accepted.
public struct ManualControlSession: Sendable {
    /// After a pause this long the person's control is confirmed again before the next action,
    /// because an AI client may have resumed work on the device in the meantime.
    public static let idleRetakeInterval: TimeInterval = 10
    public private(set) var receipt: TakeoverReceipt?
    public private(set) var needsFreshObservation = false
    /// When the person last took control or acted on the device.
    public private(set) var lastOwnAction: Date?
    /// Another session changed a device since control was last taken.
    public private(set) var otherSessionActed = false
    public init() { }
    /// Records a takeover. Returns true when it disrupted the screen the person sees: it stopped
    /// pending work or the device's connection changed. Only then does the preview need a new
    /// picture before input; an uneventful takeover leaves the shown picture usable.
    @discardableResult
    public mutating func tookControl(_ receipt: TakeoverReceipt, knownEpoch: String?, at time: Date = Date()) -> Bool {
        let disruptive = receipt.cancelledRequests > 0 || knownEpoch != receipt.epoch
        if disruptive { needsFreshObservation = true }
        self.receipt = receipt
        lastOwnAction = time; otherSessionActed = false
        return disruptive
    }
    /// Whether `plan` must take control before it is sent. False only while the person's own,
    /// recent control of the same device and connection is still current.
    public func needsTakeover(for plan: ManualControlPlan, at time: Date = Date()) -> Bool {
        guard let receipt, receipt.deviceID == plan.deviceID, receipt.epoch == plan.observedEpoch,
              !otherSessionActed, let lastOwnAction else { return true }
        let idle = time.timeIntervalSince(lastOwnAction)
        return idle < 0 || idle > Self.idleRetakeInterval
    }
    /// The person acted on the device under the current control.
    public mutating func acted(at time: Date = Date()) {
        if receipt != nil { lastOwnAction = time }
    }
    /// Another session may have changed the device: the next action takes control again.
    public mutating func otherSessionMayHaveActed() { otherSessionActed = true }
    /// The person switched to this device again: the next action takes control again.
    public mutating func expire() { lastOwnAction = nil }
    public func rejects(_ plan: ManualControlPlan) -> Bool {
        guard let receipt, receipt.deviceID == plan.deviceID else { return true }
        // An interruption is remembered in needsFreshObservation until a new picture arrives, so a
        // receipt that is reused for later actions does not reject them forever.
        return plan.screenBound && (needsFreshObservation || plan.observedEpoch != receipt.epoch)
    }
    @discardableResult
    public mutating func observedFreshScreen(deviceID: String, epoch: String, generation: Int?) -> Bool {
        guard let receipt, receipt.deviceID == deviceID, receipt.epoch == epoch, receipt.ownershipGeneration == generation else { return false }
        needsFreshObservation = false
        return true
    }
}

/// Notices device-changing requests that other sessions (AI clients) completed, from the
/// service's request history (`api/history`, newest first). The history names neither session
/// nor device, so the person's own requests are recognised by request ID, and any other request
/// that changes a device counts as another session acting. Requests still running are not in
/// the history yet; `ManualControlSession.idleRetakeInterval` covers those.
public struct ForeignActivityTracker: Sendable {
    private var known: Set<String>?
    private var unattributed: Set<String> = []
    private var own: Set<String> = []
    private var ownOrder: [String] = []
    private static let ownLimit = 1000
    private static let unattributedLimit = 256
    public init() { }
    public mutating func recordOwn(_ requestID: String) {
        guard !requestID.isEmpty, own.insert(requestID).inserted else { return }
        ownOrder.append(requestID)
        if ownOrder.count > Self.ownLimit { own.remove(ownOrder.removeFirst()) }
    }
    /// Reads one history listing. The first listing is only a baseline: what happened before
    /// TapMesh looked is covered by the first takeover anyway.
    public mutating func observe(history rows: [JSONValue], mutatingTools: Set<String>) {
        let entries = rows.compactMap { row -> (id: String, mutating: Bool)? in
            guard let id = row["request_id"]?.stringValue, !id.isEmpty else { return nil }
            return (id, row["tool"]?.stringValue.map(mutatingTools.contains) ?? false)
        }
        if let known {
            for entry in entries where entry.mutating && !known.contains(entry.id) && !own.contains(entry.id)
                && unattributed.count < Self.unattributedLimit { unattributed.insert(entry.id) }
        }
        known = Set(entries.map(\.id))
    }
    /// True, once, when another session changed a device since the last call. The person's own
    /// requests that were still on their way back when the history was read are recognised here.
    public mutating func takeForeignActivity() -> Bool {
        defer { unattributed = [] }
        return unattributed.contains { !own.contains($0) }
    }
}
