import XCTest
@testable import TapMeshCore

final class ElementHighlightTests: XCTestCase {
    /// A displayed frame shaped like agent/backends.py `_screenshot` output: 400x800 image, 200x400 points.
    private func frame(device: String = "sim:A", epoch: String = "7", at: String = "2026-11-22T10:00:05.250000+00:00",
                       orientation: String = "portrait", logical: (Double, Double) = (200, 400),
                       image: (Double, Double) = (400, 800), transform: String? = nil) throws -> Observation {
        let matrix = transform ?? #"{"a":\#(logical.0 / image.0),"b":0,"c":0,"d":\#(logical.1 / image.1),"tx":0,"ty":0}"#
        return try XCTUnwrap(Observation(JSONValue.parse(#"{"observation_id":"f1","device_id":"\#(device)","connection_epoch":\#(epoch),"observed_at":"\#(at)","source":"simctl","orientation":"\#(orientation)","logical_size":{"width":\#(logical.0),"height":\#(logical.1)},"image_size":{"width":\#(image.0),"height":\#(image.1)},"image_to_device_transform":\#(matrix),"freshness":{"state":"fresh","max_age_ms":30000}}"#)))
    }

    /// An element snapshot shaped like agent/core.py `_elements_capture` output: no image_size.
    private func snapshot(device: String = "sim:A", epoch: String = "7", at: String = "2026-11-22T10:00:00.000100+00:00",
                          orientation: String = "portrait", logical: (Double, Double) = (200, 400), snapshotID: String? = "s1") throws -> Observation {
        let sid = snapshotID.map { #""snapshot_id":"\#($0)","# } ?? ""
        return try XCTUnwrap(Observation(JSONValue.parse(#"{"observation_id":"o2",\#(sid)"device_id":"\#(device)","connection_epoch":\#(epoch),"observed_at":"\#(at)","source":"accessibility","orientation":"\#(orientation)","logical_size":{"width":\#(logical.0),"height":\#(logical.1)},"freshness":{"state":"fresh","max_age_ms":30000}}"#)))
    }

    private let bounds = try! JSONValue.parse(#"{"x":20,"y":40,"width":100,"height":50}"#)
    private let container = PlaneSize(width: 600, height: 800)

    func testMatchingObservationsMapPointsIntoLetterboxedImage() throws {
        let snap = try snapshot(), shot = try frame()
        XCTAssertNil(shot.snapshotID); XCTAssertNotNil(shot.geometry); XCTAssertNil(snap.geometry)
        XCTAssertEqual(snap.logicalSize, PlaneSize(width: 200, height: 400))
        // Image 400x800 fits 600x800 at scale 1, centered with 100 on each side; 1 point = 2 pixels.
        XCTAssertEqual(ElementHighlight.rect(bounds: bounds, snapshot: snap, frame: shot, deviceID: "sim:A", epoch: "7", container: container),
                       PlaneRect(x: 140, y: 80, width: 200, height: 100))
    }

    func testRotatedTransformIsInvertedNotGuessed() throws {
        // Same transform as ProtocolTests.testRotationUsesProvidedTransformWithoutGuessing: image x -> device y.
        let shot = try frame(orientation: "landscape_left", image: (800, 400), transform: "[0,0.5,-0.5,0,200,0]")
        let snap = try snapshot(orientation: "landscape_left")
        let rect = try XCTUnwrap(ElementHighlight.rect(bounds: bounds, snapshot: snap, frame: shot, deviceID: "sim:A", epoch: "7", container: .init(width: 800, height: 400)))
        // Device (20,40)-(120,90) -> image x = 2y, y = 400 - 2x: (80..180, 160..360).
        XCTAssertEqual(rect, PlaneRect(x: 80, y: 160, width: 100, height: 200))
    }

    func testDifferentDeviceOrEpochOrSelectionDrawsNothing() throws {
        let shot = try frame()
        XCTAssertNil(ElementHighlight.rect(bounds: bounds, snapshot: try snapshot(device: "sim:B"), frame: shot, deviceID: "sim:A", epoch: "7", container: container))
        XCTAssertNil(ElementHighlight.rect(bounds: bounds, snapshot: try snapshot(epoch: "8"), frame: shot, deviceID: "sim:A", epoch: "7", container: container))
        XCTAssertNil(ElementHighlight.rect(bounds: bounds, snapshot: try snapshot(), frame: shot, deviceID: "sim:A", epoch: "8", container: container))
        XCTAssertNil(ElementHighlight.rect(bounds: bounds, snapshot: try snapshot(), frame: shot, deviceID: nil, epoch: "7", container: container))
        XCTAssertNil(ElementHighlight.rect(bounds: bounds, snapshot: try snapshot(snapshotID: nil), frame: shot, deviceID: "sim:A", epoch: "7", container: container))
        XCTAssertNil(ElementHighlight.rect(bounds: bounds, snapshot: nil, frame: shot, deviceID: "sim:A", epoch: "7", container: container))
    }

    func testRotationBetweenObservationsDrawsNothing() throws {
        // Rotated after inspecting: logical axes swapped, or same size but a different orientation.
        let rotated = try frame(orientation: "landscape_left", logical: (400, 200), image: (800, 400))
        XCTAssertNil(ElementHighlight.rect(bounds: bounds, snapshot: try snapshot(), frame: rotated, deviceID: "sim:A", epoch: "7", container: container))
        let flipped = try frame(orientation: "portrait_upside_down")
        XCTAssertNil(ElementHighlight.rect(bounds: bounds, snapshot: try snapshot(), frame: flipped, deviceID: "sim:A", epoch: "7", container: container))
    }

    func testFramesFarApartInTimeDrawNothing() throws {
        let late = try frame(at: "2026-11-22T10:00:31+00:00")
        XCTAssertNil(ElementHighlight.rect(bounds: bounds, snapshot: try snapshot(), frame: late, deviceID: "sim:A", epoch: "7", container: container))
        let earlier = try frame(at: "2026-11-22T09:59:20+00:00")
        XCTAssertNil(ElementHighlight.rect(bounds: bounds, snapshot: try snapshot(), frame: earlier, deviceID: "sim:A", epoch: "7", container: container))
        let unparseable = try frame(at: "")
        XCTAssertNil(ElementHighlight.rect(bounds: bounds, snapshot: try snapshot(), frame: unparseable, deviceID: "sim:A", epoch: "7", container: container))
        // Within the window, a frame without microseconds still parses.
        XCTAssertNotNil(ElementHighlight.rect(bounds: bounds, snapshot: try snapshot(), frame: try frame(at: "2026-11-22T10:00:29+00:00"), deviceID: "sim:A", epoch: "7", container: container))
    }

    func testFrameWithoutUsableGeometryDrawsNothing() throws {
        let noImage = try XCTUnwrap(Observation(JSONValue.parse(#"{"observation_id":"f1","device_id":"sim:A","connection_epoch":7,"observed_at":"2026-11-22T10:00:01+00:00","orientation":"portrait","logical_size":{"width":200,"height":400}}"#)))
        XCTAssertNil(ElementHighlight.rect(bounds: bounds, snapshot: try snapshot(), frame: noImage, deviceID: "sim:A", epoch: "7", container: container))
        // A transform that doesn't cover the logical screen, and one that can't be inverted.
        XCTAssertNil(ElementHighlight.rect(bounds: bounds, snapshot: try snapshot(), frame: try frame(transform: "[0.25,0,0,0.25,0,0]"), deviceID: "sim:A", epoch: "7", container: container))
        XCTAssertNil(ElementHighlight.rect(bounds: bounds, snapshot: try snapshot(), frame: try frame(transform: "[0,0,0,0,0,0]"), deviceID: "sim:A", epoch: "7", container: container))
        XCTAssertNil(ElementHighlight.rect(bounds: bounds, snapshot: try snapshot(), frame: try frame(), deviceID: "sim:A", epoch: "7", container: .init(width: 0, height: 800)))
    }

    func testElementOutsideOrMalformedDrawsNothing() throws {
        let snap = try snapshot(), shot = try frame()
        for text in [#"{"x":150,"y":40,"width":100,"height":50}"#, #"{"x":-5,"y":40,"width":10,"height":10}"#,
                     #"{"x":20,"y":40,"width":0,"height":50}"#, #"{"x":20,"y":40,"width":100}"#, #"{"x":"20","y":40,"width":100,"height":50}"#] {
            XCTAssertNil(ElementHighlight.rect(bounds: try JSONValue.parse(text), snapshot: snap, frame: shot, deviceID: "sim:A", epoch: "7", container: container), text)
        }
        XCTAssertNil(ElementHighlight.rect(bounds: nil, snapshot: snap, frame: shot, deviceID: "sim:A", epoch: "7", container: container))
        // The full screen is allowed.
        XCTAssertEqual(ElementHighlight.rect(bounds: try JSONValue.parse(#"{"x":0,"y":0,"width":200,"height":400}"#), snapshot: snap, frame: shot, deviceID: "sim:A", epoch: "7", container: container),
                       PlaneRect(x: 100, y: 0, width: 400, height: 800))
    }
}
