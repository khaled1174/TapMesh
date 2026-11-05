import XCTest
@testable import TapMeshCore

final class LocalConnectionTests: XCTestCase {
    func testDefaultProfileIsLocalAndDoesNotConsumePrivateLegacyConfiguration() throws {
        let home = URL(fileURLWithPath: "/Users/fixture")
        XCTAssertEqual(try ConnectionConfiguration.defaultFileURL(environment: [:], homeDirectory: home).path, "/Users/fixture/Library/Application Support/TapMesh/Local/connection.json")
        XCTAssertEqual(try ConnectionConfiguration.defaultFileURL(environment: ["TAPMESH_PROFILE_DIR": "/tmp/tapmesh qualification"], homeDirectory: home).path, "/tmp/tapmesh qualification/connection.json")
        XCTAssertEqual(try ConnectionConfiguration.defaultFileURL(environment: ["TAPMESH_CONNECTION_FILE": "/tmp/private-profile/connection.json"], homeDirectory: home).path, "/tmp/private-profile/connection.json")
        XCTAssertThrowsError(try ConnectionConfiguration.defaultFileURL(environment: ["TAPMESH_CONNECTION_FILE": "relative/connection.json"], homeDirectory: home))
        XCTAssertThrowsError(try ConnectionConfiguration.defaultFileURL(environment: ["TAPMESH_PROFILE_DIR": "relative/profile"], homeDirectory: home))
    }
    private func fixture() throws -> (URL, [String: JSONValue]) {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("tapmesh-config-tests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let server = folder.appendingPathComponent("server.token"), owner = folder.appendingPathComponent("owner.token")
        try Data("fixture-server-credential".utf8).write(to: server)
        try Data("fixture-owner-credential".utf8).write(to: owner)
        return (folder, ["api_url": .string("http://127.0.0.1:48793"), "agent_url": .string("http://127.0.0.1:48792"), "server_token_file": .string(server.path), "agent_token_file": .string(server.path), "approval_token_file": .string(owner.path)])
    }
    private func load(_ object: [String: JSONValue], in folder: URL) throws -> ConnectionConfiguration {
        let file = folder.appendingPathComponent("connection.json")
        try JSONEncoder().encode(JSONValue.object(object)).write(to: file)
        return try ConnectionConfiguration.load(from: file)
    }
    func testTakeoverRequiresSeparateOwnerCredentialBeforeNetworkDispatch() throws {
        let (folder, object) = try fixture(); defer { try? FileManager.default.removeItem(at: folder) }
        let configuration = try load(object, in: folder)
        XCTAssertThrowsError(try configuration.request("api/takeover", method: "POST"))
        let request = try configuration.takeoverRequest(sessionID: "local-session", deviceID: "sim:A")
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.url?.path, "/api/takeover")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer fixture-server-credential")
        XCTAssertEqual(request.value(forHTTPHeaderField: "X-DeviceBridge-Local-Authorization"), "Bearer fixture-owner-credential")
        let body = try JSONDecoder().decode(JSONValue.self, from: XCTUnwrap(request.httpBody))
        XCTAssertEqual(body, .object(["session_id": .string("local-session"), "device_id": .string("sim:A")]))
        try FileManager.default.removeItem(atPath: configuration.approvalTokenFile)
        XCTAssertThrowsError(try configuration.takeoverRequest(sessionID: "local-session", deviceID: "sim:A"))
    }
    func testRejectsMissingOwnerConfigurationAndNonlocalEndpoints() throws {
        let (folder, object) = try fixture(); defer { try? FileManager.default.removeItem(at: folder) }
        var missingOwner = object; missingOwner.removeValue(forKey: "approval_token_file")
        XCTAssertThrowsError(try load(missingOwner, in: folder))
        var remote = object; remote["api_url"] = .string("http://example.com:48793")
        XCTAssertThrowsError(try load(remote, in: folder))
        var relative = object; relative["approval_token_file"] = .string("relative.token")
        XCTAssertThrowsError(try load(relative, in: folder))
    }
}
