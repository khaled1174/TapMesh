import XCTest
@testable import TapMeshCore

final class ArtifactPreviewTests: XCTestCase {
    func testServiceKindsMapToPreviews() {
        // Kinds and suffixes written by agent/backends.py.
        XCTAssertEqual(ArtifactPreviewPolicy.previewKind(kind: "screenshot", filename: "0a1b.jpeg"), .image)
        XCTAssertEqual(ArtifactPreviewPolicy.previewKind(kind: "screenshot", filename: "home.png"), .image)
        XCTAssertEqual(ArtifactPreviewPolicy.previewKind(kind: "recording", filename: "0a1b.mp4"), .video)
        XCTAssertEqual(ArtifactPreviewPolicy.previewKind(kind: "build_log", filename: "0a1b.log"), .text)
        XCTAssertEqual(ArtifactPreviewPolicy.previewKind(kind: "crash", filename: "0a1b.ips"), .text)
        XCTAssertEqual(ArtifactPreviewPolicy.previewKind(kind: "app_container", filename: "0a1b.zip"), .unsupported)
    }

    func testExtensionIsCaseInsensitiveAndWinsOverKind() {
        XCTAssertEqual(ArtifactPreviewPolicy.previewKind(kind: "screenshot", filename: "Shot.PNG"), .image)
        XCTAssertEqual(ArtifactPreviewPolicy.previewKind(kind: "export", filename: "result.json"), .text)
        XCTAssertEqual(ArtifactPreviewPolicy.previewKind(kind: "recording", filename: "clip.MOV"), .video)
        XCTAssertEqual(ArtifactPreviewPolicy.previewKind(kind: "screenshot", filename: "bundle.zip"), .unsupported, "The file's real type decides how it is decoded")
    }

    func testKindDecidesWhenFilenameHasNoExtension() {
        // The store falls back to the artifact id as the filename when the record has no local path.
        let id = "0123456789abcdef0123456789abcdef"
        XCTAssertEqual(ArtifactPreviewPolicy.previewKind(kind: "screenshot", filename: id), .image)
        XCTAssertEqual(ArtifactPreviewPolicy.previewKind(kind: "recording", filename: id), .video)
        XCTAssertEqual(ArtifactPreviewPolicy.previewKind(kind: "build_log", filename: id), .text)
        XCTAssertEqual(ArtifactPreviewPolicy.previewKind(kind: "artifact", filename: id), .unsupported)
    }

    func testReadinessFromState() {
        XCTAssertEqual(ArtifactPreviewPolicy.readiness(state: "complete"), .ready)
        XCTAssertEqual(ArtifactPreviewPolicy.readiness(state: "partial"), .ready, "A partial recording is a finished file")
        XCTAssertEqual(ArtifactPreviewPolicy.readiness(state: "unknown"), .ready, "The store's default when the record has no state")
        XCTAssertEqual(ArtifactPreviewPolicy.readiness(state: "in_progress"), .notReady)
        XCTAssertEqual(ArtifactPreviewPolicy.readiness(state: "Writing"), .notReady)
        XCTAssertEqual(ArtifactPreviewPolicy.readiness(state: "expired"), .unavailable)
        XCTAssertEqual(ArtifactPreviewPolicy.readiness(state: "deleted"), .unavailable)
    }

    func testSizeThreshold() {
        let limit = ArtifactPreviewPolicy.automaticLoadLimitBytes
        XCTAssertEqual(limit, 50 * 1024 * 1024)
        XCTAssertTrue(ArtifactPreviewPolicy.loadsAutomatically(.image, sizeBytes: 300_000))
        XCTAssertTrue(ArtifactPreviewPolicy.loadsAutomatically(.video, sizeBytes: limit))
        XCTAssertFalse(ArtifactPreviewPolicy.loadsAutomatically(.video, sizeBytes: limit + 1))
        XCTAssertFalse(ArtifactPreviewPolicy.loadsAutomatically(.text, sizeBytes: limit + 1))
    }

    func testUnknownSizeLoadsOnlySmallKindsAndUnsupportedNeverLoads() {
        XCTAssertTrue(ArtifactPreviewPolicy.loadsAutomatically(.image, sizeBytes: nil))
        XCTAssertTrue(ArtifactPreviewPolicy.loadsAutomatically(.text, sizeBytes: nil))
        XCTAssertFalse(ArtifactPreviewPolicy.loadsAutomatically(.video, sizeBytes: nil), "A recording of unknown size waits for Load Preview")
        XCTAssertFalse(ArtifactPreviewPolicy.loadsAutomatically(.unsupported, sizeBytes: 10))
    }

    func testSizeBytesFromMetadata() {
        XCTAssertEqual(ArtifactPreviewPolicy.sizeBytes(in: .object(["size_bytes": .number(1234)])), 1234)
        XCTAssertNil(ArtifactPreviewPolicy.sizeBytes(in: .object([:])))
        XCTAssertNil(ArtifactPreviewPolicy.sizeBytes(in: .object(["size_bytes": .number(-1)])))
        XCTAssertNil(ArtifactPreviewPolicy.sizeBytes(in: .object(["size_bytes": .string("12")])))
        XCTAssertNil(ArtifactPreviewPolicy.sizeBytes(in: .object(["size_bytes": .number(.infinity)])))
    }

