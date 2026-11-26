import AppKit
import AVFoundation
import SwiftUI
import TapMeshCore

/// Hosts a live stream's display layer so that exactly the stream's crop fills this view.
/// The view is placed on the same rectangle as the screenshot it replaces; it never takes
/// clicks, so the screenshot's gestures and coordinate math below it keep working.
struct LiveVideoView: NSViewRepresentable {
    let session: LiveVideoSession
    let format: LiveStreamFormat?

    func makeNSView(context: Context) -> LiveVideoHostView { LiveVideoHostView() }

    func updateNSView(_ view: LiveVideoHostView, context: Context) {
        view.show(session.displayLayer, format: format)
    }

    static func dismantleNSView(_ view: LiveVideoHostView, coordinator: ()) {
        view.show(nil, format: nil)
    }
}

/// A layer-hosting view: its root layer clips to the bounds and holds the video layer, which is
/// sized to the whole coded frame and offset so the crop lands on the bounds.
final class LiveVideoHostView: NSView {
    private let root = CALayer()
    private weak var video: AVSampleBufferDisplayLayer?
    private var format: LiveStreamFormat?

    init() {
        super.init(frame: .zero)
        root.masksToBounds = true
        root.backgroundColor = NSColor.clear.cgColor
        // Layer-hosting (layer set before wantsLayer): AppKit leaves the root's geometry alone,
        // so its sublayers use the standard bottom-left origin, which `layoutVideo` accounts for.
        layer = root
        wantsLayer = true
    }

    @available(*, unavailable) required init?(coder: NSCoder) { fatalError("init(coder:) is not supported") }

    override func hitTest(_ point: NSPoint) -> NSView? { nil }
    override var isOpaque: Bool { false }

    func show(_ layer: AVSampleBufferDisplayLayer?, format: LiveStreamFormat?) {
        if video !== layer {
            // A newer view may already host this layer; only take it out of this view's own root.
            if let video, video.superlayer === root { video.removeFromSuperlayer() }
            if let layer { root.addSublayer(layer) }
            video = layer
        }
        self.format = format
        layoutVideo()
    }

    override func layout() {
        super.layout()
        layoutVideo()
    }

    override func viewDidChangeBackingProperties() {
        super.viewDidChangeBackingProperties()
        root.contentsScale = window?.backingScaleFactor ?? 2
        video?.contentsScale = root.contentsScale
    }

    private func layoutVideo() {
        guard let video else { return }
        CATransaction.begin(); CATransaction.setDisableActions(true); defer { CATransaction.commit() }
        root.isGeometryFlipped = false
        let size = bounds.size
        guard let format, size.width > 0, size.height > 0 else { video.frame = bounds; return }
        // `videoFrame` uses a top-left origin; the root layer's is bottom-left.
        let frame = format.videoFrame(in: PlaneSize(width: size.width, height: size.height))
        video.frame = CGRect(x: frame.x, y: size.height - frame.y - frame.height, width: frame.width, height: frame.height)
    }
}
