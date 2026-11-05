import Foundation

/// A user-originated action can authorize only the pending ID returned by that
/// immediately preceding request. The service binds that ID to its full digest.
public enum LocalApprovalBinding {
    public static func matches(response: ToolEnvelope, record: JSONValue, sessionID: String, tool: String) -> Bool {
        guard response.error?.code == "approval_required", response.error?.sideEffectState == "not_sent",
              let id = response.error?.details?["approval_id"]?.stringValue, !id.isEmpty,
              record["id"]?.stringValue == id,
              let policy = record["policy"]?.stringValue, ["C", "S", "D", "H"].contains(policy),
              response.error?.details?["policy"]?.stringValue == policy,
              record["session_id"]?.stringValue == sessionID,
              record["tool"]?.stringValue == tool, tool != "request_human_action",
              record["device_id"]?.stringValue == response.deviceID,
              record["connection_epoch"]?.scalarDescription == response.connectionEpoch?.scalarDescription else { return false }
        return true
    }
}
