import CoreGraphics
import Foundation

let backgrounds = [[0,0,0], [255,255,255], [16,16,16], [255,0,0], [0,255,0]]
let blues = [[0,122,255], [10,132,255]]
var rows: [[String: Any]] = []
for background in backgrounds {
    for blue in blues {
        for opacity in 0...255 {
            let context = CGContext(data: nil, width: 1, height: 1, bitsPerComponent: 8,
                                    bytesPerRow: 4, space: CGColorSpaceCreateDeviceRGB(),
                                    bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
            context.setShouldAntialias(false)
            context.setFillColor(red: CGFloat(background[0])/255, green: CGFloat(background[1])/255,
                                 blue: CGFloat(background[2])/255, alpha: 1)
            context.fill(CGRect(x: 0, y: 0, width: 1, height: 1))
            let bytes = context.data!.assumingMemoryBound(to: UInt8.self)
            let actualBackground = (0..<3).map { Int(bytes[$0]) }
            context.setFillColor(red: CGFloat(blue[0])/255, green: CGFloat(blue[1])/255,
                                 blue: CGFloat(blue[2])/255, alpha: CGFloat(opacity)/255)
            context.fill(CGRect(x: 0, y: 0, width: 1, height: 1))
            rows.append(["background": actualBackground, "requested_background": background,
                         "foreground": blue, "opacity_255": opacity,
                         "pixel": (0..<3).map { Int(bytes[$0]) }])
        }
    }
}
let data = try JSONSerialization.data(withJSONObject: rows, options: [.sortedKeys])
try data.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
