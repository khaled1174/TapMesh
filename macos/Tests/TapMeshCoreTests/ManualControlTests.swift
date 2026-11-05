import XCTest
@testable import TapMeshCore

final class ManualControlTests: XCTestCase {
    private func receipt(cancelled: Int = 0, generation: Int = 2, deviceID: String = "sim:A", epoch: Int = 3) throws -> TakeoverReceipt {
        try TakeoverReceipt(response: .object(["status": .string("success"), "data": .object(["device_id": .string(deviceID), "connection_epoch": .number(Double(epoch)), "cancelled_requests": .number(Double(cancelled)), "ownership_generation": .number(Double(generation))])]), requestedDeviceID: "sim:A")
    }
    private func tool(_ name: String, mutation: Bool = true) throws -> ToolDefinition {
        try XCTUnwrap(ToolDefinition(.object(["name": .string(name), "mutation": .bool(mutation), "device_bound": .bool(true)])))
    }
    func testInterruptionDiscardsOldActionUntilFreshCurrentGenerationScreen() throws {
        let args: [String: JSONValue] = ["device_id": .string("sim:A"), "target": .object(["x": .number(10), "y": .number(20), "observation_id": .string("old-frame")])]
        let plan = try XCTUnwrap(ManualControlPlan.make(tool: tool("tap"), arguments: args, selectedDeviceID: "sim:B", epoch: "3"))
        XCTAssertEqual(plan.deviceID, "sim:A", "Explicit target must not follow a later sidebar selection")
        var control = ManualControlSession()
        control.tookControl(try receipt(cancelled: 1), knownEpoch: "3")
        XCTAssertTrue(control.rejects(plan), "The old click must be discarded, never rebound or replayed")
        control.tookControl(try receipt(generation: 3), knownEpoch: "3")
        XCTAssertTrue(control.rejects(plan), "A zero-interruption receipt cannot make the old screen fresh")
        XCTAssertFalse(control.observedFreshScreen(deviceID: "sim:B", epoch: "3", generation: 3))
        XCTAssertFalse(control.observedFreshScreen(deviceID: "sim:A", epoch: "2", generation: 3))
        XCTAssertFalse(control.observedFreshScreen(deviceID: "sim:A", epoch: "3", generation: 2))
        XCTAssertTrue(control.rejects(plan))
        XCTAssertTrue(control.observedFreshScreen(deviceID: "sim:A", epoch: "3", generation: 3))
        XCTAssertFalse(control.needsFreshObservation)
    }
    func testZeroInterruptionPreservesOwnScreenButEpochChangeDoesNot() throws {
        let plan = try XCTUnwrap(ManualControlPlan.make(tool: tool("tap"), arguments: ["target": .object(["snapshot_id": .string("own-snapshot"), "ref": .string("element")])], selectedDeviceID: "sim:A", epoch: "3"))
        var control = ManualControlSession()
        control.tookControl(try receipt(), knownEpoch: "3")
        XCTAssertFalse(control.rejects(plan))
        control.tookControl(try receipt(generation: 3, epoch: 4), knownEpoch: "3")
        XCTAssertTrue(control.rejects(plan))
        XCTAssertThrowsError(try receipt(deviceID: "sim:OTHER"))
        XCTAssertThrowsError(try TakeoverReceipt(response: .object(["status": .string("success"), "data": .object(["device_id": .string("sim:A")])]), requestedDeviceID: "sim:A"))
    }
    func testRecordingStopAndReadOnlyCallsNeverPlanTakeover() throws {
        XCTAssertNil(try ManualControlPlan.make(tool: tool("stop_recording"), arguments: ["device_id": .string("sim:A"), "recording_id": .string("recording")], selectedDeviceID: "sim:B", epoch: "3"))
        XCTAssertNil(try ManualControlPlan.make(tool: tool("take_screenshot", mutation: false), arguments: [:], selectedDeviceID: "sim:A", epoch: "3"))
        XCTAssertThrowsError(try ManualControlPlan.make(tool: tool("press_button"), arguments: [:], selectedDeviceID: nil, epoch: nil))
    }
}
