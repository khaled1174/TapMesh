import Darwin
import Foundation
import XCTest
@testable import TapMeshCore

final class ProfileUpdateFenceTests: XCTestCase {
    private func profile() throws -> URL {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("tapmesh-fence-tests-" + UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: directory.path)
        return directory
    }

    private func receipt(_ directory: URL) throws -> [String: Any] {
        try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: directory.appendingPathComponent("update-fence.json"))) as? [String: Any])
    }

    func testInstallsPrivateExactReceiptAndClearsOnlyItsOwnLease() throws {
        let directory = try profile(); defer { try? FileManager.default.removeItem(at: directory) }
        let lease = UUID().uuidString
        let before = Date().timeIntervalSince1970
        try ProfileUpdateFence.install(profile: directory, leaseID: lease, buildNumber: "42")
        let object = try receipt(directory)
        XCTAssertEqual(Set(object.keys), ["schema", "lease_id", "build_number", "expires_at"])
        XCTAssertEqual(object["schema"] as? Int, 1)
        XCTAssertEqual(object["lease_id"] as? String, lease)
        XCTAssertEqual(object["build_number"] as? String, "42")
        let expires = try XCTUnwrap(object["expires_at"] as? Double)
        XCTAssertGreaterThanOrEqual(expires, before + 300)
        XCTAssertLessThanOrEqual(expires, Date().timeIntervalSince1970 + 300)
        let attributes = try FileManager.default.attributesOfItem(atPath: directory.appendingPathComponent("update-fence.json").path)
        XCTAssertEqual((attributes[.posixPermissions] as? NSNumber)?.intValue, 0o600)
        XCTAssertEqual((attributes[.ownerAccountID] as? NSNumber)?.uint32Value, getuid())
        try ProfileUpdateFence.clearOwned(profile: directory, leaseID: lease)
        XCTAssertFalse(FileManager.default.fileExists(atPath: directory.appendingPathComponent("update-fence.json").path))
        try ProfileUpdateFence.clearOwned(profile: directory, leaseID: lease)
    }

    func testAnotherLeaseCannotOverwriteOrClearActivePreparation() throws {
        let directory = try profile(); defer { try? FileManager.default.removeItem(at: directory) }
        try ProfileUpdateFence.install(profile: directory, leaseID: "first-owner", buildNumber: "1")
        let file = directory.appendingPathComponent("update-fence.json")
        let original = try Data(contentsOf: file)
        XCTAssertThrowsError(try ProfileUpdateFence.install(profile: directory, leaseID: "second-owner", buildNumber: "2"))
        XCTAssertEqual(try Data(contentsOf: file), original)
        XCTAssertThrowsError(try ProfileUpdateFence.clearOwned(profile: directory, leaseID: "second-owner"))
        XCTAssertEqual(try Data(contentsOf: file), original)
        try ProfileUpdateFence.install(profile: directory, leaseID: "first-owner", buildNumber: "1", lifetimeSeconds: 30)
        XCTAssertEqual(try receipt(directory)["lease_id"] as? String, "first-owner")
    }

    func testExpiredFenceMayBeReplacedAndForeignExpiredFenceCannotBeCleared() throws {
        let directory = try profile(); defer { try? FileManager.default.removeItem(at: directory) }
        let file = directory.appendingPathComponent("update-fence.json")
        let expired: [String: Any] = ["schema": 1, "lease_id": "old-owner", "build_number": "1", "expires_at": Date().timeIntervalSince1970 - 1]
        try JSONSerialization.data(withJSONObject: expired).write(to: file)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
        XCTAssertThrowsError(try ProfileUpdateFence.clearOwned(profile: directory, leaseID: "new-owner"))
        try ProfileUpdateFence.install(profile: directory, leaseID: "new-owner", buildNumber: "18446744073709551615")
        XCTAssertEqual(try receipt(directory)["lease_id"] as? String, "new-owner")
        XCTAssertEqual(try receipt(directory)["build_number"] as? String, String(UInt64.max))
    }

    func testRejectsInvalidLeaseBuildAndLifetimeWithoutInstallingReceipt() throws {
        let directory = try profile(); defer { try? FileManager.default.removeItem(at: directory) }
        for lease in ["", String(repeating: "x", count: 129), "owner\nnext", "owner with spaces", "مالك"] {
            XCTAssertThrowsError(try ProfileUpdateFence.install(profile: directory, leaseID: lease, buildNumber: "1"))
        }
        for build in ["", "0", "01", "-1", "+1", "1.0", "1\n", "١", "18446744073709551616"] {
            XCTAssertThrowsError(try ProfileUpdateFence.install(profile: directory, leaseID: "owner", buildNumber: build))
        }
        for lifetime in [0, -1, 300.1, Double.infinity, Double.nan] {
            XCTAssertThrowsError(try ProfileUpdateFence.install(profile: directory, leaseID: "owner", buildNumber: "1", lifetimeSeconds: lifetime))
        }
        XCTAssertFalse(FileManager.default.fileExists(atPath: directory.appendingPathComponent("update-fence.json").path))
    }

    func testRejectsSymlinkFenceAndNonprivateProfileOrFile() throws {
        let directory = try profile(); defer { try? FileManager.default.removeItem(at: directory) }
        let target = directory.appendingPathComponent("untouched.json")
        let file = directory.appendingPathComponent("update-fence.json")
        try Data("untouched".utf8).write(to: target)
        try FileManager.default.createSymbolicLink(at: file, withDestinationURL: target)
        XCTAssertThrowsError(try ProfileUpdateFence.install(profile: directory, leaseID: "owner", buildNumber: "1"))
        XCTAssertThrowsError(try ProfileUpdateFence.clearOwned(profile: directory, leaseID: "owner"))
        XCTAssertEqual(try String(contentsOf: target, encoding: .utf8), "untouched")
        try FileManager.default.removeItem(at: file)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: directory.path)
        XCTAssertThrowsError(try ProfileUpdateFence.install(profile: directory, leaseID: "owner", buildNumber: "1"))
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: directory.path)
        try ProfileUpdateFence.install(profile: directory, leaseID: "owner", buildNumber: "1")
        try FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: file.path)
        XCTAssertThrowsError(try ProfileUpdateFence.install(profile: directory, leaseID: "owner", buildNumber: "1"))
        XCTAssertThrowsError(try ProfileUpdateFence.clearOwned(profile: directory, leaseID: "owner"))
    }

    func testRejectsSymlinkProfileAndMalformedOrOversizedReceipt() throws {
        let directory = try profile(); defer { try? FileManager.default.removeItem(at: directory) }
        let link = directory.deletingLastPathComponent().appendingPathComponent("tapmesh-fence-link-" + UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: link) }
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: directory)
        XCTAssertThrowsError(try ProfileUpdateFence.install(profile: link, leaseID: "owner", buildNumber: "1"))
        let file = directory.appendingPathComponent("update-fence.json")
        for data in [Data("{}".utf8), Data(repeating: 32, count: 4097), Data("{\"schema\":true,\"lease_id\":\"owner\",\"build_number\":\"1\",\"expires_at\":1}".utf8)] {
            try data.write(to: file)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
            XCTAssertThrowsError(try ProfileUpdateFence.install(profile: directory, leaseID: "owner", buildNumber: "1"))
            XCTAssertThrowsError(try ProfileUpdateFence.clearOwned(profile: directory, leaseID: "owner"))
        }
    }

    func testHeldProfileFenceLockRejectsAConcurrentWriter() throws {
        let directory = try profile(); defer { try? FileManager.default.removeItem(at: directory) }
        let lock = open(directory.appendingPathComponent("update-fence.lock").path, O_RDWR | O_CREAT | O_EXCL, mode_t(0o600))
        XCTAssertGreaterThanOrEqual(lock, 0)
        guard lock >= 0 else { return }
        defer { _ = flock(lock, LOCK_UN); close(lock) }
        XCTAssertEqual(fchmod(lock, mode_t(0o600)), 0)
        XCTAssertEqual(flock(lock, LOCK_EX | LOCK_NB), 0)
        XCTAssertThrowsError(try ProfileUpdateFence.install(profile: directory, leaseID: "concurrent-owner", buildNumber: "1"))
        XCTAssertFalse(FileManager.default.fileExists(atPath: directory.appendingPathComponent("update-fence.json").path))
        XCTAssertEqual(flock(lock, LOCK_UN), 0)
        try ProfileUpdateFence.install(profile: directory, leaseID: "concurrent-owner", buildNumber: "1")
        XCTAssertEqual(try receipt(directory)["lease_id"] as? String, "concurrent-owner")
    }
}
