import XCTest
@testable import TapMeshCore

final class RecordingStateTests: XCTestCase {
    private func envelope(_ json: String) throws -> ToolEnvelope { try JSONDecoder().decode(ToolEnvelope.self, from: Data(json.utf8)) }

    func testFailedStopRetainsOriginalTargetAndAllowsSafeRetry() throws {
        var state = RecordingState()
        let started = try envelope(#"{"request_id":"start","status":"ok","device_id":"sim:ORIGINAL","data":{"recording_id":"rec-1"}}"#)
        XCTAssertTrue(state.started(response: started, requestedDeviceID: "sim:ORIGINAL"))
        let first = try XCTUnwrap(state.beginStop())
        XCTAssertEqual(first.stopArguments, ["recording_id": .string("rec-1"), "device_id": .string("sim:ORIGINAL")])
        XCTAssertNil(state.beginStop(), "Repeated clicks must not send overlapping stop requests")
        let disconnected = try envelope(#"{"request_id":"stop1","status":"error","device_id":"sim:ORIGINAL","error":{"code":"device_disconnected","message":"Disconnected","side_effect_state":"unknown"}}"#)
        state.finishedStop(first, response: disconnected)
        XCTAssertEqual(state.active, first, "An uncertain stop must preserve the original recording identity")
        let second = try XCTUnwrap(state.beginStop())
        XCTAssertEqual(second, first, "Selection changes cannot redirect the retry to another target")
        let stopped = try envelope(#"{"request_id":"stop2","status":"ok","device_id":"sim:ORIGINAL","data":{"recording_id":"rec-1","state":"complete"}}"#)
        state.finishedStop(second, response: stopped)
        XCTAssertNil(state.active)
        XCTAssertNil(state.stopping)
        XCTAssertNil(state.beginStop())
    }

    func testMismatchedDeviceResponseCannotReplaceOrClearRecording() throws {
        var state = RecordingState()
        let mismatched = try envelope(#"{"request_id":"start","status":"ok","device_id":"sim:OTHER","data":{"recording_id":"rec-2"}}"#)
        XCTAssertFalse(state.started(response: mismatched, requestedDeviceID: "sim:ORIGINAL"))
        XCTAssertNil(state.active)
        let started = try envelope(#"{"request_id":"start","status":"ok","device_id":"sim:ORIGINAL","data":{"recording_id":"rec-1"}}"#)
        XCTAssertTrue(state.started(response: started, requestedDeviceID: "sim:ORIGINAL"))
        let request = try XCTUnwrap(state.beginStop())
        let wrongStop = try envelope(#"{"request_id":"stop","status":"ok","device_id":"sim:OTHER"}"#)
        state.finishedStop(request, response: wrongStop)
        XCTAssertEqual(state.active?.deviceID, "sim:ORIGINAL")
        XCTAssertFalse(state.started(response: mismatched, requestedDeviceID: "sim:OTHER"), "An active recording cannot silently be replaced")
    }
}
