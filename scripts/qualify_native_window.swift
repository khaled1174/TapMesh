import AppKit
import ApplicationServices
import CoreGraphics
import Foundation

guard CommandLine.arguments.count == 4,
      let pid = Int32(CommandLine.arguments[2]),
      let app = NSRunningApplication(processIdentifier: pid),
      app.bundleIdentifier == "io.tapmesh.mac",
      app.executableURL?.resolvingSymlinksInPath().path == URL(fileURLWithPath: CommandLine.arguments[3]).resolvingSymlinksInPath().path
else { exit(2) }

let mode = CommandLine.arguments[1]
let result: [String: Any]
if mode == "quit" {
    result = ["termination_requested": app.terminate()]
} else if mode == "window" {
    let rows = CGWindowListCopyWindowInfo(.optionOnScreenOnly, kCGNullWindowID) as? [[String: Any]] ?? []
    let owned = rows.filter { ($0[kCGWindowOwnerPID as String] as? Int32) == pid }
    let workspaceWindow = owned.contains { row in
        guard let bounds = row[kCGWindowBounds as String] as? [String: Any],
              let width = bounds["Width"] as? Double,
              let height = bounds["Height"] as? Double else { return false }
        return width >= 1000 && height >= 600
    }
    result = ["workspace_window_visible": workspaceWindow,
              "ui_automation_trusted": AXIsProcessTrusted()]
} else { exit(2) }
let data = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
print(String(decoding: data, as: UTF8.self))
