import Foundation

/// One step of a `build_and_run` request as reported by the service (agent/backends.py `_build`).
public struct BuildStage: Equatable, Sendable {
    public let name: String
    public let status: String
    public var succeeded: Bool { status == "success" }
    public var failed: Bool { status == "failed" }
}

/// What the Builds page needs from one `build_and_run` response, success or failure.
///
/// On success the service returns `data: {stages, bundle_id, log_artifact, ...}`. On failure
/// the stages travel in `error.details.stages` instead (backends.py `_build`): an Xcode failure
/// lists the build step as failed; a missing app product or failed install lists only the
/// build step (succeeded), because the failing step never got its own entry; a failed launch
/// carries no stages at all. `stages` is nil whenever the shape is not the expected one, so
/// callers fall back to the raw payload instead of guessing.
public struct BuildReport: Equatable, Sendable {
    public enum Outcome: Equatable, Sendable { case succeeded, partial(String), failed }

    public let outcome: Outcome
    /// Nil when the response has no stage list in the expected shape.
    public let stages: [BuildStage]?
    public let bundleID: String?
    /// The saved build log, when the service reported one.
    public let logArtifactID: String?
    public let errorCode: String?
    public let errorMessage: String?
    public let sideEffectState: String?
    /// The complete payload: `data` on success; `{error, data?}` on failure.
    public let raw: JSONValue?

    /// The first step the service marked as failed.
    public var failedStage: BuildStage? { stages?.first(where: \.failed) }
    /// A failure after every listed step succeeded: the step that failed was not reported as its own entry.
    public var failedAfterListedStages: Bool { outcome == .failed && stages?.isEmpty == false && failedStage == nil }

    public init(envelope: ToolEnvelope) {
        let source: JSONValue?
        if let error = envelope.error {
            outcome = .failed
            source = error.details
            errorCode = error.code; errorMessage = error.message; sideEffectState = error.sideEffectState
            var payload: [String: JSONValue] = [:]
            if let encoded = try? JSONDecoder().decode(JSONValue.self, from: JSONEncoder().encode(error)) { payload["error"] = encoded }
            if let data = envelope.data { payload["data"] = data }
            raw = .object(payload)
        } else {
            outcome = envelope.isSuccess ? .succeeded : .partial(envelope.status)
            source = envelope.data
            errorCode = nil; errorMessage = nil; sideEffectState = nil
            raw = envelope.data
        }
        stages = Self.stages(in: source)
        bundleID = source?["bundle_id"]?.stringValue
        logArtifactID = Self.logArtifactID(in: source, stages: source?["stages"]?.arrayValue)
    }

    /// Only a non-empty list where every entry names its step and status is used.
    static func stages(in value: JSONValue?) -> [BuildStage]? {
        guard let items = value?["stages"]?.arrayValue, !items.isEmpty else { return nil }
        var result: [BuildStage] = []
        for item in items {
            guard let name = item["stage"]?.stringValue, let status = item["status"]?.stringValue else { return nil }
            result.append(BuildStage(name: name, status: status))
        }
        return result
    }

    private static func logArtifactID(in value: JSONValue?, stages: [JSONValue]?) -> String? {
        if let id = value?["log_artifact"]?["artifact_id"]?.stringValue { return id }
        return stages?.lazy.compactMap { $0["log_artifact"]?["artifact_id"]?.stringValue }.first
    }
}

/// Plain wording for an error's `side_effect_state`, shared by the error banner and Activity.
public enum DeliveryNote {
    public static func sentence(for state: String?) -> String {
        switch state {
        case "not_sent"?: String(localized: "Nothing was sent to the device.")
        case "unknown"?: String(localized: "The action may have reached the device. Check it before retrying.")
        case "confirmed"?: String(localized: "The action did reach the device.")
        case nil: String(localized: "TapMesh couldn't tell whether the action reached the device. Check it before retrying.")
        case let other?: String(localized: "Delivery state: \(other).")
        }
    }
    /// True unless the service said plainly whether the action was delivered.
    public static func isUncertain(_ state: String?) -> Bool { state != "not_sent" && state != "confirmed" }
}
