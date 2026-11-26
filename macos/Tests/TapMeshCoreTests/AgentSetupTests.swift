import Foundation
import XCTest
@testable import TapMeshCore

final class AgentSetupTests: XCTestCase {
    func testLauncherUsesTheSelectedAppBundle() {
        let bundle = URL(fileURLWithPath: "/Users/fixture/My Apps/Tap'Mesh.app", isDirectory: true)
        XCTAssertEqual(AgentSetup.launcherPath(bundleURL: bundle), "/Users/fixture/My Apps/Tap'Mesh.app/Contents/MacOS/tapmesh-mcp")
    }

    func testConfigAndCommandQuoteThePath() throws {
        let path = "/Users/a b/Apps/Tap'Mesh.app/Contents/MacOS/tapmesh-mcp"
        let configuration = AgentSetup.mcpConfigJSON(launcherPath: path)
        let decoded = try JSONDecoder().decode([String: [String: [String: String]]].self, from: Data(configuration.utf8))
        XCTAssertEqual(decoded, ["mcpServers": ["tapmesh": ["command": path]]])
        XCTAssertTrue(configuration.contains("\n"))
        XCTAssertEqual(AgentSetup.claudeCodeCommand(launcherPath: path), "claude mcp add tapmesh -- '/Users/a b/Apps/Tap'\\''Mesh.app/Contents/MacOS/tapmesh-mcp'")
    }

    func testJSONRoundTripsUnicodeQuotesAndBackslashes() throws {
        let path = #"/Applications/تطبيق "Tap\Mesh".app/Contents/MacOS/tapmesh-mcp"#
        let data = Data(AgentSetup.mcpConfigJSON(launcherPath: path).utf8)
        let decoded = try JSONDecoder().decode([String: [String: [String: String]]].self, from: data)
        XCTAssertEqual(decoded, ["mcpServers": ["tapmesh": ["command": path]]])
    }
}
