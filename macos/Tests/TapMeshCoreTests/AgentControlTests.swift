import Foundation
import XCTest
import TapMeshCore

final class AgentControlTests: XCTestCase {
    func testSettingsDecoding() throws {
        let settings = try XCTUnwrap(ControlSettings(.parse("""
        {"fast_control":true,"agent_devices":["ios:A","ios:A","ios:B"]}
        """)))
        XCTAssertTrue(settings.fastControl)
        XCTAssertEqual(settings.agentDevices, ["ios:A", "ios:B"])
        XCTAssertNil(try ControlSettings(.parse(#"{"agent_devices":[]}"#)))
        XCTAssertNil(try ControlSettings(.parse(#"{"fast_control":false,"agent_devices":["ios:A",1]}"#)))
    }

    func testActivityDecodingAndBannerRules() throws {
        let activity = try XCTUnwrap(DeviceActivity(.parse("""
        {"device_id":"ios:A","active":false,"seconds_since_action":4.9,"tool":"tap","held":false}
        """)))
        XCTAssertEqual(activity.deviceID, "ios:A")
        XCTAssertEqual(activity.secondsSinceAction, 4.9)
        XCTAssertEqual(activity.tool, "tap")
        XCTAssertNil(try DeviceActivity(.parse(#"{"active":false,"held":false}"#)))
        XCTAssertEqual(AgentBanner.banner(for: nil), .none)
        XCTAssertEqual(AgentBanner.banner(for: activity), .controlling)
        XCTAssertEqual(AgentBanner.banner(for: try state(active: true, seconds: nil, held: false)), .controlling)
        XCTAssertEqual(AgentBanner.banner(for: try state(active: false, seconds: 5.0, held: false)), .none)
        XCTAssertEqual(AgentBanner.banner(for: try state(active: true, seconds: 0, held: true)), .paused)
    }

    func testOnlyExplicitLegacyOffMigratesToTheBundledServiceOnce() {
        XCTAssertEqual(ControlSettings.legacyFastControlOverride(false, bundled: true, migrated: false), false)
        XCTAssertNil(ControlSettings.legacyFastControlOverride(true, bundled: true, migrated: false))
        XCTAssertNil(ControlSettings.legacyFastControlOverride(nil, bundled: true, migrated: false))
        XCTAssertNil(ControlSettings.legacyFastControlOverride(false, bundled: true, migrated: true))
        XCTAssertNil(ControlSettings.legacyFastControlOverride(false, bundled: false, migrated: false))
    }

    func testDeviceGrantCardIsNeverAutoApprovedEvenWhenEveryOtherFieldMatches() throws {
        let response = try JSONDecoder().decode(ToolEnvelope.self, from: Data("""
        {"request_id":"request","status":"error","device_id":"ios:A","connection_epoch":0,
         "error":{"code":"approval_required","message":"Grant requested","side_effect_state":"not_sent",
                  "details":{"approval_id":"grant","policy":"A","scope":"device"}}}
        """.utf8))
        let record = try JSONValue.parse("""
        {"id":"grant","session_id":"app","tool":"allow_ai_control","device_id":"ios:A","connection_epoch":0,"policy":"A"}
        """)
        XCTAssertFalse(LocalApprovalBinding.matches(response: response, record: record, sessionID: "app", tool: "allow_ai_control"))
    }

    func testDeviceGrantNotificationUsesTheDeviceNameAndExplainsTheGrant() throws {
        var tracker = ApprovalAlertTracker(identifierPrefix: "test")
        let request = PendingApprovalSummary(id: "grant", action: nil, deviceName: "Test iPhone", deviceGrant: true)
        let plan = tracker.update(pending: [request], lookingAtApp: false, notificationsEnabled: true)
        let alert = try XCTUnwrap(plan.alert)
        XCTAssertEqual(alert.title, "Allow AI agents to control Test iPhone?")
        XCTAssertTrue(alert.body.contains("without asking each time"))
        XCTAssertFalse(alert.body.contains("ios:"))
    }

    private func state(active: Bool, seconds: Double?, held: Bool) throws -> DeviceActivity {
        try XCTUnwrap(DeviceActivity(.object([
            "device_id": .string("ios:A"), "active": .bool(active),
            "seconds_since_action": seconds.map(JSONValue.number) ?? .null, "tool": .null, "held": .bool(held)
        ])))
    }
}
