import TapMeshCore
import XCTest

final class ErrorGuidanceTests: XCTestCase {
    func testEveryServiceCodeHasGuidance() {
        let serviceCodes = [
            "agent_unavailable", "ambiguous_text_match", "approval_denied", "approval_expired", "approval_required",
            "artifact_conflict", "backend_failure", "backend_unavailable", "build_failed", "cancelled",
            "capability_unavailable", "connection_changed", "deadline_exceeded", "delivery_outcome_unknown",
            "device_disconnected", "device_held", "device_locked", "device_not_found", "device_not_selected",
            "install_failed", "insufficient_storage", "invalid_arguments", "live_unavailable",
            "local_authorization_required", "overloaded", "ownership_changed", "prerequisite_required",
            "protected_action_requires_human", "stale_element_reference", "stale_observation",
            "unsafe_path", "unsupported_capability",
        ]
        for code in serviceCodes { XCTAssertNotNil(ErrorGuide.guidance(code: code), code) }
    }

    func testUnknownCodeFallsBack() {
        XCTAssertNil(ErrorGuide.guidance(code: "brand_new_code"))
    }

    func testDeviceHeldTellsThePersonWhatToDo() {
        XCTAssertEqual(ErrorGuide.guidance(code: "device_held")?.actionKey, "Choose Resume to let agents continue.")
    }

    func testLockErrorsUseUnlockGuidanceWhileOtherHandoffsKeepTheirOwnGuidance() {
        let handoff = "protected_action_requires_human"
        let lockMessages = [
            "The device is locked. Unlock it on the device, then try again. No input was sent.",
            "The device locked during input. Unlock it on the device and inspect the result before retrying."
        ]
        for message in lockMessages {
            XCTAssertEqual(ErrorGuide.guidance(code: handoff, serviceMessage: message), ErrorGuide.guidance(code: "device_locked"))
        }
        let prompt = ErrorGuide.guidance(code: handoff, serviceMessage: "Complete the protected prompt on the device.")
        XCTAssertEqual(prompt?.actionKey, "Complete the step on the device.")
    }
}
