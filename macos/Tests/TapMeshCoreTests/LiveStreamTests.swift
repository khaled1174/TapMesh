import XCTest
@testable import TapMeshCore

final class LiveStreamTests: XCTestCase {
    // MARK: Wire helpers

    private func be32(_ value: Int) -> [UInt8] { [UInt8(value >> 24 & 0xff), UInt8(value >> 16 & 0xff), UInt8(value >> 8 & 0xff), UInt8(value & 0xff)] }
    private func message(_ type: UInt8, _ payload: [UInt8]) -> Data { Data([type] + be32(payload.count) + payload) }
    private func json(_ text: String) -> [UInt8] { Array(text.utf8) }
    private let formatJSON = #"{"version":1,"codec":"hevc","coded_width":1184,"coded_height":2560,"crop":{"x":0,"y":2,"width":1179,"height":2556}}"#
    private func paramsPayload(_ sets: [[UInt8]]) -> [UInt8] { sets.flatMap { be32($0.count) + $0 } }
    private func framePayload(key: Bool, pts: UInt64, nals: [[UInt8]]) -> [UInt8] {
        var bytes: [UInt8] = [key ? 1 : 0]
        for shift in stride(from: 56, through: 0, by: -8) { bytes.append(UInt8(pts >> UInt64(shift) & 0xff)) }
        return bytes + nals.flatMap { be32($0.count) + $0 }
    }
    private var sampleStream: Data {
        message(1, json(formatJSON))
            + message(2, paramsPayload([[0x40, 0x01, 0x0c], [0x42, 0x01, 0x01, 0x01], [0x44, 0x01, 0xc0]]))
            + message(3, framePayload(key: true, pts: 1_000_000, nals: [[0x26, 0x01, 0xaf], [0x26, 0x01]]))
            + message(4, [])
            + message(3, framePayload(key: false, pts: 1_016_667, nals: [[0x02, 0x01, 0xd0]]))
            + message(5, json(#"{"reason":"device_disconnected"}"#))
    }

    // MARK: Parser

    func testParsesManyMessagesInOneChunk() throws {
        var parser = LiveStreamParser()
        let messages = try parser.feed(sampleStream)
        XCTAssertEqual(messages.count, 6)
        guard case .format(let format) = messages[0] else { return XCTFail("expected FORMAT") }
        XCTAssertEqual(format.codedWidth, 1184); XCTAssertEqual(format.codedHeight, 2560)
        XCTAssertEqual(format.crop, LiveCrop(x: 0, y: 2, width: 1179, height: 2556))
        guard case .parameterSets(let params) = messages[1] else { return XCTFail("expected PARAMS") }
        XCTAssertEqual(params.vps, Data([0x40, 0x01, 0x0c])); XCTAssertEqual(params.sps, Data([0x42, 0x01, 0x01, 0x01])); XCTAssertEqual(params.pps, Data([0x44, 0x01, 0xc0]))
        guard case .frame(let key) = messages[2] else { return XCTFail("expected FRAME") }
        XCTAssertTrue(key.isKeyframe); XCTAssertEqual(key.presentationMicroseconds, 1_000_000)
        XCTAssertEqual(key.data, Data(be32(3) + [0x26, 0x01, 0xaf] + be32(2) + [0x26, 0x01]))
        XCTAssertEqual(messages[3], .heartbeat)
        guard case .frame(let delta) = messages[4] else { return XCTFail("expected FRAME") }
        XCTAssertFalse(delta.isKeyframe); XCTAssertEqual(delta.presentationMicroseconds, 1_016_667)
        XCTAssertEqual(messages[5], .end(reason: "device_disconnected"))
        XCTAssertEqual(parser.pendingByteCount, 0)
    }

    func testSameMessagesWhateverTheChunking() throws {
        var whole = LiveStreamParser()
        let expected = try whole.feed(sampleStream)
        let bytes = [UInt8](sampleStream)
        for size in [1, 2, 3, 5, 7, 13, 64] {
            var parser = LiveStreamParser()
            var messages: [LiveStreamMessage] = []
            for start in stride(from: 0, to: bytes.count, by: size) {
                messages += try parser.feed(Data(bytes[start..<min(start + size, bytes.count)]))
            }
            XCTAssertEqual(messages, expected, "chunk size \(size)")
            XCTAssertEqual(parser.pendingByteCount, 0)
        }
    }

    func testWaitsForTheRestOfASplitMessage() throws {
        var parser = LiveStreamParser()
        let frame = message(3, framePayload(key: true, pts: 7, nals: [[1, 2, 3, 4]]))
        XCTAssertEqual(try parser.feed(frame.prefix(3)), [])
        XCTAssertEqual(try parser.feed(frame.dropFirst(3).prefix(6)), [])
        XCTAssertEqual(parser.pendingByteCount, 9)
        let messages = try parser.feed(frame.dropFirst(9) + message(4, []))
        XCTAssertEqual(messages.count, 2)
        XCTAssertEqual(messages.last, .heartbeat)
    }

    func testRejectsAbsurdLengthBeforeBuffering() {
        var parser = LiveStreamParser()
        let header = Data([3] + be32(LiveStreamParser.maximumPayload + 1))
        XCTAssertThrowsError(try parser.feed(header)) { error in
            XCTAssertEqual(error as? LiveStreamError, .payloadTooLarge(type: 3, length: LiveStreamParser.maximumPayload + 1))
        }
        var other = LiveStreamParser()
        XCTAssertThrowsError(try other.feed(Data([0xff, 0xff, 0xff, 0xff, 0xff])))
    }

    func testSkipsUnknownMessageTypes() throws {
        var parser = LiveStreamParser()
        let messages = try parser.feed(message(0x7f, [1, 2, 3]) + message(4, []))
        XCTAssertEqual(messages, [.unknown(type: 0x7f), .heartbeat])
    }

    func testEndWithoutReason() throws {
        var parser = LiveStreamParser()
        XCTAssertEqual(try parser.feed(message(5, [])), [.end(reason: nil)])
    }

    // MARK: Payloads

    func testFormatValidation() throws {
        let noCrop = try LiveStreamFormat(payload: Data(json(#"{"version":1,"codec":"hevc","coded_width":1920,"coded_height":1080}"#)))
        XCTAssertEqual(noCrop.crop, LiveCrop(x: 0, y: 0, width: 1920, height: 1080))
        for bad in [
            #"{"version":2,"codec":"hevc","coded_width":10,"coded_height":10}"#,
            #"{"version":1,"codec":"h264","coded_width":10,"coded_height":10}"#,
            #"{"version":1,"codec":"hevc","coded_width":0,"coded_height":10}"#,
            #"{"version":1,"codec":"hevc","coded_width":10,"coded_height":10,"crop":{"x":2,"y":0,"width":10,"height":10}}"#,
            #"{"version":1,"codec":"hevc","coded_width":10,"coded_height":10,"crop":{"x":0,"y":0,"width":0,"height":10}}"#,
            "not json",
        ] {
            XCTAssertThrowsError(try LiveStreamFormat(payload: Data(json(bad))), bad)
        }
    }

    func testParameterSetsNeedVPSSPSAndPPS() {
        XCTAssertThrowsError(try HEVCParameterSets(payload: Data(paramsPayload([[1], [2]]))))
        XCTAssertThrowsError(try HEVCParameterSets(payload: Data(be32(5) + [1, 2])))
        XCTAssertThrowsError(try HEVCParameterSets(payload: Data(be32(0) + be32(1) + [1] + be32(1) + [1])))
        XCTAssertEqual(try HEVCParameterSets(payload: Data(paramsPayload([[1], [2], [3], [4]]))).sets.count, 4)
    }

    func testFrameValidation() throws {
        XCTAssertThrowsError(try LiveVideoFrame(payload: Data([1, 0, 0, 0, 0, 0, 0, 0, 0])), "no access unit")
        // A NAL length running past the end of the payload.
        XCTAssertThrowsError(try LiveVideoFrame(payload: Data(framePayload(key: true, pts: 0, nals: [[1, 2]]).dropLast())))
        // Trailing bytes too short for a length.
        XCTAssertThrowsError(try LiveVideoFrame(payload: Data(framePayload(key: true, pts: 0, nals: [[1, 2]]) + [0, 0])))
        let frame = try LiveVideoFrame(payload: Data(framePayload(key: false, pts: UInt64.max, nals: [[9]])))
        XCTAssertEqual(frame.presentationMicroseconds, UInt64.max)
        XCTAssertFalse(frame.isKeyframe)
        // Only bit 0 means keyframe.
        XCTAssertFalse(try LiveVideoFrame(payload: Data([0x02] + framePayload(key: false, pts: 0, nals: [[9]]).dropFirst())).isKeyframe)
    }

    // MARK: Geometry

    func testCropFillsTheViewAndMatchesScreenshotMapping() throws {
        let format = LiveStreamFormat(codedWidth: 1184, codedHeight: 2560, crop: LiveCrop(x: 2, y: 2, width: 1179, height: 2556))
        // The screenshot is the same crop, scaled to 1280 px at most: here 1179 × 2556 → 590 × 1279.
        let screenshot = PlaneSize(width: 590, height: 1279)
        XCTAssertTrue(format.matchesAspect(of: screenshot))
        XCTAssertFalse(format.matchesAspect(of: PlaneSize(width: 1279, height: 590)), "rotated picture")
        let container = PlaneSize(width: 500, height: 700)
        let rect = try XCTUnwrap(aspectFitRect(content: screenshot, in: container))
        // The video view sits on the screenshot's rectangle; the crop lands exactly on its bounds.
        let video = format.videoFrame(in: PlaneSize(width: rect.width, height: rect.height))
        let sx = rect.width / 1179, sy = rect.height / 2556
        XCTAssertEqual(video.x + 2 * sx, 0, accuracy: 1e-9)
        XCTAssertEqual(video.y + 2 * sy, 0, accuracy: 1e-9)
        XCTAssertEqual(video.x + (2 + 1179) * sx, rect.width, accuracy: 1e-9)
        XCTAssertEqual(video.y + (2 + 2556) * sy, rect.height, accuracy: 1e-9)
        XCTAssertEqual(video.width, 1184 * sx, accuracy: 1e-9)
        // A click mapped through the screenshot geometry hits the device point drawn under it in the video.
        let geometry = PreviewGeometry(image: screenshot, logical: PlaneSize(width: 393, height: 852), transform: ImageTransform(a: 393.0 / 590, b: 0, c: 0, d: 852.0 / 1279, tx: 0, ty: 0), orientation: "portrait")
        let click = PlanePoint(x: rect.x + rect.width * 0.25, y: rect.y + rect.height * 0.75)
        let device = try XCTUnwrap(geometry.devicePoint(at: click, in: container))
        XCTAssertEqual(device.x, 393 * 0.25, accuracy: 0.01)
        XCTAssertEqual(device.y, 852 * 0.75, accuracy: 0.01)
        // The same click in video coordinates: a quarter across and three quarters down the crop.
        let codedX = (click.x - rect.x - video.x) / sx, codedY = (click.y - rect.y - video.y) / sy
        XCTAssertEqual(codedX, 2 + 1179 * 0.25, accuracy: 1e-6)
        XCTAssertEqual(codedY, 2 + 2556 * 0.75, accuracy: 1e-6)
    }

    func testAspectFitMatchesPreviewGeometry() throws {
        let rect = try XCTUnwrap(aspectFitRect(content: PlaneSize(width: 100, height: 200), in: PlaneSize(width: 300, height: 300)))
        XCTAssertEqual(rect, PlaneRect(x: 75, y: 0, width: 150, height: 300))
        XCTAssertNil(aspectFitRect(content: PlaneSize(width: 0, height: 1), in: PlaneSize(width: 1, height: 1)))
    }

    // MARK: Request

    func testLiveRequestUsesSessionAndServerCredential() throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("tapmesh-live-tests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true); defer { try? FileManager.default.removeItem(at: folder) }
        let token = folder.appendingPathComponent("server.token"); try Data("fixture-server-credential".utf8).write(to: token)
        let file = folder.appendingPathComponent("connection.json")
        try JSONEncoder().encode(JSONValue.object(["api_url": .string("http://127.0.0.1:48793"), "agent_url": .string("http://127.0.0.1:48792"), "server_token_file": .string(token.path), "agent_token_file": .string(token.path), "approval_token_file": .string(token.path)])).write(to: file)
        let configuration = try ConnectionConfiguration.load(from: file)
        let request = try configuration.liveRequest(sessionID: "local session", deviceID: "ios:00008110-ABC/1")
        XCTAssertEqual(request.httpMethod, "GET")
        XCTAssertEqual(request.url?.absoluteString, "http://127.0.0.1:48793/api/live/ios:00008110-ABC%2F1?session_id=local%20session")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer fixture-server-credential")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Accept"), "application/vnd.tapmesh.live")
        XCTAssertNil(request.value(forHTTPHeaderField: "X-DeviceBridge-Local-Authorization"))
        XCTAssertGreaterThan(request.timeoutInterval, 24 * 3600)
        XCTAssertThrowsError(try configuration.liveRequest(sessionID: "", deviceID: "ios:A"))
    }

    // MARK: Policy

    func testOnlyReadyPhysicalDevicesAreTried() {
        XCTAssertTrue(LivePreviewPolicy.isEligible(backend: "physical", ready: true))
        XCTAssertFalse(LivePreviewPolicy.isEligible(backend: "physical", ready: false))
        XCTAssertFalse(LivePreviewPolicy.isEligible(backend: "simulator", ready: true))
    }

    func testBackoffAfterFailures() {
        var policy = LivePreviewPolicy()
        policy.select(deviceID: "ios:A")
        XCTAssertEqual(policy.delayBeforeStart(at: 0), 0)
        policy.started(at: 0)
        XCTAssertNil(policy.delayBeforeStart(at: 0), "never two streams")
        XCTAssertEqual(policy.screenshotInterval, LivePreviewPolicy.picturePollInterval, "pictures until the first frame")
        XCTAssertEqual(policy.failed(at: 1), 5)
        XCTAssertFalse(policy.showsVideo)
        XCTAssertEqual(policy.delayBeforeStart(at: 3), 3)
        XCTAssertEqual(policy.delayBeforeStart(at: 6), 0)
        policy.started(at: 6); XCTAssertEqual(policy.failed(at: 7), 15)
        policy.started(at: 22); XCTAssertEqual(policy.failed(at: 23), 30)
        policy.started(at: 53); XCTAssertEqual(policy.failed(at: 54), 30)
        // A stream that played briefly doesn't reset the backoff.
        policy.started(at: 84); policy.firstFrame(at: 85)
        XCTAssertTrue(policy.showsVideo)
        XCTAssertEqual(policy.screenshotInterval, LivePreviewPolicy.livePollInterval)
        XCTAssertEqual(policy.failed(at: 90), 30)
        // One that played for a while does.
        policy.started(at: 120); policy.firstFrame(at: 121)
        XCTAssertEqual(policy.failed(at: 121 + LivePreviewPolicy.stableAfter), 5)
    }

    func testDeliberateStopHasNoPenaltyAndNewDeviceStartsClean() {
        var policy = LivePreviewPolicy()
        policy.select(deviceID: "ios:A")
        policy.started(at: 0); policy.firstFrame(at: 1); policy.stopped()
        XCTAssertEqual(policy.delayBeforeStart(at: 1), 0)
        policy.started(at: 1); policy.failed(at: 2)
        policy.select(deviceID: "ios:A")
        XCTAssertEqual(policy.delayBeforeStart(at: 2), 5, "same device keeps its backoff")
        policy.select(deviceID: "ios:B")
        XCTAssertEqual(policy.delayBeforeStart(at: 2), 0)
        XCTAssertEqual(policy.consecutiveFailures, 0)
        policy.firstFrame(at: 3)
        XCTAssertFalse(policy.showsVideo, "a frame without a running stream is ignored")
    }

    func testServiceRestartRetriesLiveVideoAtOnce() {
        var policy = LivePreviewPolicy()
        policy.select(deviceID: "ios:A")
        policy.started(at: 0)
        policy.failed(at: 1)
        policy.started(at: 6)
        policy.failed(at: 7)

        XCTAssertNotNil(policy.retryAt)
        policy.serviceRestarted()
        XCTAssertEqual(policy.deviceID, "ios:A")
        XCTAssertEqual(policy.consecutiveFailures, 0)
        XCTAssertEqual(policy.delayBeforeStart(at: 8), 0)
        XCTAssertEqual(policy.screenshotInterval, LivePreviewPolicy.picturePollInterval)
        policy.started(at: 8)
        XCTAssertEqual(policy.failed(at: 9), 5)
    }

    func testSilence() {
        XCTAssertFalse(LivePreviewPolicy.isSilent(startedAt: 0, lastMessageAt: nil, now: 9))
        XCTAssertTrue(LivePreviewPolicy.isSilent(startedAt: 0, lastMessageAt: nil, now: 10.5))
        XCTAssertFalse(LivePreviewPolicy.isSilent(startedAt: 0, lastMessageAt: 20, now: 23))
        XCTAssertTrue(LivePreviewPolicy.isSilent(startedAt: 0, lastMessageAt: 20, now: 23.1))
    }

    func testDecodeFailuresGiveUpOnlyWhenRepeated() {
        var health = LiveDecodeHealth()
        XCTAssertFalse(health.recordFailure(at: 0))
        XCTAssertFalse(health.recordFailure(at: 4))
        XCTAssertFalse(health.recordFailure(at: 12), "the first one is outside the window")
        XCTAssertTrue(health.recordFailure(at: 13))
    }
}
