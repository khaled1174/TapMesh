import XCTest
@testable import TapMeshCore

final class SavedFileUsageTests: XCTestCase {
    func testStorageSnapshotRequiresTwoNonnegativeWholeNumbers() {
        let snapshot = SavedFileUsage(.object(["count": .number(3), "bytes": .number(5_000_000_000)]))
        XCTAssertEqual(snapshot?.count, 3)
        XCTAssertEqual(snapshot?.bytes, 5_000_000_000)
        XCTAssertEqual(SavedFileUsage(.object(["count": .number(0), "bytes": .number(0)])), .empty)
        XCTAssertNil(SavedFileUsage(.object(["count": .number(1)])))
        XCTAssertNil(SavedFileUsage(.object(["count": .number(-1), "bytes": .number(40)])))
        XCTAssertNil(SavedFileUsage(.object(["count": .number(1), "bytes": .number(1.25)])))
    }
}
