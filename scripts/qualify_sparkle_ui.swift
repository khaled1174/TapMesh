import AppKit
import ApplicationServices
import Foundation

guard CommandLine.arguments.count == 3 else { exit(2) }
let mode = CommandLine.arguments[1]
let binary = URL(fileURLWithPath: CommandLine.arguments[2]).resolvingSymlinksInPath().path
let apps = NSRunningApplication.runningApplications(withBundleIdentifier: "io.tapmesh.mac").filter {
    $0.executableURL?.resolvingSymlinksInPath().path == binary
}
func output(_ value: [String: Any]) {
    let data = try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    print(String(decoding: data, as: UTF8.self))
}
guard apps.count == 1, let app = apps.first else {
    output(["running_count": apps.count, "available": false]); exit(0)
}
if mode == "locate" {
    output(["running_count": 1, "process_id": app.processIdentifier]); exit(0)
}
if mode == "quit" {
    output(["termination_requested": app.terminate()]); exit(0)
}
guard AXIsProcessTrusted() else { output(["trusted": false]); exit(0) }
let root = AXUIElementCreateApplication(app.processIdentifier)
func attribute(_ element: AXUIElement, _ name: CFString) -> CFTypeRef? {
    var result: CFTypeRef?
    return AXUIElementCopyAttributeValue(element, name, &result) == .success ? result : nil
}
func find(_ node: AXUIElement, depth: Int, budget: inout Int,
          matches: (AXUIElement) -> Bool) -> AXUIElement? {
    guard depth < 15, budget > 0 else { return nil }
    budget -= 1
    if matches(node) { return node }
    for child in attribute(node, kAXChildrenAttribute as CFString) as? [AXUIElement] ?? [] {
        if let found = find(child, depth: depth + 1, budget: &budget, matches: matches) { return found }
    }
    return nil
}
func press(_ element: AXUIElement) {
    guard attribute(element, kAXEnabledAttribute as CFString) as? Bool == true else {
        output(["trusted": true, "available": false]); return
    }
    let error = AXUIElementPerformAction(element, kAXPressAction as CFString)
    if error == .success { output(["action_performed": true]) }
    else { output(["action_error": error.rawValue]) }
}
var budget = 800
if mode == "check-update" {
    guard let barValue = attribute(root, kAXMenuBarAttribute as CFString),
          CFGetTypeID(barValue) == AXUIElementGetTypeID() else {
        output(["available": false]); exit(0)
    }
    let bar = barValue as! AXUIElement
    let menu = (attribute(bar, kAXChildrenAttribute as CFString) as? [AXUIElement] ?? []).first {
        attribute($0, kAXTitleAttribute as CFString) as? String == "TapMesh"
    }
    guard let menu else { output(["available": false]); exit(0) }
    let opened = AXUIElementPerformAction(menu, kAXPressAction as CFString)
    guard opened == .success else { output(["action_error": opened.rawValue]); exit(0) }
    let item = find(menu, depth: 0, budget: &budget) {
        attribute($0, kAXTitleAttribute as CFString) as? String == "Check for Updates…"
    }
    if let item { press(item) } else { output(["available": false]) }
} else {
    let identifiers = ["install": "SPUUserUpdateChoiceInstall", "relaunch": "SUStatusInstallAndRelaunch"]
    guard let identifier = identifiers[mode] else { exit(2) }
    let button = find(root, depth: 0, budget: &budget) {
        attribute($0, kAXIdentifierAttribute as CFString) as? String == identifier
    }
    if let button { press(button) } else { output(["available": false]) }
}
