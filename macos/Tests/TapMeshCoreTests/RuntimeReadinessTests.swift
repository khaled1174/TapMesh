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
}
