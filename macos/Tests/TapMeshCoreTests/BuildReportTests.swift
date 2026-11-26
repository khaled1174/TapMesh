import XCTest
@testable import TapMeshCore

final class BuildReportTests: XCTestCase {
    private func envelope(_ json: String) throws -> ToolEnvelope { try JSONDecoder().decode(ToolEnvelope.self, from: Data(json.utf8)) }

    func testSuccessListsEveryStageWithBundleAndLog() throws {
        let response = try envelope(#"{"request_id":"b1","status":"success","device_id":"sim:A","data":{"stages":[{"stage":"build","status":"success","log_artifact":{"artifact_id":"log-1","kind":"build_log"}},{"stage":"install","status":"success","result":{"bundle_id":"com.example.App"}},{"stage":"launch","status":"success","result":{}}],"bundle_id":"com.example.App","log_artifact":{"artifact_id":"log-1","kind":"build_log"},"delivery":"confirmed"}}"#)
        let report = BuildReport(envelope: response)
        XCTAssertEqual(report.outcome, .succeeded)
        XCTAssertEqual(report.stages, [BuildStage(name: "build", status: "success"), BuildStage(name: "install", status: "success"), BuildStage(name: "launch", status: "success")])
        XCTAssertNil(report.failedStage)
        XCTAssertFalse(report.failedAfterListedStages)
        XCTAssertEqual(report.bundleID, "com.example.App")
        XCTAssertEqual(report.logArtifactID, "log-1")
        XCTAssertNil(report.errorCode)
        XCTAssertEqual(report.raw, response.data, "The full result keeps the complete service data")
    }

    func testFailureReadsStagesFromErrorDetails() throws {
        // Shape raised by agent/backends.py `_build` when xcodebuild exits non-zero.
        let response = try envelope(#"{"request_id":"b2","status":"error","device_id":"sim:A","error":{"code":"build_failed","message":"Xcode build failed","retryable":false,"side_effect_state":"unknown","details":{"stages":[{"stage":"build","status":"failed","log_artifact":{"artifact_id":"log-2","kind":"build_log"}}]}}}"#)
        let report = BuildReport(envelope: response)
        XCTAssertEqual(report.outcome, .failed)
        XCTAssertEqual(report.failedStage, BuildStage(name: "build", status: "failed"))
        XCTAssertFalse(report.failedAfterListedStages)
        XCTAssertEqual(report.logArtifactID, "log-2", "The log reference is read from the failed stage")
        XCTAssertEqual(report.errorCode, "build_failed")
        XCTAssertEqual(report.sideEffectState, "unknown")
        XCTAssertNil(report.bundleID)
        XCTAssertEqual(report.raw?["error"]?["details"]?["stages"]?.arrayValue?.count, 1, "The full error, details included, stays available")
        XCTAssertEqual(report.raw?["error"]?["side_effect_state"]?.stringValue, "unknown")
    }

    func testInstallFailureAfterSuccessfulBuildIsNotShownAsSuccess() throws {
        let response = try envelope(#"{"request_id":"b3","status":"error","error":{"code":"install_failed","message":"Install failed","side_effect_state":"unknown","details":{"stages":[{"stage":"build","status":"success","log_artifact":{"artifact_id":"log-3"}}],"built_app_path":"/tmp/App.app","log_artifact":{"artifact_id":"log-3"}}}}"#)
        let report = BuildReport(envelope: response)
        XCTAssertEqual(report.outcome, .failed)
        XCTAssertNil(report.failedStage)
        XCTAssertTrue(report.failedAfterListedStages, "The step after the listed ones failed without its own entry")
        XCTAssertEqual(report.logArtifactID, "log-3")
    }

    func testUnexpectedShapeFallsBackToRawPayload() throws {
        let missingStatus = try envelope(#"{"request_id":"b4","status":"success","data":{"stages":[{"stage":"build"}],"bundle_id":"com.example.App"}}"#)
        let report = BuildReport(envelope: missingStatus)
        XCTAssertNil(report.stages, "Entries without a status are not interpreted")
        XCTAssertEqual(report.raw, missingStatus.data)

        let notAList = try envelope(#"{"request_id":"b5","status":"error","error":{"code":"build_failed","message":"Failed","side_effect_state":"unknown","details":{"stages":"build"}}}"#)
        XCTAssertNil(BuildReport(envelope: notAList).stages)
        XCTAssertFalse(BuildReport(envelope: notAList).failedAfterListedStages)

        // A failed launch raises without stage details.
        let noDetails = try envelope(#"{"request_id":"b6","status":"error","error":{"code":"launch_failed","message":"Launch failed","side_effect_state":"unknown"}}"#)
        let launch = BuildReport(envelope: noDetails)
        XCTAssertEqual(launch.outcome, .failed)
        XCTAssertNil(launch.stages)
        XCTAssertEqual(launch.raw?["error"]?["code"]?.stringValue, "launch_failed")
    }

    func testEmptyResult() throws {
        let noData = try envelope(#"{"request_id":"b7","status":"success"}"#)
        let report = BuildReport(envelope: noData)
        XCTAssertEqual(report.outcome, .succeeded)
        XCTAssertNil(report.stages)
        XCTAssertNil(report.bundleID)
        XCTAssertNil(report.logArtifactID)
        XCTAssertNil(report.raw)

        let emptyStages = try envelope(#"{"request_id":"b8","status":"partial","data":{"stages":[]}}"#)
        let partial = BuildReport(envelope: emptyStages)
        XCTAssertEqual(partial.outcome, .partial("partial"))
        XCTAssertNil(partial.stages, "An empty list is treated as no step details")
    }

    func testDeliveryNoteWordingAndUncertainty() {
        XCTAssertEqual(DeliveryNote.sentence(for: "not_sent"), "Nothing was sent to the device.")
        XCTAssertEqual(DeliveryNote.sentence(for: "unknown"), "The action may have reached the device. Check it before retrying.")
        XCTAssertEqual(DeliveryNote.sentence(for: "confirmed"), "The action did reach the device.")
        XCTAssertTrue(DeliveryNote.sentence(for: nil).contains("Check it before retrying"))
        XCTAssertEqual(DeliveryNote.sentence(for: "queued"), "Delivery state: queued.")
        XCTAssertTrue(DeliveryNote.isUncertain("unknown"))
        XCTAssertTrue(DeliveryNote.isUncertain(nil))
        XCTAssertTrue(DeliveryNote.isUncertain("queued"))
        XCTAssertFalse(DeliveryNote.isUncertain("not_sent"))
        XCTAssertFalse(DeliveryNote.isUncertain("confirmed"))
    }
}
