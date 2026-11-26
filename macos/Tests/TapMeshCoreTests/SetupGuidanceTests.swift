import XCTest
@testable import TapMeshCore

final class SetupGuidanceTests: XCTestCase {
    func testIssueMapping() {
        XCTAssertEqual(SetupGuidance.issue(readinessCode: "unpaired"), .unpaired)
        XCTAssertEqual(SetupGuidance.issue(readinessCode: "developer_mode_off"), .developerModeOff)
        XCTAssertEqual(SetupGuidance.issue(readinessCode: "not_booted"), .notBooted)
        XCTAssertEqual(SetupGuidance.issue(readinessCode: "unavailable"), .unavailable)
        XCTAssertEqual(SetupGuidance.issue(readinessCode: "simulator_shutdown"), .simulatorShutdown)
        XCTAssertEqual(SetupGuidance.issue(readinessCode: "runtime_missing"), .runtimeMissing)
        XCTAssertEqual(SetupGuidance.issue(readinessCode: "something_new"), .unavailable)
        XCTAssertNil(SetupGuidance.issue(readinessCode: nil))
    }

    func testXcodeMissingNeedsBothToolsUnavailable() {
        XCTAssertTrue(SetupGuidance.xcodeMissing(tools: ["simctl": "unavailable", "devicectl": "unavailable"]))
        XCTAssertFalse(SetupGuidance.xcodeMissing(tools: ["simctl": "available", "devicectl": "unavailable"]))
        XCTAssertFalse(SetupGuidance.xcodeMissing(tools: ["simctl": "unavailable", "devicectl": "available"]))
        XCTAssertFalse(SetupGuidance.xcodeMissing(tools: ["simctl": "unavailable"]))
        XCTAssertFalse(SetupGuidance.xcodeMissing(tools: [:]))
    }

    func testDeviceGuidanceUsesReadinessAndSupportsOlderServices() throws {
        let tablet = try XCTUnwrap(Device(.object([
            "device_id": .string("ios:tablet"),
            "backend": .string("physical"),
            "ready": .bool(false),
            "readiness_code": .string("developer_mode_off"),
        ])))
        let legacySimulator = try XCTUnwrap(Device(.object([
            "device_id": .string("sim:tablet"),
            "backend": .string("simulator"),
            "ready": .bool(false),
        ])))
        let readyPhone = try XCTUnwrap(Device(.object([
            "device_id": .string("ios:phone"),
            "backend": .string("physical"),
            "ready": .bool(true),
            "readiness_code": .null,
        ])))

        XCTAssertEqual(SetupGuidance.issue(for: tablet), .developerModeOff)
        XCTAssertEqual(SetupGuidance.issue(for: legacySimulator), .simulatorShutdown)
        XCTAssertNil(SetupGuidance.issue(for: readyPhone))
    }
}
