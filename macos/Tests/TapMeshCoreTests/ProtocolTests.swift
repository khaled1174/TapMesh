import XCTest
@testable import TapMeshCore

final class ProtocolTests: XCTestCase {
    func testUnknownDeviceMetadataSurvivesDecoding() throws {
        let json = #"{"request_id":"r1","status":"ok","data":{"devices":[{"device_id":"sim:ABC","backend":"simulator","native_id":"ABC","name":"Test iPhone","ready":false,"readiness_reasons":["Simulator is shut down"],"connection_epoch":3,"capabilities":{"screen.capture":{"status":"requires_user_action","reason":"Boot first","limits":{"max_width":1280}}}}]}}"#
        let envelope = try JSONDecoder().decode(ToolEnvelope.self, from: Data(json.utf8))
        let devices = envelope.data?["devices"]?.arrayValue?.compactMap(Device.init) ?? []
        XCTAssertEqual(devices.first?.id, "sim:ABC")
        XCTAssertEqual(devices.first?.epoch, "3")
        XCTAssertFalse(devices.first?.ready ?? true)
        XCTAssertEqual(devices.first?.capabilities["screen.capture"]?.status, "requires_user_action")
    }

    func testErrorPreservesUncertainDelivery() throws {
        let json = #"{"request_id":"r2","status":"error","error":{"code":"delivery_outcome_unknown","message":"Device disconnected after input","retryable":false,"side_effect_state":"unknown"}}"#
        let result = try JSONDecoder().decode(ToolEnvelope.self, from: Data(json.utf8))
        XCTAssertEqual(result.error?.sideEffectState, "unknown")
        XCTAssertEqual(result.error?.retryable, false)
        XCTAssertFalse(result.isSuccess)
    }

    func testLetterboxRejectsMarginsAndMapsImagePixels() throws {
        let geometry = PreviewGeometry(image: .init(width: 400, height: 800), logical: .init(width: 200, height: 400), transform: .init(a: 0.5, b: 0, c: 0, d: 0.5, tx: 0, ty: 0), orientation: "portrait")
        XCTAssertNil(geometry.devicePoint(at: .init(x: 20, y: 200), in: .init(width: 600, height: 800)))
        XCTAssertEqual(geometry.devicePoint(at: .init(x: 300, y: 400), in: .init(width: 600, height: 800)), .init(x: 100, y: 200))
        XCTAssertNil(geometry.devicePoint(at: .init(x: 600, y: 800), in: .init(width: 600, height: 800)))
    }

    func testRotationUsesProvidedTransformWithoutGuessing() {
        let geometry = PreviewGeometry(image: .init(width: 800, height: 400), logical: .init(width: 200, height: 400), transform: .init(a: 0, b: 0.5, c: -0.5, d: 0, tx: 200, ty: 0), orientation: "landscape_left")
        XCTAssertEqual(geometry.devicePoint(at: .init(x: 200, y: 100), in: .init(width: 800, height: 400)), .init(x: 150, y: 100))
        XCTAssertNil(PreviewGeometry(image: .init(width: 0, height: 0), logical: .init(width: 200, height: 400), transform: .identity, orientation: "portrait").devicePoint(at: .init(x: 1, y: 1), in: .init(width: 800, height: 400)))
    }

    func testObservationRequiresExplicitGeometryAndRetainsBinding() throws {
        let data = try JSONValue.parse(#"{"observation_id":"obs-1","snapshot_id":"snap-2","device_id":"sim:ABC","connection_epoch":"epoch-3","orientation":"landscape_right","logical_size":{"width":200,"height":400},"image_size":{"width":800,"height":400},"image_to_device_transform":[0,0.5,-0.5,0,200,0],"freshness":"fresh"}"#)
        let observation = try XCTUnwrap(Observation(data))
        XCTAssertEqual(observation.id, "obs-1")
        XCTAssertEqual(observation.snapshotID, "snap-2")
        XCTAssertEqual(observation.epoch, "epoch-3")
        XCTAssertEqual(observation.geometry?.orientation, "landscape_right")
        XCTAssertNil(Observation(try JSONValue.parse(#"{"orientation":"portrait"}"#)))
    }

    func testClosedSchemaValidationCatchesUnknownAndMissingFields() throws {
        let schema = try JSONValue.parse(#"{"type":"object","required":["text"],"additionalProperties":false,"properties":{"text":{"type":"string","maxLength":3},"count":{"type":"integer","minimum":1}}}"#)
        XCTAssertFalse(SchemaValidator.errors(for: try JSONValue.parse(#"{"text":"hello","extra":true}"#), schema: schema).isEmpty)
        XCTAssertTrue(SchemaValidator.errors(for: try JSONValue.parse(#"{"text":"hi","count":1}"#), schema: schema).isEmpty)
        XCTAssertFalse(SchemaValidator.errors(for: try JSONValue.parse(#"{"count":0}"#), schema: schema).isEmpty)
    }

    func testLocalApprovalRejectsOtherSessionsConnectionsAndHandoffs() throws {
        let json = #"{"request_id":"r3","status":"error","device_id":"sim:ABC","connection_epoch":3,"error":{"code":"approval_required","message":"Approve action","side_effect_state":"not_sent","details":{"approval_id":"approval1","policy":"C"}}}"#
        let response = try JSONDecoder().decode(ToolEnvelope.self, from: Data(json.utf8))
        let record = try JSONValue.parse(#"{"id":"approval1","session_id":"local-session","tool":"tap","policy":"C","device_id":"sim:ABC","connection_epoch":3}"#)
        XCTAssertTrue(LocalApprovalBinding.matches(response: response, record: record, sessionID: "local-session", tool: "tap"))
        XCTAssertFalse(LocalApprovalBinding.matches(response: response, record: record, sessionID: "mcp-session", tool: "tap"))
        XCTAssertFalse(LocalApprovalBinding.matches(response: response, record: record, sessionID: "local-session", tool: "uninstall_app"))
        for (key, value) in ["id": JSONValue.string("another-approval"), "connection_epoch": .number(4), "policy": .string("U"), "device_id": .string("sim:OTHER")] {
            var object = record.objectValue!; object[key] = value
            XCTAssertFalse(LocalApprovalBinding.matches(response: response, record: .object(object), sessionID: "local-session", tool: "tap"))
        }
        let uncertain = try JSONDecoder().decode(ToolEnvelope.self, from: Data(json.replacingOccurrences(of: "not_sent", with: "unknown").utf8))
        XCTAssertFalse(LocalApprovalBinding.matches(response: uncertain, record: record, sessionID: "local-session", tool: "tap"))
    }
}
