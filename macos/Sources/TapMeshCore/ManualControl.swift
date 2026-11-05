import Foundation

public enum ManualControlError: LocalizedError {
    case missingTarget, invalidResponse, differentTarget
    public var errorDescription: String? {
        switch self {
        case .missingTarget: "Select the exact device before taking control."
        case .invalidResponse: "The takeover response did not confirm device identity, connection epoch, and ownership generation. No device action was sent."
        case .differentTarget: "The takeover response belongs to a different device. No device action was sent."
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

/// An interrupted screen remains unusable across later zero-interruption
/// takeovers until a new observation from the current generation is accepted.
public struct ManualControlSession: Sendable {
    public private(set) var receipt: TakeoverReceipt?
    public private(set) var needsFreshObservation = false
    public init() { }
    public mutating func tookControl(_ receipt: TakeoverReceipt, knownEpoch: String?) {
        if receipt.cancelledRequests > 0 || knownEpoch != receipt.epoch { needsFreshObservation = true }
        self.receipt = receipt
    }
    public func rejects(_ plan: ManualControlPlan) -> Bool {
        guard let receipt else { return true }
        return plan.requiresFreshInput(after: receipt) || (plan.screenBound && needsFreshObservation)
    }
    @discardableResult
    public mutating func observedFreshScreen(deviceID: String, epoch: String, generation: Int?) -> Bool {
        guard let receipt, receipt.deviceID == deviceID, receipt.epoch == epoch, receipt.ownershipGeneration == generation else { return false }
        needsFreshObservation = false
        return true
    }
}
