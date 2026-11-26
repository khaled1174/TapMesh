import AppKit
import AVFoundation
import CoreMedia
import TapMeshCore

/// Why a live stream ended without TapMesh asking it to. The preview always falls back to
/// pictures quietly; the reason is only for the tooltip and debugging.
enum LiveVideoEnd: Sendable, Equatable {
    /// The service answered with something other than a stream (404 unknown device, 409 `live_unavailable`…).
    case refused(status: Int)
    case connectionLost
    case protocolError
    /// The hardware decoder kept failing, or no keyframe came to resume after a failure.
    case decodeFailed
    /// No message for longer than `LivePreviewPolicy.silenceLimit`.
    case silent
    /// END, with the service's reason.
    case serviceEnded(String?)
}

enum LiveVideoEvent: Sendable {
    /// FORMAT: sent first and again when the screen's size or orientation changes.
    case format(LiveStreamFormat)
    /// The first frame was decoded and can be shown in place of the last picture.
    case firstFrame
    /// Sent at most once; never after `stop()`.
    case ended(LiveVideoEnd)
}

/// One live video stream from the service for one device, decoded in hardware by an
/// `AVSampleBufferDisplayLayer` (through its `sampleBufferRenderer`, macOS 14+).
///
/// Network callbacks, parsing, sample buffer creation and enqueueing all run on one private
/// serial queue; the main thread only hosts the layer and receives `LiveVideoEvent`s, in order.
/// Every mutable property below is touched only on `queue`.
final class LiveVideoSession: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    let deviceID: String
    let epoch: String
    /// Shown by `LiveVideoView`. Its renderer is fed from `queue`.
    @MainActor let displayLayer: AVSampleBufferDisplayLayer

    private let renderer: AVSampleBufferVideoRenderer
    private let request: URLRequest
    private let onEvent: @MainActor @Sendable (LiveVideoEvent) -> Void
    private let queue = DispatchQueue(label: "TapMesh.LiveVideoSession", qos: .userInteractive)
    private let delegateQueue = OperationQueue()

    private var urlSession: URLSession?
    private var parser = LiveStreamParser()
    private var format: LiveStreamFormat?
    private var parameterSets: HEVCParameterSets?
    private var formatDescription: CMVideoFormatDescription?
    private var waitingForKeyframe = true
    /// When the first delta frame was dropped while waiting for a keyframe.
    private var waitingSince: Double?
    /// A frame went to the decoder since the last flush, so a new failure is a new one.
    private var enqueuedSinceFlush = false
    private var health = LiveDecodeHealth()
    private var startedAt: Double = 0
    private var lastMessageAt: Double?
    private var firstKeyframeAt: Double?
    private var ready = false
    private var finished = false
    private var completed = false
    private var closeWaiters: [CheckedContinuation<Void, Never>] = []
    private var watchdog: DispatchSourceTimer?
    private var observers: [NSObjectProtocol] = []

    /// A first frame that never becomes ready for display within this time counts as a decode failure.
    private static let readyTimeout: Double = 5

    @MainActor
    init(request: URLRequest, deviceID: String, epoch: String, onEvent: @escaping @MainActor @Sendable (LiveVideoEvent) -> Void) {
        self.request = request; self.deviceID = deviceID; self.epoch = epoch; self.onEvent = onEvent
        let layer = AVSampleBufferDisplayLayer()
        // The crop is placed by `LiveVideoView`, which sizes the layer from the coded frame, so
        // the picture is stretched to the layer exactly, never letterboxed inside it.
        layer.videoGravity = .resize
        layer.backgroundColor = NSColor.clear.cgColor
        layer.preventsDisplaySleepDuringVideoPlayback = false
        displayLayer = layer
        renderer = layer.sampleBufferRenderer
        super.init()
        delegateQueue.maxConcurrentOperationCount = 1
        delegateQueue.underlyingQueue = queue
        delegateQueue.name = "TapMesh.LiveVideoSession.delegate"
    }

    private static var now: Double { ProcessInfo.processInfo.systemUptime }

    func start() {
        queue.async { [self] in
            guard !finished else { return }
            let center = NotificationCenter.default
            // Posted on arbitrary threads; handled on `queue` through `delegateQueue`.
            observers = [
                center.addObserver(forName: AVSampleBufferVideoRenderer.didFailToDecodeNotification, object: renderer, queue: delegateQueue) { [weak self] _ in self?.decodeFailed() },
                center.addObserver(forName: AVSampleBufferVideoRenderer.requiresFlushToResumeDecodingDidChangeNotification, object: renderer, queue: delegateQueue) { [weak self] _ in
                    guard let self, self.renderer.requiresFlushToResumeDecoding else { return }
                    self.decodeFailed()
                },
            ]
            let configuration = URLSessionConfiguration.ephemeral
            configuration.timeoutIntervalForRequest = ConnectionConfiguration.liveTimeout
            configuration.timeoutIntervalForResource = ConnectionConfiguration.liveTimeout
            configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
            configuration.urlCache = nil
            configuration.httpMaximumConnectionsPerHost = 1
            // The session keeps its delegate (self) until it is invalidated in `close()`.
            let session = URLSession(configuration: configuration, delegate: self, delegateQueue: delegateQueue)
            urlSession = session
            startedAt = Self.now
            let timer = DispatchSource.makeTimerSource(queue: queue)
            timer.schedule(deadline: .now() + 0.25, repeating: 0.25)
            timer.setEventHandler { [weak self] in self?.tick() }
            timer.resume()
            watchdog = timer
            session.dataTask(with: request).resume()
        }
    }

    /// Stops the stream. No event is sent afterwards. Returns once the connection is closed
    /// (or after two seconds at most), so a new stream for the device never overlaps this one.
    func stop() async {
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            queue.async { [self] in
                finished = true
                close()
                if completed || urlSession == nil { completed = true; continuation.resume(); return }
                closeWaiters.append(continuation)
                queue.asyncAfter(deadline: .now() + 2) { [self] in releaseCloseWaiters() }
            }
        }
    }

    // MARK: URLSessionDataDelegate (on `queue`)

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse, completionHandler: @escaping @Sendable (URLSession.ResponseDisposition) -> Void) {
        let http = response as? HTTPURLResponse
        let type = http?.value(forHTTPHeaderField: "Content-Type")?.lowercased() ?? ""
        guard let http, http.statusCode == 200, type.hasPrefix("application/vnd.tapmesh.live") else {
            completionHandler(.cancel)
            end(.refused(status: http?.statusCode ?? 0))
            return
        }
        completionHandler(.allow)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        guard !finished else { return }
        let messages: [LiveStreamMessage]
        do { messages = try parser.feed(data) } catch { end(.protocolError); return }
        for message in messages {
            guard !finished else { return }
            lastMessageAt = Self.now
            handle(message)
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        end(.connectionLost)
        completed = true
        releaseCloseWaiters()
        session.finishTasksAndInvalidate()
    }

    // MARK: Messages (on `queue`)

    private func handle(_ message: LiveStreamMessage) {
        switch message {
        case .format(let value):
            guard value != format else { return }
            format = value
            send(.format(value))
        case .parameterSets(let sets):
            guard sets != parameterSets else { return }
            guard let description = Self.formatDescription(for: sets) else { end(.protocolError); return }
            parameterSets = sets; formatDescription = description
            // New parameters apply from the next keyframe.
            waitForKeyframe()
        case .frame(let frame):
            enqueue(frame)
        case .heartbeat, .unknown:
            break
        case .end(let reason):
            end(.serviceEnded(reason))
        }
    }

    private func enqueue(_ frame: LiveVideoFrame) {
        guard let formatDescription, format != nil else { return }
        if renderer.status == .failed || renderer.requiresFlushToResumeDecoding { decodeFailed(); guard !finished else { return } }
        if waitingForKeyframe {
            guard frame.isKeyframe else {
                // Delta frames can't be decoded without their keyframe; `tick` gives up if none comes.
                if waitingSince == nil { waitingSince = Self.now }
                return
            }
            waitingForKeyframe = false; waitingSince = nil
        }
        guard let sample = Self.sampleBuffer(for: frame, format: formatDescription) else { end(.protocolError); return }
        renderer.enqueue(sample)
        enqueuedSinceFlush = true
        if frame.isKeyframe, firstKeyframeAt == nil { firstKeyframeAt = Self.now }
    }

    private func waitForKeyframe() { waitingForKeyframe = true }

    /// Flushes and waits for the next keyframe; gives up when failures repeat. The same failure
    /// can be reported twice (notification and status); only one after new frames counts.
    private func decodeFailed() {
        guard !finished else { return }
        renderer.flush()
        waitForKeyframe()
        guard enqueuedSinceFlush else { return }
        enqueuedSinceFlush = false
        if health.recordFailure(at: Self.now) { end(.decodeFailed) }
    }

    private func tick() {
        guard !finished else { return }
        let now = Self.now
        if LivePreviewPolicy.isSilent(startedAt: startedAt, lastMessageAt: lastMessageAt, now: now) { end(.silent); return }
        if let waitingSince, now - waitingSince > LiveDecodeHealth.keyframeWait { end(.decodeFailed); return }
        guard !ready, let firstKeyframeAt else { return }
        let elapsed = now - firstKeyframeAt
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            MainActor.assumeIsolated {
                let shown = self.displayLayer.isReadyForDisplay
                // Only a layer that is on screen is expected to become ready in time.
                let onScreen = self.displayLayer.superlayer != nil
                self.queue.async { self.readinessChecked(shown: shown, onScreen: onScreen, elapsed: elapsed) }
            }
        }
    }

    private func readinessChecked(shown: Bool, onScreen: Bool, elapsed: Double) {
        guard !finished, !ready else { return }
        if shown { ready = true; send(.firstFrame) }
        else if onScreen, elapsed > Self.readyTimeout { end(.decodeFailed) }
    }

    private func end(_ reason: LiveVideoEnd) {
        guard !finished else { return }
        finished = true
        close()
        send(.ended(reason))
    }

    private func close() {
        watchdog?.cancel(); watchdog = nil
        for observer in observers { NotificationCenter.default.removeObserver(observer) }
        observers = []
        urlSession?.invalidateAndCancel()
    }

    private func releaseCloseWaiters() {
        let waiters = closeWaiters; closeWaiters = []
        for waiter in waiters { waiter.resume() }
    }

    /// Delivered on the main queue in the order sent.
    private func send(_ event: LiveVideoEvent) {
        let onEvent = onEvent
        DispatchQueue.main.async { MainActor.assumeIsolated { onEvent(event) } }
    }

    // MARK: Core Media

    static func formatDescription(for sets: HEVCParameterSets) -> CMVideoFormatDescription? {
        let joined = sets.sets.reduce(into: [UInt8]()) { $0.append(contentsOf: $1) }
        let sizes = sets.sets.map(\.count)
        var offsets: [Int] = []; var offset = 0
        for size in sizes { offsets.append(offset); offset += size }
        var description: CMVideoFormatDescription?
        let status = joined.withUnsafeBufferPointer { buffer -> OSStatus in
            guard let base = buffer.baseAddress else { return -1 }
            let pointers = offsets.map { base + $0 }
            return pointers.withUnsafeBufferPointer { pointerBuffer in
                sizes.withUnsafeBufferPointer { sizeBuffer in
                    CMVideoFormatDescriptionCreateFromHEVCParameterSets(allocator: kCFAllocatorDefault, parameterSetCount: sizes.count,
                                                                        parameterSetPointers: pointerBuffer.baseAddress!, parameterSetSizes: sizeBuffer.baseAddress!,
                                                                        nalUnitHeaderLength: 4, extensions: nil, formatDescriptionOut: &description)
                }
            }
        }
        return status == noErr ? description : nil
    }

    /// One access unit as a ready sample buffer, shown as soon as it is decoded.
    static func sampleBuffer(for frame: LiveVideoFrame, format: CMVideoFormatDescription) -> CMSampleBuffer? {
        let length = frame.data.count
        var block: CMBlockBuffer?
        guard CMBlockBufferCreateWithMemoryBlock(allocator: kCFAllocatorDefault, memoryBlock: nil, blockLength: length, blockAllocator: kCFAllocatorDefault,
                                                 customBlockSource: nil, offsetToData: 0, dataLength: length, flags: kCMBlockBufferAssureMemoryNowFlag,
                                                 blockBufferOut: &block) == kCMBlockBufferNoErr, let block else { return nil }
        let copied = frame.data.withUnsafeBytes { bytes -> OSStatus in
            guard let base = bytes.baseAddress else { return -1 }
            return CMBlockBufferReplaceDataBytes(with: base, blockBuffer: block, offsetIntoDestination: 0, dataLength: length)
        }
        guard copied == kCMBlockBufferNoErr else { return nil }
        let pts = CMTime(value: CMTimeValue(clamping: frame.presentationMicroseconds), timescale: 1_000_000)
        var timing = CMSampleTimingInfo(duration: .invalid, presentationTimeStamp: pts, decodeTimeStamp: .invalid)
        var size = length
        var sample: CMSampleBuffer?
        guard CMSampleBufferCreateReady(allocator: kCFAllocatorDefault, dataBuffer: block, formatDescription: format, sampleCount: 1,
                                        sampleTimingEntryCount: 1, sampleTimingArray: &timing, sampleSizeEntryCount: 1, sampleSizeArray: &size,
                                        sampleBufferOut: &sample) == noErr, let sample else { return nil }
        guard let attachments = CMSampleBufferGetSampleAttachmentsArray(sample, createIfNecessary: true), CFArrayGetCount(attachments) > 0 else { return nil }
        let dictionary = unsafeBitCast(CFArrayGetValueAtIndex(attachments, 0), to: CFMutableDictionary.self)
        func set(_ key: CFString, _ value: Bool) {
            CFDictionarySetValue(dictionary, Unmanaged.passUnretained(key).toOpaque(), Unmanaged.passUnretained(value ? kCFBooleanTrue : kCFBooleanFalse).toOpaque())
        }
        set(kCMSampleAttachmentKey_DisplayImmediately, true)
        set(kCMSampleAttachmentKey_NotSync, !frame.isKeyframe)
        set(kCMSampleAttachmentKey_DependsOnOthers, !frame.isKeyframe)
        return sample
    }
}
