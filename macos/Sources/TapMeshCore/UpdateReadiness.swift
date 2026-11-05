import Foundation

public struct UpdateActivity: Equatable, Sendable {
    public var commandActive: Bool
    public var buildActive: Bool
    public var recordingActive: Bool
    public var recordingStopping: Bool
    public var takeoverActive: Bool
    public var connecting: Bool

    public init(commandActive: Bool = false, buildActive: Bool = false, recordingActive: Bool = false,
                recordingStopping: Bool = false, takeoverActive: Bool = false, connecting: Bool = false) {
        self.commandActive = commandActive; self.buildActive = buildActive
        self.recordingActive = recordingActive; self.recordingStopping = recordingStopping
        self.takeoverActive = takeoverActive; self.connecting = connecting
    }

    public var allowsInstallation: Bool {
        !commandActive && !buildActive && !recordingActive && !recordingStopping && !takeoverActive && !connecting
    }
}

public enum UpdateConfiguration {
    public static func isValid(feedURL: String?, publicKey: String?, allowLocalStaging: Bool = false) -> Bool {
        guard let feedURL, let url = URL(string: feedURL),
              let host = url.host, !host.isEmpty, url.user == nil, url.password == nil,
              url.query == nil, url.fragment == nil, let publicKey, let key = Data(base64Encoded: publicKey),
              key.count == 32, key.base64EncodedString() == publicKey else { return false }
        if url.scheme == "https" { return true }
        guard allowLocalStaging, url.scheme == "http", ["127.0.0.1", "::1", "[::1]"].contains(host),
              let port = url.port, (1...65535).contains(port) else { return false }
        return true
    }
}
