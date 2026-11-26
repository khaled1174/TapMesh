import Foundation

// The live preview stream: `GET /api/live/{device_id}?session_id=…` answers with
// `Content-Type: application/vnd.tapmesh.live`, a sequence of messages
// `[type: UInt8][length: UInt32 big-endian][payload]`. Everything here is pure, so the
// wire format is tested without a device, a network or a decoder.

public enum LiveStreamError: Error, Equatable, Sendable {
    /// A message declared a payload larger than `LiveStreamParser.maximumPayload`.
    case payloadTooLarge(type: UInt8, length: Int)
    /// A message's payload doesn't have the shape its type requires.
    case malformed(String)
}

/// The visible display area inside the coded video frame, in coded pixels.
public struct LiveCrop: Equatable, Sendable {
    public let x: Double, y: Double, width: Double, height: Double
    public init(x: Double, y: Double, width: Double, height: Double) { self.x = x; self.y = y; self.width = width; self.height = height }
}

/// FORMAT (0x01): the coded size of the video and the part of it that is the device screen.
public struct LiveStreamFormat: Equatable, Sendable {
    public static let supportedVersion = 1
    public let version: Int
    public let codec: String
    public let codedWidth: Double
    public let codedHeight: Double
    public let crop: LiveCrop

    public init(version: Int = supportedVersion, codec: String = "hevc", codedWidth: Double, codedHeight: Double, crop: LiveCrop) {
        self.version = version; self.codec = codec; self.codedWidth = codedWidth; self.codedHeight = codedHeight; self.crop = crop
    }

    /// Decodes and checks a FORMAT payload. A missing crop means the whole coded frame.
    public init(payload: Data) throws {
        guard let json = try? JSONDecoder().decode(JSONValue.self, from: payload) else { throw LiveStreamError.malformed("FORMAT is not JSON") }
        guard let version = json["version"]?.doubleValue, version == Double(Self.supportedVersion) else { throw LiveStreamError.malformed("unsupported FORMAT version") }
        guard let codec = json["codec"]?.stringValue, codec == "hevc" else { throw LiveStreamError.malformed("unsupported codec") }
        guard let width = json["coded_width"]?.doubleValue, let height = json["coded_height"]?.doubleValue,
              width.isFinite, height.isFinite, width >= 1, height >= 1, width <= 16_384, height <= 16_384 else { throw LiveStreamError.malformed("invalid coded size") }
        let crop: LiveCrop
        if let value = json["crop"], value != .null {
            guard let x = value["x"]?.doubleValue, let y = value["y"]?.doubleValue, let w = value["width"]?.doubleValue, let h = value["height"]?.doubleValue,
                  [x, y, w, h].allSatisfy(\.isFinite), x >= 0, y >= 0, w >= 1, h >= 1,
                  // Half a pixel of slack for rounding on the service.
                  x + w <= width + 0.5, y + h <= height + 0.5 else { throw LiveStreamError.malformed("invalid crop") }
            crop = LiveCrop(x: x, y: y, width: w, height: h)
        } else {
            crop = LiveCrop(x: 0, y: 0, width: width, height: height)
        }
        self.init(version: Int(version), codec: codec, codedWidth: width, codedHeight: height, crop: crop)
    }

    /// Where the whole coded frame goes, in a view whose bounds show exactly the crop
    /// (top-left origin, same units as `view`). The crop rectangle lands on the view's bounds;
    /// everything outside it falls outside the view and is clipped.
    public func videoFrame(in view: PlaneSize) -> PlaneRect {
        let sx = view.width / crop.width, sy = view.height / crop.height
        return PlaneRect(x: -crop.x * sx, y: -crop.y * sy, width: codedWidth * sx, height: codedHeight * sy)
    }

