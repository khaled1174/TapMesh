import XCTest
@testable import TapMeshCore

final class UpdateReadinessTests: XCTestCase {
    func testRecordingDefersInstallationUntilWriterAndFinalizationAreDone() {
        XCTAssertFalse(UpdateActivity(recordingActive: true).allowsInstallation)
        XCTAssertFalse(UpdateActivity(recordingStopping: true).allowsInstallation)
        XCTAssertTrue(UpdateActivity().allowsInstallation)
    }

    func testInstallCannotInterruptBuildCommandTakeoverOrConnectionRestart() {
        XCTAssertFalse(UpdateActivity(commandActive: true).allowsInstallation)
        XCTAssertFalse(UpdateActivity(buildActive: true).allowsInstallation)
        XCTAssertFalse(UpdateActivity(takeoverActive: true).allowsInstallation)
        XCTAssertFalse(UpdateActivity(connecting: true).allowsInstallation)
    }

    func testOnlyCompleteHTTPSFeedAndEd25519KeyEnableUpdater() {
        let key = Data(repeating: 1, count: 32).base64EncodedString()
        XCTAssertTrue(UpdateConfiguration.isValid(feedURL: "https://updates.example.org/appcast.xml", publicKey: key))
        XCTAssertFalse(UpdateConfiguration.isValid(feedURL: nil, publicKey: key))
        XCTAssertFalse(UpdateConfiguration.isValid(feedURL: "http://updates.example.org/appcast.xml", publicKey: key))
        XCTAssertFalse(UpdateConfiguration.isValid(feedURL: "https://user:secret@updates.example.org/feed", publicKey: key))
        XCTAssertFalse(UpdateConfiguration.isValid(feedURL: "https://updates.example.org/feed", publicKey: "missing"))
        XCTAssertFalse(UpdateConfiguration.isValid(feedURL: "https://updates.example.org/feed", publicKey: Data(repeating: 1, count: 31).base64EncodedString()))
    }

    func testExplicitStagingModeAllowsOnlyLiteralLoopbackHTTPWithPort() {
        let key = Data(repeating: 1, count: 32).base64EncodedString()
        XCTAssertTrue(UpdateConfiguration.isValid(feedURL: "http://127.0.0.1:48999/appcast.xml", publicKey: key, allowLocalStaging: true))
        XCTAssertTrue(UpdateConfiguration.isValid(feedURL: "http://[::1]:48999/appcast.xml", publicKey: key, allowLocalStaging: true))
        for feed in ["http://127.0.0.1/appcast.xml", "http://localhost:48999/feed", "http://127.0.0.1.evil:48999/feed", "http://updates.example.org:48999/feed", "http://127.0.0.1:48999/feed?token=value", "http://user@127.0.0.1:48999/feed", "http://127.0.0.1:48999/feed#fragment"] {
            XCTAssertFalse(UpdateConfiguration.isValid(feedURL: feed, publicKey: key, allowLocalStaging: true), feed)
        }
        XCTAssertFalse(UpdateConfiguration.isValid(feedURL: "http://127.0.0.1:48999/appcast.xml", publicKey: key))
    }
}
