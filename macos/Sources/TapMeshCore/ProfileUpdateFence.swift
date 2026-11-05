import Darwin
import CoreFoundation
import Foundation

public enum ProfileUpdateFenceError: LocalizedError {
    case invalidInput, unsafeProfile, unsafeFence, activeFence, leaseMismatch, busy, ioFailure

    public var errorDescription: String? {
        switch self {
        case .invalidInput: "The update preparation identifier or build number is invalid."
        case .unsafeProfile: "The local update profile must be a private directory owned by this user."
        case .unsafeFence: "The local update preparation file is invalid or has unsafe permissions."
        case .activeFence: "Another TapMesh update is preparing this profile. Wait for it to finish."
        case .leaseMismatch: "This TapMesh instance does not own the profile's update preparation."
        case .busy: "Another TapMesh instance is changing the update preparation. Try again."
        case .ioFailure: "TapMesh could not save the local update preparation. Check available storage and permissions."
        }
    }
}

/// A short, persistent replacement fence, shared by native app instances and
/// checked by the bundled supervisor before it starts services. The lease is
/// separate from the supervisor's lifetime and runtime.lock ownership.
public enum ProfileUpdateFence {
    private static let filename = "update-fence.json"
    private static let maximumBytes = 4096

    public static func install(profile: URL, leaseID: String, buildNumber: String,
                               lifetimeSeconds: TimeInterval = 300) throws {
        guard validLease(leaseID), validBuild(buildNumber), lifetimeSeconds.isFinite,
              lifetimeSeconds > 0, lifetimeSeconds <= 300 else { throw ProfileUpdateFenceError.invalidInput }
        try withLockedProfile(profile) { directory in
            let now = Date().timeIntervalSince1970
            if let previous = try readReceipt(directory), previous.expires > now, previous.lease != leaseID {
                throw ProfileUpdateFenceError.activeFence
            }
            let data = try JSONSerialization.data(withJSONObject: [
                "schema": 1, "lease_id": leaseID, "build_number": buildNumber,
                "expires_at": now + lifetimeSeconds,
            ], options: [.sortedKeys])
            let temporary = ".update-fence-" + UUID().uuidString
            let descriptor = openat(directory, temporary, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, mode_t(0o600))
            guard descriptor >= 0 else { throw ProfileUpdateFenceError.ioFailure }
            defer { close(descriptor); unlinkat(directory, temporary, 0) }
            guard fchmod(descriptor, mode_t(0o600)) == 0 else { throw ProfileUpdateFenceError.ioFailure }
            try data.withUnsafeBytes { buffer in
                var offset = 0
                while offset < buffer.count {
                    let count = Darwin.write(descriptor, buffer.baseAddress!.advanced(by: offset), buffer.count - offset)
                    if count < 0, errno == EINTR { continue }
                    guard count > 0 else { throw ProfileUpdateFenceError.ioFailure }
                    offset += count
                }
            }
            guard fsync(descriptor) == 0, renameat(directory, temporary, directory, filename) == 0,
                  fsync(directory) == 0 else { throw ProfileUpdateFenceError.ioFailure }
        }
    }

    public static func clearOwned(profile: URL, leaseID: String) throws {
        guard validLease(leaseID) else { throw ProfileUpdateFenceError.invalidInput }
        try withLockedProfile(profile) { directory in
            guard let existing = try readReceipt(directory) else { return }
            guard existing.lease == leaseID else { throw ProfileUpdateFenceError.leaseMismatch }
            guard unlinkat(directory, filename, 0) == 0, fsync(directory) == 0 else { throw ProfileUpdateFenceError.ioFailure }
        }
    }

    private static func validLease(_ value: String) -> Bool {
        (1...128).contains(value.utf8.count) && value.utf8.allSatisfy { (33...126).contains($0) }
    }

    private static func validBuild(_ value: String) -> Bool {
        guard (1...20).contains(value.utf8.count), let first = value.utf8.first, (49...57).contains(first),
              value.utf8.allSatisfy({ (48...57).contains($0) }), let parsed = UInt64(value), parsed > 0 else { return false }
        return true
    }

    private static func privateFile(_ descriptor: Int32) -> Bool {
        var info = stat()
        return fstat(descriptor, &info) == 0 && info.st_uid == getuid()
            && (info.st_mode & S_IFMT) == S_IFREG && (info.st_mode & 0o7777) == 0o600 && info.st_nlink == 1
    }

    private static func withLockedProfile<T>(_ profile: URL, operation: (Int32) throws -> T) throws -> T {
        guard profile.isFileURL, profile.path.hasPrefix("/"), !profile.path.contains("\0") else { throw ProfileUpdateFenceError.unsafeProfile }
        let directory = open(profile.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard directory >= 0 else { throw ProfileUpdateFenceError.unsafeProfile }
        defer { close(directory) }
        var info = stat()
        guard fstat(directory, &info) == 0, info.st_uid == getuid(),
              (info.st_mode & S_IFMT) == S_IFDIR, (info.st_mode & 0o7777) == 0o700 else { throw ProfileUpdateFenceError.unsafeProfile }
        var lock = openat(directory, "update-fence.lock", O_RDWR | O_CREAT | O_EXCL | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC, mode_t(0o600))
        if lock < 0, errno == EEXIST {
            lock = openat(directory, "update-fence.lock", O_RDWR | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
        } else if lock >= 0, fchmod(lock, mode_t(0o600)) != 0 {
            close(lock)
            throw ProfileUpdateFenceError.ioFailure
        }
        guard lock >= 0 else { throw ProfileUpdateFenceError.unsafeFence }
        defer { close(lock) }
        guard privateFile(lock) else { throw ProfileUpdateFenceError.unsafeFence }
        guard flock(lock, LOCK_EX | LOCK_NB) == 0 else { throw ProfileUpdateFenceError.busy }
        defer { _ = flock(lock, LOCK_UN) }
        return try operation(directory)
    }

    private static func readReceipt(_ directory: Int32) throws -> (lease: String, expires: Double)? {
        let descriptor = openat(directory, filename, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
        if descriptor < 0, errno == ENOENT { return nil }
        guard descriptor >= 0 else { throw ProfileUpdateFenceError.unsafeFence }
        defer { close(descriptor) }
        guard privateFile(descriptor) else { throw ProfileUpdateFenceError.unsafeFence }
        var bytes = [UInt8](repeating: 0, count: maximumBytes + 1)
        var total = 0
        while total < bytes.count {
            let count = bytes.withUnsafeMutableBytes { buffer in
                Darwin.read(descriptor, buffer.baseAddress!.advanced(by: total), buffer.count - total)
            }
            if count < 0, errno == EINTR { continue }
            guard count >= 0 else { throw ProfileUpdateFenceError.unsafeFence }
            if count == 0 { break }
            total += count
        }
        guard total <= maximumBytes,
              let object = try? JSONSerialization.jsonObject(with: Data(bytes.prefix(total))) as? [String: Any],
              Set(object.keys) == ["schema", "lease_id", "build_number", "expires_at"],
              let schema = object["schema"] as? NSNumber, CFGetTypeID(schema) != CFBooleanGetTypeID(), schema.doubleValue == 1,
              let lease = object["lease_id"] as? String, validLease(lease),
              let build = object["build_number"] as? String, validBuild(build),
              let expires = object["expires_at"] as? NSNumber, CFGetTypeID(expires) != CFBooleanGetTypeID(),
              expires.doubleValue.isFinite, expires.doubleValue >= 0 else { throw ProfileUpdateFenceError.unsafeFence }
        return (lease, expires.doubleValue)
    }
}
