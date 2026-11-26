import Foundation

public enum AgentSetup {
    public static func launcherPath(bundleURL: URL) -> String {
        bundleURL.appending(path: "Contents/MacOS/tapmesh-mcp").path
    }

    public static func mcpConfigJSON(launcherPath: String) -> String {
        let configuration = ["mcpServers": ["tapmesh": ["command": launcherPath]]]
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        return String(decoding: try! encoder.encode(configuration), as: UTF8.self)
    }

    public static func claudeCodeCommand(launcherPath: String) -> String {
        let quotedPath = launcherPath.replacingOccurrences(of: "'", with: "'\\''")
        return "claude mcp add tapmesh -- '\(quotedPath)'"
    }
}
