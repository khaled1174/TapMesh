// Developer-only fixture generation. The runtime helper does not import AppKit.
import AppKit
import Foundation

let image = NSImage(size: NSSize(width: 1200, height: 400))
image.lockFocus()
NSColor.white.setFill()
NSRect(x: 0, y: 0, width: 1200, height: 400).fill()
("مرحبا تجربة" as NSString).draw(in: NSRect(x: 90, y: 120, width: 1000, height: 160), withAttributes: [
    .font: NSFont(name: "Geeza Pro", size: 95)!, .foregroundColor: NSColor.black
])
image.unlockFocus()
let bitmap = NSBitmapImageRep(data: image.tiffRepresentation!)!
try bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
