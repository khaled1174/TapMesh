import Darwin
import Foundation
import XCTest
@testable import TapMeshCore

final class SupportReportTests: XCTestCase {
    private func folder() throws -> URL {
        let url = FileManager.default.temporaryDirectory.resolvingSymlinksInPath().appendingPathComponent("tapmesh-support-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        return url
    }
    private var validEvent: JSONValue { .object([
        "event": .string("unexpected_backend_exception"), "diagnostic_id": .string(String(repeating: "a", count: 32)),
        "exception_type": .string("RuntimeError"), "stack": .array([.object(["file": .string("core.py"), "line": .number(42), "function": .string("call")])])
    ]) }
    private func report(events: [JSONValue] = [], health: JSONValue? = nil) -> SupportReport {
        SupportReport(appVersion: "0.1.0", appBuild: "2", operatingSystemVersion: "27.2.0", architecture: "arm64",
                      serverHealth: health, agentHealth: health, counts: ["devices": 2, "tools": 50, "unexpected_private_count": 123],
                      diagnosticEvents: events, diagnosticsAvailable: true)
    }
    func testOnlyExplicitMetadataFieldsSurviveMaliciousPayloads() throws {
        var event = try XCTUnwrap(validEvent.objectValue)
        event["message"] = .string("PRIVATE CREDENTIAL MESSAGE")
        event["device_id"] = .string("PRIVATE UDID")
        event["headers"] = .object(["Authorization": .string("Bearer PRIVATE TOKEN")])
        let health: JSONValue = .object(["status": .string("ok"), "version": .string("0.1.0"), "host": .string("PRIVATE HOST"), "token": .string("PRIVATE TOKEN"), "devices": .array([.string("PRIVATE DEVICE")])])
        let result = report(events: [.object(event)], health: health)
        let text = String(decoding: try result.encoded(), as: UTF8.self)
        XCTAssertFalse(text.contains("PRIVATE"))
        XCTAssertFalse(text.contains("unexpected_private_count"))
        XCTAssertFalse(text.contains("Authorization"))
        XCTAssertEqual(result.value["services"]?["agent"]?["version"], .string("0.1.0"))
        XCTAssertEqual(result.value["diagnostics"]?["events"]?.arrayValue, [validEvent])
    }
    func testUntrustedVersionAndClassFieldsCannotBecomeFreeText() throws {
        var event = try XCTUnwrap(validEvent.objectValue)
        event["exception_type"] = .string("PrivateCredentialThatLooksLikeAClass")
        event["stack"] = .array([.object(["file": .string("/Users/private/project.py"), "line": .number(2), "function": .string("private text")])])
        let result = SupportReport(appVersion: "PRIVATE TOKEN", appBuild: "PRIVATE PATH", operatingSystemVersion: "PRIVATE USER", architecture: "PRIVATE HOST",
                                   serverHealth: .object(["status": .string("PRIVATE DATA"), "version": .string("PRIVATE BODY")]), agentHealth: nil,
                                   counts: ["devices": -1], diagnosticEvents: [.object(event)], diagnosticsAvailable: true)
        let text = String(decoding: try result.encoded(), as: UTF8.self)
        XCTAssertFalse(text.localizedCaseInsensitiveContains("private"))
        XCTAssertTrue(text.contains("UnexpectedError"))
        XCTAssertEqual(result.value["counts"]?["devices"], .number(0))
    }
    func testRecentLogsAreBoundedAndSanitizedAcrossRotations() throws {
        let directory = try folder(); defer { try? FileManager.default.removeItem(at: directory) }
        for (suffix, first) in [(".2", 0), (".1", 10), ("", 20)] {
            let file = directory.appendingPathComponent("diagnostics.jsonl" + suffix)
            var data = Data()
            for index in first..<(first + 10) {
                var event = try XCTUnwrap(validEvent.objectValue)
                event["diagnostic_id"] = .string(String(format: "%032x", index))
                event["message"] = .string("PRIVATE payload")
                data.append(try JSONEncoder().encode(JSONValue.object(event))); data.append(10)
            }
            try data.write(to: file)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
        }
        let events = try SupportReport.recentDiagnostics(in: directory, limit: 5)
        XCTAssertEqual(events.count, 5)
        XCTAssertEqual(events.last?["diagnostic_id"], .string(String(format: "%032x", 29)))
        XCTAssertFalse(JSONValue.array(events).prettyPrinted.contains("PRIVATE"))
    }
    func testUnsafeInputLogAndSymlinkAreRejectedBeforeReading() throws {
        let directory = try folder(); defer { try? FileManager.default.removeItem(at: directory) }
        let target = directory.appendingPathComponent("credential.json")
        try Data("PRIVATE TOKEN".utf8).write(to: target)
        let link = directory.appendingPathComponent("diagnostics.jsonl")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: target)
        XCTAssertThrowsError(try SupportReport.recentDiagnostics(in: directory))
        XCTAssertEqual(try String(contentsOf: target, encoding: .utf8), "PRIVATE TOKEN")
        try FileManager.default.removeItem(at: link)
        try Data(repeating: 65, count: 1_048_577).write(to: link)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: link.path)
        XCTAssertThrowsError(try SupportReport.recentDiagnostics(in: directory))
    }
    func testUserCreatedDirectorySymlinksCannotSelectOtherInputOrOutput() throws {
        let directory = try folder(); defer { try? FileManager.default.removeItem(at: directory) }
        let actual = directory.appendingPathComponent("actual")
        try FileManager.default.createDirectory(at: actual, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        let alias = directory.appendingPathComponent("alias")
        try FileManager.default.createSymbolicLink(at: alias, withDestinationURL: actual)
        XCTAssertThrowsError(try SupportReport.recentDiagnostics(in: alias))
        XCTAssertThrowsError(try report().write(to: alias.appendingPathComponent("support.json")))
        XCTAssertTrue(try FileManager.default.contentsOfDirectory(atPath: actual.path).isEmpty)
    }
    func testExportIsPrivateAndDoesNotFollowOutputSymlinks() throws {
        let directory = try folder(); defer { try? FileManager.default.removeItem(at: directory) }
        let file = directory.appendingPathComponent("support.json")
        try report().write(to: file)
        let attributes = try FileManager.default.attributesOfItem(atPath: file.path)
        XCTAssertEqual((attributes[.posixPermissions] as? NSNumber)?.intValue, 0o600)
        let target = directory.appendingPathComponent("secret.json")
        try Data("PRIVATE TOKEN".utf8).write(to: target)
        let link = directory.appendingPathComponent("linked.json")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: target)
        XCTAssertThrowsError(try report().write(to: link))
        XCTAssertEqual(try String(contentsOf: target, encoding: .utf8), "PRIVATE TOKEN")
        XCTAssertThrowsError(try report().write(to: directory.appendingPathComponent("server.token")))
        let bundle = directory.appendingPathComponent("TapMesh.app/Contents/Resources")
        try FileManager.default.createDirectory(at: bundle, withIntermediateDirectories: true)
        XCTAssertThrowsError(try report().write(to: bundle.appendingPathComponent("support.json")))
    }
}
