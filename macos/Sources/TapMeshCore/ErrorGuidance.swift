public struct ErrorGuidance: Equatable, Sendable {
    public let messageKey: String
    public let actionKey: String?

    fileprivate init(_ messageKey: String, _ actionKey: String? = nil) {
        self.messageKey = messageKey
        self.actionKey = actionKey
    }
}

public enum ErrorGuide {
    public static func guidance(code: String, serviceMessage: String? = nil) -> ErrorGuidance? {
        if code == "protected_action_requires_human",
           let serviceMessage,
           serviceMessage.hasPrefix("The device is locked.") || serviceMessage.hasPrefix("The device locked during input.") {
            return guidance(code: "device_locked")
        }
        return switch code {
        case "approval_required": ErrorGuidance("This action needs your approval.", "Open Approvals to review it.")
        case "approval_denied": ErrorGuidance("The request was denied.")
        case "approval_expired": ErrorGuidance("The approval expired.", "Send the request again if you still need it.")
        case "protected_action_requires_human": ErrorGuidance("This step needs your input.", "Complete the step on the device.")
        case "device_held": ErrorGuidance("AI control is paused.", "Choose Resume to let agents continue.")
        case "ownership_changed": ErrorGuidance("The owner took control of the device.", "Refresh the screen before sending input.")
        case "connection_changed": ErrorGuidance("The device reconnected.", "Refresh the screen before sending input.")
        case "device_disconnected": ErrorGuidance("The device disconnected.", "Reconnect it or choose another device.")
        case "device_locked": ErrorGuidance("The device is locked.", "Unlock it on the device and check the result before trying again.")
        case "device_not_found": ErrorGuidance("TapMesh couldn't find the device.", "Refresh the device list and choose an available device.")
        case "device_not_selected": ErrorGuidance("Choose a device for this action.")
        case "stale_observation": ErrorGuidance("This screen image is out of date.", "Refresh the screen before sending input.")
        case "stale_element_reference": ErrorGuidance("This item is out of date.", "Inspect the screen again and select the item.")
        case "ambiguous_text_match": ErrorGuidance("More than one item matches that text.", "Use the inspector to choose the item you want.")
        case "prerequisite_required": ErrorGuidance("This device needs more setup.", "Check the device setup steps and try again.")
        case "capability_unavailable", "unsupported_capability":
            ErrorGuidance("This action is unavailable on the selected device.", "Check the device's capabilities in the Inspector.")
        case "invalid_arguments": ErrorGuidance("Some of the action settings are invalid.", "Review the settings and try again.")
        case "deadline_exceeded": ErrorGuidance("The action took too long.", "Check the device before trying again.")
        case "delivery_outcome_unknown": ErrorGuidance("TapMesh couldn't confirm the result.", "Check the device before trying again.")
        case "cancelled": ErrorGuidance("The action stopped.")
        case "overloaded": ErrorGuidance("The service is busy.", "Wait for the current work to finish, then try again.")
        case "backend_failure": ErrorGuidance("The device service couldn't complete the action.", "Check the device and reconnect if necessary.")
        case "backend_unavailable", "agent_unavailable":
            ErrorGuidance("The device service is unavailable.", "Reconnect in TapMesh and try again.")
        case "live_unavailable": ErrorGuidance("Live video is unavailable.", "Use refreshed pictures or reconnect the device with a cable.")
        case "local_authorization_required":
            ErrorGuidance("This action requires approval from the owner.", "Open the TapMesh window that manages these services.")
        case "artifact_conflict": ErrorGuidance("A file with that name already exists.", "Choose a different name or folder.")
        case "insufficient_storage": ErrorGuidance("There isn't enough space to save this file.", "Free up space and try again.")
        case "unsafe_path": ErrorGuidance("TapMesh can't use that file or folder.", "Choose a location you have allowed in TapMesh.")
        case "build_failed": ErrorGuidance("The build failed.", "Open Builds to see the failed step and log.")
        case "install_failed": ErrorGuidance("TapMesh couldn't install the app.", "Check that the app supports this device and is signed for it.")
        default: nil
        }
    }
}