    func testSizeTextIsHumanReadable() {
        let text = ArtifactPreviewPolicy.sizeText(50 * 1024 * 1024)
        XCTAssertTrue(text.contains("MB"), text)
        XCTAssertFalse(ArtifactPreviewPolicy.sizeText(-5).contains("-"), "Negative sizes are clamped")
    }

    func testTextExcerptWithinLimitIsComplete() throws {
        let excerpt = try XCTUnwrap(ArtifactPreviewPolicy.textExcerpt(from: Data("Build succeeded\n".utf8), limit: 100))
        XCTAssertEqual(excerpt.text, "Build succeeded\n")
        XCTAssertFalse(excerpt.truncated)
    }

    func testTextExcerptTruncatesAndTrimsSplitCharacter() throws {
        // "é" is two bytes; a limit of 2 cuts through it.
        let excerpt = try XCTUnwrap(ArtifactPreviewPolicy.textExcerpt(from: Data("aé tail".utf8), limit: 2))
        XCTAssertEqual(excerpt.text, "a")
        XCTAssertTrue(excerpt.truncated)
    }

    func testTextExcerptRejectsBinary() {
        XCTAssertNil(ArtifactPreviewPolicy.textExcerpt(from: Data([0x50, 0x4B, 0x03, 0x04, 0x00, 0x00]), limit: 100))
    }
}

final class ArtifactPreviewCacheRuleTests: XCTestCase {
    func testFolderNameRoundTripsProcessID() {
        let name = ArtifactPreviewCacheRule.sessionFolderName(processID: 4321, token: "A1B2C3D4-0000-1111-2222-333344445555")
        XCTAssertEqual(ArtifactPreviewCacheRule.processID(fromFolderName: name), 4321)
    }

    func testFoldersWithoutProcessIDAreStale() {
        // The previous layout used a bare UUID folder name.
        for name in ["A1B2C3D4-0000-1111-2222-333344445555", "-12", "12-", "0-x", "junk", ""] {
            XCTAssertNil(ArtifactPreviewCacheRule.processID(fromFolderName: name), name)
            XCTAssertTrue(ArtifactPreviewCacheRule.isStale(folderName: name, createdAt: nil) { _ in .running(startedAt: nil) }, name)
        }
        // An old UUID whose first group is all digits reads as a process id that no TapMesh has.
        XCTAssertTrue(ArtifactPreviewCacheRule.isStale(folderName: "12345678-0000-1111-2222-333344445555", createdAt: nil) { _ in .notRunning })
    }

    func testRunningOwnerKeepsFolderEvenWhenOld() {
        let started = Date(timeIntervalSince1970: 1_000)
        let created = started.addingTimeInterval(5)
        XCTAssertFalse(ArtifactPreviewCacheRule.isStale(folderName: "77-t", createdAt: created) { pid in
            XCTAssertEqual(pid, 77); return .running(startedAt: started)
        })
        XCTAssertFalse(ArtifactPreviewCacheRule.isStale(folderName: "77-t", createdAt: nil) { _ in .running(startedAt: started) })
        XCTAssertFalse(ArtifactPreviewCacheRule.isStale(folderName: "77-t", createdAt: created) { _ in .running(startedAt: nil) })
    }

    func testEndedOrReusedOwnerMakesFolderStale() {
        let created = Date(timeIntervalSince1970: 1_000)
        XCTAssertTrue(ArtifactPreviewCacheRule.isStale(folderName: "77-t", createdAt: created) { _ in .notRunning })
        XCTAssertTrue(ArtifactPreviewCacheRule.isStale(folderName: "77-t", createdAt: created) { _ in .running(startedAt: created.addingTimeInterval(60)) },
                      "A TapMesh that started after the folder was made reused the process id")
        XCTAssertFalse(ArtifactPreviewCacheRule.isStale(folderName: "77-t", createdAt: created) { _ in .running(startedAt: created.addingTimeInterval(0.5)) },
                       "Sub-second rounding is tolerated")
    }

    func testIsInsideResolvesDotsAndRejectsSiblings() {
        let root = URL(fileURLWithPath: "/tmp/TapMesh/ArtifactPreviews")
        XCTAssertTrue(ArtifactPreviewCacheRule.isInside(URL(fileURLWithPath: "/tmp/TapMesh/ArtifactPreviews/1-a/id/shot.png"), folder: root))
        XCTAssertTrue(ArtifactPreviewCacheRule.isInside(root, folder: root))
        XCTAssertTrue(ArtifactPreviewCacheRule.isInside(URL(fileURLWithPath: "/tmp/TapMesh/Other/../ArtifactPreviews/x"), folder: root))
        XCTAssertFalse(ArtifactPreviewCacheRule.isInside(URL(fileURLWithPath: "/tmp/TapMesh/ArtifactPreviewsOld/x"), folder: root))
        XCTAssertFalse(ArtifactPreviewCacheRule.isInside(URL(fileURLWithPath: "/Users/someone/Desktop/shot.png"), folder: root))
    }
}
