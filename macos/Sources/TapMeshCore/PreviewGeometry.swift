import Foundation

public struct PlaneSize: Equatable, Sendable { public let width: Double; public let height: Double; public init(width: Double, height: Double) { self.width = width; self.height = height } }
public struct PlanePoint: Equatable, Sendable { public let x: Double; public let y: Double; public init(x: Double, y: Double) { self.x = x; self.y = y } }
public struct ImageTransform: Equatable, Sendable {
    public let a: Double, b: Double, c: Double, d: Double, tx: Double, ty: Double
    public init(a: Double, b: Double, c: Double, d: Double, tx: Double, ty: Double) { self.a = a; self.b = b; self.c = c; self.d = d; self.tx = tx; self.ty = ty }
    public static let identity = Self(a: 1, b: 0, c: 0, d: 1, tx: 0, ty: 0)
    public init?(_ value: JSONValue) {
        if let list = value.arrayValue, list.count == 6, list.allSatisfy({ $0.doubleValue?.isFinite == true }) {
            self.init(a: list[0].doubleValue!, b: list[1].doubleValue!, c: list[2].doubleValue!, d: list[3].doubleValue!, tx: list[4].doubleValue!, ty: list[5].doubleValue!)
        } else if let a = value["a"]?.doubleValue, let b = value["b"]?.doubleValue, let c = value["c"]?.doubleValue, let d = value["d"]?.doubleValue, let tx = value["tx"]?.doubleValue, let ty = value["ty"]?.doubleValue, [a,b,c,d,tx,ty].allSatisfy(\.isFinite) {
            self.init(a: a, b: b, c: c, d: d, tx: tx, ty: ty)
        } else { return nil }
    }
}

public struct PreviewGeometry: Sendable {
    public let image: PlaneSize
    public let logical: PlaneSize
    public let transform: ImageTransform
    public let orientation: String
    public init(image: PlaneSize, logical: PlaneSize, transform: ImageTransform, orientation: String) { self.image = image; self.logical = logical; self.transform = transform; self.orientation = orientation }
    public func devicePoint(at point: PlanePoint, in container: PlaneSize) -> PlanePoint? {
        guard [image.width, image.height, logical.width, logical.height, container.width, container.height].allSatisfy({ $0.isFinite && $0 > 0 }), point.x.isFinite, point.y.isFinite else { return nil }
        let scale = min(container.width / image.width, container.height / image.height)
        let originX = (container.width - image.width * scale) / 2, originY = (container.height - image.height * scale) / 2
        let x = (point.x - originX) / scale, y = (point.y - originY) / scale
        guard x >= 0, y >= 0, x < image.width, y < image.height else { return nil }
        let result = PlanePoint(x: transform.a * x + transform.c * y + transform.tx, y: transform.b * x + transform.d * y + transform.ty)
        guard result.x.isFinite, result.y.isFinite, result.x >= 0, result.y >= 0, result.x < logical.width, result.y < logical.height else { return nil }
        return result
    }
}

public struct Observation: Sendable {
    public let id: String
    public let snapshotID: String?
    public let deviceID: String?
    public let epoch: String?
    public let geometry: PreviewGeometry?
    public let observedAt: String
    public let source: String
    public let freshness: JSONValue?
    public init?(_ value: JSONValue) {
        guard let id = value["observation_id"]?.stringValue else { return nil }
        self.id = id; snapshotID = value["snapshot_id"]?.stringValue; deviceID = value["device_id"]?.stringValue; epoch = value["connection_epoch"]?.scalarDescription
        observedAt = value["observed_at"]?.stringValue ?? ""; source = value["source"]?.stringValue ?? "unknown"; freshness = value["freshness"]
        if let iw = value["image_size"]?["width"]?.doubleValue, let ih = value["image_size"]?["height"]?.doubleValue,
           let lw = value["logical_size"]?["width"]?.doubleValue, let lh = value["logical_size"]?["height"]?.doubleValue,
           let transformJSON = value["image_to_device_transform"], let transform = ImageTransform(transformJSON) {
            geometry = PreviewGeometry(image: .init(width: iw, height: ih), logical: .init(width: lw, height: lh), transform: transform, orientation: value["orientation"]?.stringValue ?? "unknown")
        } else { geometry = nil }
    }
}
