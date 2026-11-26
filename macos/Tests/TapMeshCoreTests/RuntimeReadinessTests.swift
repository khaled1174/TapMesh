import Foundation
import XCTest
@testable import TapMeshCore

final class RuntimeReadinessTests: XCTestCase {
    func testShortReadinessLineArrivesWhileSupervisorKeepsPipeOpen() throws {
        let pipe = Pipe()
        let received = expectation(description: "received live supervisor readiness")
        DispatchQueue.global().async {
            do {
                let line = try RuntimeReadinessLine.read(from: pipe.fileHandleForReading)
                XCTAssertEqual(line, Data("ready".utf8))
            } catch { XCTFail("Readiness failed: \(error)") }
            received.fulfill()
        }
        try pipe.fileHandleForWriting.write(contentsOf: Data("ready\n".utf8))
        wait(for: [received], timeout: 2)
        try pipe.fileHandleForWriting.close()
        try pipe.fileHandleForReading.close()
    }

    func testCoalescedLinesAreConsumedInOrder() throws {
        let pipe = Pipe()
        defer { try? pipe.fileHandleForReading.close(); try? pipe.fileHandleForWriting.close() }
        try pipe.fileHandleForWriting.write(contentsOf: Data("ready\nrestarted\n".utf8))
        var lines = RuntimeReadinessLine()
        XCTAssertEqual(try lines.next(from: pipe.fileHandleForReading, limit: 9), Data("ready".utf8))
        XCTAssertEqual(try lines.next(from: pipe.fileHandleForReading, limit: 9), Data("restarted".utf8))
    }

    func testOversizedLineIsRejected() throws {
        let pipe = Pipe()
        defer { try? pipe.fileHandleForReading.close(); try? pipe.fileHandleForWriting.close() }
        try pipe.fileHandleForWriting.write(contentsOf: Data("12345\n".utf8))
        var lines = RuntimeReadinessLine()
        XCTAssertThrowsError(try lines.next(from: pipe.fileHandleForReading, limit: 4)) {
            guard case RuntimeReadinessError.oversized = $0 else { return XCTFail("Expected line limit error: \($0)") }
        }
    }

    func testSupervisorEventChecksFieldTypesAndServiceNames() throws {
        func decode(_ text: String) throws -> RuntimeSupervisorEvent {
            try JSONDecoder().decode(RuntimeSupervisorEvent.self, from: Data(text.utf8))
        }

        XCTAssertEqual(try decode(#"{"status":"ready","config_path":"/profile/connection.json","instance_id":"123","owner":false}"#),
                       .ready(configurationPath: "/profile/connection.json", instanceID: "123", ownsServices: false))
        XCTAssertEqual(try decode(#"{"status":"restarted","service":"server"}"#), .restarted(.server))
        XCTAssertEqual(try decode(#"{"status":"restarted","service":"agent"}"#), .restarted(.agent))
        XCTAssertEqual(try decode(#"{"status":"error","code":"future_error","message":"English diagnostics"}"#), .failure(code: "future_error"))
        XCTAssertThrowsError(try decode(#"{"status":"restarted","service":"unrecognized"}"#))
        XCTAssertThrowsError(try decode(#"{"status":"ready","config_path":"/profile/connection.json","instance_id":"123","owner":1}"#))
        XCTAssertThrowsError(try decode(#"{"status":"ready","config_path":"relative/connection.json","instance_id":"123","owner":true}"#))
    }
}