    /// True when the cropped video and a screenshot of `image` size have the same shape, so the
    /// video can be drawn in the screenshot's rectangle and clicks map through the screenshot's
    /// geometry. `tolerance` is relative (0.02 = 2 %), for rounding when screenshots are scaled.
    public func matchesAspect(of image: PlaneSize, tolerance: Double = 0.02) -> Bool {
        guard image.width > 0, image.height > 0, crop.width > 0, crop.height > 0 else { return false }
        let video = crop.width / crop.height, picture = image.width / image.height
        return abs(video - picture) <= tolerance * picture
    }
}

/// The rectangle a `content`-shaped picture occupies when aspect-fitted and centered in
/// `container`; the same placement SwiftUI's `.aspectRatio(contentMode: .fit)` and
/// `PreviewGeometry.devicePoint` use.
public func aspectFitRect(content: PlaneSize, in container: PlaneSize) -> PlaneRect? {
    guard [content.width, content.height, container.width, container.height].allSatisfy({ $0.isFinite && $0 > 0 }) else { return nil }
    let scale = min(container.width / content.width, container.height / content.height)
    let width = content.width * scale, height = content.height * scale
    return PlaneRect(x: (container.width - width) / 2, y: (container.height - height) / 2, width: width, height: height)
}

/// PARAMS (0x02): the HEVC parameter sets, without start codes, in the order VPS, SPS, PPS.
public struct HEVCParameterSets: Equatable, Sendable {
    public let sets: [Data]
    public init(sets: [Data]) { self.sets = sets }
    /// Each set is prefixed by its 4-byte big-endian length. At least VPS, SPS and PPS are required.
    public init(payload: Data) throws {
        var sets: [Data] = []
        var bytes = payload[...]
        while !bytes.isEmpty {
            guard bytes.count >= 4 else { throw LiveStreamError.malformed("truncated parameter set length") }
            let length = Int(readUInt32(bytes, at: bytes.startIndex))
            bytes = bytes.dropFirst(4)
            guard length > 0, length <= bytes.count else { throw LiveStreamError.malformed("invalid parameter set length") }
            sets.append(Data(bytes.prefix(length)))
            bytes = bytes.dropFirst(length)
        }
        guard sets.count >= 3, sets.count <= 16 else { throw LiveStreamError.malformed("expected VPS, SPS and PPS") }
        self.sets = sets
    }
    public var vps: Data { sets[0] }
    public var sps: Data { sets[1] }
    public var pps: Data { sets[2] }
}

/// FRAME (0x03): one complete HEVC access unit in AVCC form (NAL units with 4-byte lengths).
public struct LiveVideoFrame: Equatable, Sendable {
    public let isKeyframe: Bool
    public let presentationMicroseconds: UInt64
    public let data: Data
    public init(isKeyframe: Bool, presentationMicroseconds: UInt64, data: Data) { self.isKeyframe = isKeyframe; self.presentationMicroseconds = presentationMicroseconds; self.data = data }
    /// `[flags: UInt8, bit0 = keyframe][pts_us: UInt64 BE][AVCC]`. The AVCC lengths must cover the data exactly.
    public init(payload: Data) throws {
        guard payload.count > 9 else { throw LiveStreamError.malformed("FRAME too short") }
        let start = payload.startIndex
        let flags = payload[start]
        var pts: UInt64 = 0
        for byte in payload[(start + 1)..<(start + 9)] { pts = pts << 8 | UInt64(byte) }
        let body = payload[(start + 9)...]
        var index = body.startIndex
        while index < body.endIndex {
            guard body.endIndex - index >= 4 else { throw LiveStreamError.malformed("truncated NAL length") }
            let length = Int(readUInt32(body, at: index))
            guard length > 0, length <= body.endIndex - index - 4 else { throw LiveStreamError.malformed("invalid NAL length") }
            index += 4 + length
        }
        self.init(isKeyframe: flags & 0x01 != 0, presentationMicroseconds: pts, data: Data(body))
    }
}

