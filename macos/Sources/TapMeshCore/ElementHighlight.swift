import Foundation

public struct PlaneRect: Equatable, Sendable {
    public let x: Double, y: Double, width: Double, height: Double
    public init(x: Double, y: Double, width: Double, height: Double) { self.x = x; self.y = y; self.width = width; self.height = height }
}

/// Where an inspected element sits on the displayed screenshot, for a visual outline only.
///
/// Element snapshots (`list_elements`) carry no image, only `logical_size` and `orientation`, and
/// their `bounds` are device logical points with a top-left origin, the same space the service
/// uses to resolve `{ref, snapshot_id}` taps (agent/safety.py `resolve`). The displayed frame maps
/// its image into that space with `image_to_device_transform`. So an element can be placed on the
/// frame only when both observations demonstrably describe the same logical screen. Every check
/// below must pass; otherwise the result is nil and nothing is drawn.
public enum ElementHighlight {
    /// The service's observation lifetime (agent/safety.py `ObservationStore(max_age=30)`).
    public static let defaultMaxAgeMilliseconds = 30_000.0
    /// Logical sizes are copied, not measured, so they match exactly; this only absorbs float noise.
    static let tolerance = 0.5

    /// - Parameters:
    ///   - bounds: the element's `bounds` object (`x`, `y`, `width`, `height` in device points).
    ///   - snapshot: the element snapshot the element came from.
    ///   - frame: the observation of the screenshot being displayed.
    ///   - deviceID, epoch: the currently selected device and its connection epoch.
    ///   - container: the size of the view the screenshot is aspect-fitted into.
    /// - Returns: the element's rectangle in the container's coordinates, or nil when the match
    ///   can't be established.
    public static func rect(bounds: JSONValue?, snapshot: Observation?, frame: Observation?, deviceID: String?, epoch: String?, container: PlaneSize) -> PlaneRect? {
        guard let snapshot, let frame, snapshot.snapshotID != nil, let geometry = frame.geometry,
              let deviceID, let epoch,
              snapshot.deviceID == deviceID, frame.deviceID == deviceID,
              snapshot.epoch == epoch, frame.epoch == epoch else { return nil }

        // Same logical screen, in the same orientation.
        guard let snapshotLogical = snapshot.logicalSize, let frameLogical = frame.logicalSize,
              same(snapshotLogical, frameLogical), same(frameLogical, geometry.logical),
              snapshot.orientation == frame.orientation, frame.orientation == geometry.orientation else { return nil }

        // Taken close enough together that the service would still accept the snapshot's references.
        guard let snapshotTime = parseTimestamp(snapshot.observedAt), let frameTime = parseTimestamp(frame.observedAt) else { return nil }
        var maxAge = defaultMaxAgeMilliseconds
        if let declared = snapshot.freshness?["max_age_ms"]?.doubleValue, declared.isFinite, declared > 0 { maxAge = min(declared, maxAge) }
        guard abs(snapshotTime.timeIntervalSince(frameTime)) * 1000 <= maxAge else { return nil }

        // The frame's transform must be usable and must cover exactly the logical screen.
        let image = geometry.image, logical = geometry.logical, t = geometry.transform
        guard [image.width, image.height, logical.width, logical.height, container.width, container.height].allSatisfy({ $0.isFinite && $0 > 0 }) else { return nil }
        let determinant = t.a * t.d - t.b * t.c
        guard determinant.isFinite, abs(determinant) > 1e-12 else { return nil }
        let imageCorners = [(0.0, 0.0), (image.width, 0.0), (0.0, image.height), (image.width, image.height)]
            .map { (t.a * $0.0 + t.c * $0.1 + t.tx, t.b * $0.0 + t.d * $0.1 + t.ty) }
        guard let covered = box(imageCorners),
              abs(covered.x) <= tolerance, abs(covered.y) <= tolerance,
              abs(covered.width - logical.width) <= tolerance, abs(covered.height - logical.height) <= tolerance else { return nil }

        // The element must be a real area inside the logical screen.
        guard let x = bounds?["x"]?.doubleValue, let y = bounds?["y"]?.doubleValue,
              let width = bounds?["width"]?.doubleValue, let height = bounds?["height"]?.doubleValue,
              [x, y, width, height].allSatisfy(\.isFinite), width > 0, height > 0,
              x >= -0.01, y >= -0.01, x + width <= logical.width + 0.01, y + height <= logical.height + 0.01 else { return nil }

        // Device points -> image pixels (inverse transform) -> aspect-fitted container.
        let inverse = (a: t.d / determinant, b: -t.b / determinant, c: -t.c / determinant, d: t.a / determinant)
        let elementCorners = [(x, y), (x + width, y), (x, y + height), (x + width, y + height)].map { point -> (Double, Double) in
            let dx = point.0 - t.tx, dy = point.1 - t.ty
            return (inverse.a * dx + inverse.c * dy, inverse.b * dx + inverse.d * dy)
        }
        guard let inImage = box(elementCorners) else { return nil }
        let scale = min(container.width / image.width, container.height / image.height)
        let originX = (container.width - image.width * scale) / 2, originY = (container.height - image.height * scale) / 2
        let result = PlaneRect(x: originX + inImage.x * scale, y: originY + inImage.y * scale, width: inImage.width * scale, height: inImage.height * scale)
        guard [result.x, result.y, result.width, result.height].allSatisfy(\.isFinite) else { return nil }
        return result
    }

    /// The service writes Python `datetime.isoformat()`: with or without microseconds.
    static func parseTimestamp(_ text: String) -> Date? {
        guard !text.isEmpty else { return nil }
        let fractional = ISO8601DateFormatter(); fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = fractional.date(from: text) { return date }
        let whole = ISO8601DateFormatter(); whole.formatOptions = [.withInternetDateTime]
        return whole.date(from: text)
    }

    private static func same(_ lhs: PlaneSize, _ rhs: PlaneSize) -> Bool {
        abs(lhs.width - rhs.width) <= tolerance && abs(lhs.height - rhs.height) <= tolerance
    }

    private static func box(_ points: [(Double, Double)]) -> PlaneRect? {
        guard let minX = points.map(\.0).min(), let maxX = points.map(\.0).max(),
              let minY = points.map(\.1).min(), let maxY = points.map(\.1).max(),
              [minX, maxX, minY, maxY].allSatisfy(\.isFinite) else { return nil }
        return PlaneRect(x: minX, y: minY, width: maxX - minX, height: maxY - minY)
    }
}
