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
    private func tapPlan(epoch: String? = "3") throws -> ManualControlPlan {
        try XCTUnwrap(ManualControlPlan.make(tool: tool("tap"), arguments: ["target": .object(["x": .number(1), "y": .number(2), "observation_id": .string("frame")])], selectedDeviceID: "sim:A", epoch: epoch))
    }
    func testConsecutiveOwnActionsReuseControlWithoutAnotherTakeover() throws {
        let start = Date(timeIntervalSinceReferenceDate: 1000)
        var control = ManualControlSession()
        XCTAssertTrue(control.needsTakeover(for: try tapPlan(), at: start), "The first action on a device takes control")
        XCTAssertFalse(control.tookControl(try receipt(), knownEpoch: "3", at: start), "Nothing stopped and the same connection: the preview stays as it is")
        XCTAssertFalse(control.needsFreshObservation, "An uneventful takeover must not invalidate the shown picture")
        var now = start
        for _ in 0..<20 {
            now += 0.4
            XCTAssertFalse(control.needsTakeover(for: try tapPlan(), at: now), "A run of taps and swipes must not take control each time")
            XCTAssertFalse(control.rejects(try tapPlan()))
            control.acted(at: now)
        }
        XCTAssertFalse(control.needsTakeover(for: try tapPlan(), at: now + ManualControlSession.idleRetakeInterval))
        XCTAssertTrue(control.needsTakeover(for: try tapPlan(), at: now + ManualControlSession.idleRetakeInterval + 1), "After a pause an agent may have resumed")
        XCTAssertTrue(control.needsTakeover(for: try tapPlan(), at: now - 1), "A clock that went backwards is not trusted")
    }
    func testAnotherSessionEpochChangeOrReselectionTakesControlAgain() throws {
        let start = Date(timeIntervalSinceReferenceDate: 1000)
        var control = ManualControlSession()
        control.tookControl(try receipt(), knownEpoch: "3", at: start)
        control.otherSessionMayHaveActed()
        XCTAssertTrue(control.needsTakeover(for: try tapPlan(), at: start + 1), "Another session acted since control was taken")
        control.tookControl(try receipt(generation: 3), knownEpoch: "3", at: start + 1)
        XCTAssertFalse(control.needsTakeover(for: try tapPlan(), at: start + 2), "Taking control again clears the other session's mark")
        XCTAssertTrue(control.needsTakeover(for: try tapPlan(epoch: "4"), at: start + 2), "A new connection needs control taken again")
        XCTAssertTrue(control.needsTakeover(for: try XCTUnwrap(ManualControlPlan.make(tool: tool("tap"), arguments: ["device_id": .string("sim:B")], selectedDeviceID: "sim:B", epoch: "3")), at: start + 2), "Control of one device never covers another")
        control.expire()
        XCTAssertTrue(control.needsTakeover(for: try tapPlan(), at: start + 2), "Selecting the device again takes control again")
        XCTAssertFalse(control.rejects(try tapPlan()), "Re-taking control is not a reason to refuse the action")
    }
    func testOnlyDisruptiveTakeoverInvalidatesPreviewAndReusedReceiptStopsRejectingOnceFresh() throws {
        var control = ManualControlSession()
        XCTAssertTrue(control.tookControl(try receipt(cancelled: 2), knownEpoch: "3"), "Stopping other work changes the screen")
        XCTAssertTrue(control.rejects(try tapPlan()))
        XCTAssertTrue(control.observedFreshScreen(deviceID: "sim:A", epoch: "3", generation: 2))
        XCTAssertFalse(control.rejects(try tapPlan()), "Once a new picture arrived, later actions under the same control are accepted")
        XCTAssertFalse(control.needsTakeover(for: try tapPlan()))
        var moved = ManualControlSession()
        XCTAssertTrue(moved.tookControl(try receipt(epoch: 4), knownEpoch: "3"), "A changed connection invalidates the preview")
        XCTAssertTrue(moved.needsFreshObservation)
    }
    func testForeignActivityComesFromOtherSessionsDeviceChangesOnly() throws {
        func row(_ id: String, _ tool: String) -> JSONValue { .object(["request_id": .string(id), "tool": .string(tool), "status": .string("success")]) }
        let mutating: Set<String> = ["tap", "swipe", "launch_app"]
        var tracker = ForeignActivityTracker()
        tracker.observe(history: [row("old-agent", "tap")], mutatingTools: mutating)
        XCTAssertFalse(tracker.takeForeignActivity(), "The first reading is only a baseline")
        tracker.recordOwn("mine-1")
        tracker.observe(history: [row("mine-1", "swipe"), row("shot", "take_screenshot"), row("old-agent", "tap")], mutatingTools: mutating)
        XCTAssertFalse(tracker.takeForeignActivity(), "The person's own actions and screen reads are not another session acting")
        tracker.observe(history: [row("mine-2", "tap"), row("mine-1", "swipe")], mutatingTools: mutating)
        tracker.recordOwn("mine-2")
        XCTAssertFalse(tracker.takeForeignActivity(), "An own request read from the history before its reply arrived is still recognised")
        tracker.observe(history: [row("agent-1", "launch_app"), row("mine-2", "tap")], mutatingTools: mutating)
        XCTAssertTrue(tracker.takeForeignActivity())
        XCTAssertFalse(tracker.takeForeignActivity(), "Reported once")
    }
    func testRecordingStopAndReadOnlyCallsNeverPlanTakeover() throws {
        XCTAssertNil(try ManualControlPlan.make(tool: tool("stop_recording"), arguments: ["device_id": .string("sim:A"), "recording_id": .string("recording")], selectedDeviceID: "sim:B", epoch: "3"))
        XCTAssertNil(try ManualControlPlan.make(tool: tool("take_screenshot", mutation: false), arguments: [:], selectedDeviceID: "sim:A", epoch: "3"))
        XCTAssertThrowsError(try ManualControlPlan.make(tool: tool("press_button"), arguments: [:], selectedDeviceID: nil, epoch: nil))
    }
}