public enum LiveStreamMessage: Equatable, Sendable {
    case format(LiveStreamFormat)
    case parameterSets(HEVCParameterSets)
    case frame(LiveVideoFrame)
    case heartbeat
    /// END (0x05), with the service's reason when it gave one.
    case end(reason: String?)
    /// A message type this version doesn't know. Skipped, so the service can add types later.
    case unknown(type: UInt8)

    public static let formatType: UInt8 = 0x01, parametersType: UInt8 = 0x02, frameType: UInt8 = 0x03, heartbeatType: UInt8 = 0x04, endType: UInt8 = 0x05

    /// Decodes one message's payload by type.
    public init(type: UInt8, payload: Data) throws {
        switch type {
        case Self.formatType: self = .format(try LiveStreamFormat(payload: payload))
        case Self.parametersType: self = .parameterSets(try HEVCParameterSets(payload: payload))
        case Self.frameType: self = .frame(try LiveVideoFrame(payload: payload))
        case Self.heartbeatType: self = .heartbeat
        case Self.endType:
            let reason = payload.isEmpty ? nil : (try? JSONDecoder().decode(JSONValue.self, from: payload))?["reason"]?.stringValue
            self = .end(reason: reason)
        default: self = .unknown(type: type)
        }
    }
}

/// Splits the byte stream into messages. Feed it chunks exactly as they arrive: a message may be
/// split across chunks and a chunk may hold many messages. After it throws, the stream is unusable.
public struct LiveStreamParser: Sendable {
    /// Larger payloads are a protocol error, never a reason to buffer without limit.
    public static let maximumPayload = 16 * 1024 * 1024
    static let headerLength = 5
    private var buffer: [UInt8] = []
    private var readIndex = 0
    public init() {}

    /// Bytes received but not yet part of a complete message.
    public var pendingByteCount: Int { buffer.count - readIndex }

    public mutating func feed(_ chunk: Data) throws -> [LiveStreamMessage] {
        buffer.append(contentsOf: chunk)
        var messages: [LiveStreamMessage] = []
        while buffer.count - readIndex >= Self.headerLength {
            let type = buffer[readIndex]
            let length = Int(UInt32(buffer[readIndex + 1]) << 24 | UInt32(buffer[readIndex + 2]) << 16 | UInt32(buffer[readIndex + 3]) << 8 | UInt32(buffer[readIndex + 4]))
            guard length <= Self.maximumPayload else { throw LiveStreamError.payloadTooLarge(type: type, length: length) }
            guard buffer.count - readIndex - Self.headerLength >= length else { break }
            let start = readIndex + Self.headerLength
            let payload = Data(buffer[start..<(start + length)])
            readIndex = start + length
            messages.append(try LiveStreamMessage(type: type, payload: payload))
        }
        // Drop consumed bytes once they are most of the buffer, so it never grows without bound.
        if readIndex == buffer.count { buffer.removeAll(keepingCapacity: true); readIndex = 0 }
        else if readIndex > 0, readIndex >= buffer.count / 2 { buffer.removeSubrange(0..<readIndex); readIndex = 0 }
        return messages
    }
}

/// Big-endian; `index` is an absolute index (Data slices keep their parent's indices).
private func readUInt32(_ bytes: Data, at index: Data.Index) -> UInt32 {
    UInt32(bytes[index]) << 24 | UInt32(bytes[index + 1]) << 16 | UInt32(bytes[index + 2]) << 8 | UInt32(bytes[index + 3])
}

// MARK: Live or pictures

/// Decides between live video and the screenshot loop for the selected device. Times are
/// seconds on any monotonic clock. Live video is tried for eligible devices; after a failure the
/// preview goes back to pictures and live is tried again after 5 s, then 15 s, then every 30 s.
/// A stream that played for `stableAfter` seconds starts the sequence again from 5 s.
public struct LivePreviewPolicy: Sendable {
    public static let retryDelays: [Double] = [5, 15, 30]
    /// No message at all for this long ends a stream that has started sending.
    public static let silenceLimit: Double = 3
    /// The first message (after the request is sent) may take this long: the service may have to
    /// start the device's capture first.
    public static let connectTimeout: Double = 10
    public static let stableAfter: Double = 30
    /// Seconds between screenshots: while video plays they only keep an observation for clicks
    /// and the inspector; otherwise the pictures are the preview.
    public static let livePollInterval: Double = 2
    public static let picturePollInterval: Double = 0.25

