import Foundation

public struct RecordingSession: Equatable, Sendable {
    public let id: String
    public let deviceID: String
    public var stopArguments: [String: JSONValue] { ["recording_id": .string(id), "device_id": .string(deviceID)] }
}

/// Owns the originating target until a successful stop response. Ordinary request
/// activity and current device selection do not participate in this lifecycle.
public struct RecordingState: Sendable {
    public private(set) var active: RecordingSession?
    public private(set) var stopping: RecordingSession?
    public init() { }

    @discardableResult
    public mutating func started(response: ToolEnvelope, requestedDeviceID: String) -> Bool {
        guard active == nil, response.isSuccess,
              !requestedDeviceID.isEmpty,
              response.deviceID == nil || response.deviceID == requestedDeviceID,
              let id = response.data?["recording_id"]?.stringValue, !id.isEmpty else { return false }
        active = RecordingSession(id: id, deviceID: requestedDeviceID)
        return true
    }

    public mutating func beginStop() -> RecordingSession? {
        guard stopping == nil, let active else { return nil }
        stopping = active
        return active
    }

    public mutating func finishedStop(_ session: RecordingSession, response: ToolEnvelope?) {
        guard stopping == session else { return }
        stopping = nil
        guard active == session, let response, response.error == nil,
              response.isSuccess || response.status == "partial",
              response.deviceID == nil || response.deviceID == session.deviceID else { return }
        active = nil
    }
}