    public enum Phase: Equatable, Sendable {
        /// Pictures only. `retryAt` says when live may be tried again.
        case idle
        /// Requested; the last picture stays on screen until the first frame is decoded.
        case connecting
        /// Video is on screen.
        case playing
    }

    public private(set) var phase: Phase = .idle
    public private(set) var deviceID: String?
    public private(set) var consecutiveFailures = 0
    public private(set) var retryAt: Double?
    private var playingSince: Double?

    public init() {}

    /// Only wired physical iPhones and iPads stream; the service answers 409 for any other device
    /// (including Wi-Fi), which `failed` handles like any other failure.
    public static func isEligible(backend: String, ready: Bool) -> Bool { ready && backend == "physical" }

    /// A different device starts with a clean slate; the same device keeps its backoff.
    public mutating func select(deviceID: String?) {
        guard deviceID != self.deviceID else { return }
        self = LivePreviewPolicy()
        self.deviceID = deviceID
    }

    /// Seconds to wait before live may be tried, 0 when it may be tried now, nil while a stream runs.
    public func delayBeforeStart(at now: Double) -> Double? {
        guard phase == .idle else { return nil }
        guard let retryAt else { return 0 }
        return max(0, retryAt - now)
    }

    public mutating func started(at now: Double) { phase = .connecting; playingSince = nil }

    public mutating func firstFrame(at now: Double) {
        guard phase == .connecting else { return }
        phase = .playing; playingSince = now
    }

    /// The stream ended without being asked to (refused, dropped, silent, undecodable).
    /// Returns the seconds until live may be tried again.
    @discardableResult
    public mutating func failed(at now: Double) -> Double {
        if let playingSince, now - playingSince >= Self.stableAfter { consecutiveFailures = 0 }
        let delay = Self.retryDelays[min(consecutiveFailures, Self.retryDelays.count - 1)]
        consecutiveFailures += 1
        retryAt = now + delay
        phase = .idle; playingSince = nil
        return delay
    }

    /// TapMesh stopped the stream itself (preview hidden, device changed…): no penalty.
    public mutating func stopped() { phase = .idle; playingSince = nil }

    public mutating func serviceRestarted() {
        stopped()
        consecutiveFailures = 0
        retryAt = nil
    }

    public var showsVideo: Bool { phase == .playing }
    public var screenshotInterval: Double { showsVideo ? Self.livePollInterval : Self.picturePollInterval }

    /// True when a running stream should be treated as dead.
    /// - Parameters:
    ///   - startedAt: when the request was sent.
    ///   - lastMessageAt: when the last complete message arrived, nil before the first one.
    public static func isSilent(startedAt: Double, lastMessageAt: Double?, now: Double) -> Bool {
        if let lastMessageAt { return now - lastMessageAt > silenceLimit }
        return now - startedAt > connectTimeout
    }
}

/// Counts decoder failures. Each one is answered by a flush and a wait for the next keyframe;
/// `limit` failures within `window` seconds mean decoding keeps failing and live should stop.
public struct LiveDecodeHealth: Sendable {
    public static let limit = 3
    public static let window: Double = 10
    /// How long the decoder may wait for a keyframe after a flush before the stream is given up.
    public static let keyframeWait: Double = 5
    private var failures: [Double] = []
    public init() {}
    /// Records a failure. Returns true when live should be given up.
    public mutating func recordFailure(at now: Double) -> Bool {
        failures = failures.filter { now - $0 < Self.window } + [now]
        return failures.count >= Self.limit
    }
}
