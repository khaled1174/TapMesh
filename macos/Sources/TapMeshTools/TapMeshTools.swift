import AVFoundation
import CoreML
import Foundation
import ImageIO
import Vision

/// A fixed, bounded JSON interface to system media and text recognition services.
/// The parent validates permissions and owns the child process deadline.
@main
struct TapMeshTools {
    static let outputLimit = 1_048_576

    enum Failure: String, Error {
        case invalidArguments = "invalid_arguments"
        case invalidFile = "invalid_file"
        case invalidImage = "invalid_image"
        case invalidMedia = "invalid_media"
        case unsupportedLanguage = "unsupported_language"
        case recognitionUnavailable = "recognition_unavailable"
        case recognitionFailed = "recognition_failed"
        case outputTooLarge = "output_too_large"

        var message: String {
            switch self {
            case .invalidArguments: "The helper arguments or logical image size are invalid."
            case .invalidFile: "A readable local regular file is required."
            case .invalidImage: "A valid PNG or JPEG image within the size limits is required."
            case .invalidMedia: "The file does not contain finalized video with a positive duration."
            case .unsupportedLanguage: "The requested recognition language is unavailable on this Mac."
            case .recognitionUnavailable: "System text recognition is unavailable on this Mac."
            case .recognitionFailed: "System text recognition did not finish successfully."
            case .outputTooLarge: "The helper result exceeds the output limit."
            }
        }
    }

    static func main() async {
        do {
            let data = try await encodedResult(Array(CommandLine.arguments.dropFirst()))
            FileHandle.standardOutput.write(data + Data([10]))
        } catch {
            let failure = (error as? Failure) ?? .recognitionFailed
            let result = ["error": ["code": failure.rawValue, "message": failure.message]]
            if let data = try? JSONSerialization.data(withJSONObject: result, options: [.sortedKeys]) {
                FileHandle.standardError.write(data + Data([10]))
            }
            exit(1)
        }
    }

    // Older Swift 6 toolchains run nonisolated async work on the generic
    // executor. Encode there so opaque Foundation JSON never crosses actors.
    static func encodedResult(_ arguments: [String]) async throws -> Data {
        let result = try await execute(arguments)
        let data = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
        guard data.count < outputLimit else { throw Failure.outputTooLarge }
        return data
    }

    static func execute(_ arguments: [String]) async throws -> [String: Any] {
        switch arguments.first {
        case "ocr-capabilities":
            guard arguments.count == 1 else { throw Failure.invalidArguments }
            return capabilities()
        case "probe":
            guard arguments.count == 2 else { throw Failure.invalidArguments }
            return try await probe(arguments[1])
        case "ocr":
            guard arguments.count == 8, arguments[2] == "--logical-width",
                  arguments[4] == "--logical-height", arguments[6] == "--languages",
                  let width = Double(arguments[3]), let height = Double(arguments[5]),
                  width.isFinite, height.isFinite, width > 0, height > 0,
                  width <= 16384, height <= 16384 else { throw Failure.invalidArguments }
            return try recognize(arguments[1], width: width, height: height, languageArgument: arguments[7])
        default: throw Failure.invalidArguments
        }
    }

    static func fileURL(_ path: String, maximumBytes: Int? = nil) throws -> URL {
        guard path.hasPrefix("/"), !path.utf8.contains(0) else { throw Failure.invalidFile }
        let url = URL(fileURLWithPath: path)
        guard let values = try? url.resourceValues(forKeys: [.isRegularFileKey, .fileSizeKey]),
              values.isRegularFile == true, let size = values.fileSize,
              size > 0, maximumBytes.map({ size <= $0 }) ?? true,
              FileManager.default.isReadableFile(atPath: path) else { throw Failure.invalidFile }
        return url
    }

    static func probe(_ path: String) async throws -> [String: Any] {
        let url = try fileURL(path)
        let asset = AVURLAsset(url: url, options: [AVURLAssetPreferPreciseDurationAndTimingKey: true])
        do {
            let duration = try await asset.load(.duration).seconds
            let tracks = try await asset.loadTracks(withMediaType: .video)
            guard duration.isFinite, duration > 0, !tracks.isEmpty else { throw Failure.invalidMedia }
            return ["duration_seconds": duration, "video_tracks": tracks.count]
        } catch { throw Failure.invalidMedia }
    }

    static func recognitionRequest() -> VNRecognizeTextRequest {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = true
        request.preferBackgroundProcessing = true
        // A short-lived helper must not wait for on-device Neural Engine model
        // compilation. Select the publicly advertised CPU for each valid stage.
        if let stages = try? request.supportedComputeStageDevices {
            for (stage, devices) in stages {
                if let cpu = devices.first(where: { if case .cpu = $0 { true } else { false } }) {
                    request.setComputeDevice(cpu, for: stage)
                }
            }
        }
        return request
    }

    static func languageMapping(_ supported: [String]) -> [String: String] {
        var result: [String: String] = [:]
        for (code, prefix, preferred) in [("eng", "en", "en-US"), ("ara", "ar", "ar-SA")] {
            if supported.contains(preferred) { result[code] = preferred }
            else if let tag = supported.first(where: { $0.split(separator: "-").first == Substring(prefix) }) { result[code] = tag }
        }
        return result
    }

    static func capabilities() -> [String: Any] {
        let supported = (try? recognitionRequest().supportedRecognitionLanguages()) ?? []
        let languages = languageMapping(supported).keys.sorted()
        return ["status": languages.isEmpty ? "unavailable" : "available", "engine": "vision",
                "languages": languages, "supported_languages": supported.sorted()]
    }

    static func recognize(_ path: String, width: Double, height: Double, languageArgument: String) throws -> [String: Any] {
        let request = recognitionRequest()
        guard let supported = try? request.supportedRecognitionLanguages() else { throw Failure.recognitionUnavailable }
        let mapping = languageMapping(supported)
        let languages = languageArgument.split(separator: "+", omittingEmptySubsequences: false).map(String.init)
        guard !languages.isEmpty, languages.count <= 2, Set(languages).count == languages.count,
              languages.allSatisfy({ mapping[$0] != nil }) else { throw Failure.unsupportedLanguage }
        request.recognitionLanguages = languages.compactMap { mapping[$0] }
        request.automaticallyDetectsLanguage = languages.count > 1
        let url = try fileURL(path, maximumBytes: 8 * 1024 * 1024)
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
              CGImageSourceGetCount(source) == 1,
              let type = CGImageSourceGetType(source) as String?, ["public.png", "public.jpeg"].contains(type),
              let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let pixelWidth = properties[kCGImagePropertyPixelWidth] as? Int,
              let pixelHeight = properties[kCGImagePropertyPixelHeight] as? Int,
              pixelWidth > 0, pixelHeight > 0, pixelWidth <= 8_000_000 / pixelHeight else { throw Failure.invalidImage }
        guard abs(Double(pixelWidth) / Double(pixelHeight) - width / height) <= 0.02 * width / height else { throw Failure.invalidArguments }
        guard let image = CGImageSourceCreateImageAtIndex(source, 0, [kCGImageSourceShouldCacheImmediately: true] as CFDictionary) else { throw Failure.invalidImage }
        do { try VNImageRequestHandler(cgImage: image, orientation: .up).perform([request]) }
        catch { throw Failure.recognitionFailed }
        var elements: [[String: Any]] = []
        var textBytes = 0
        for observation in (request.results ?? []).prefix(1024) {
            guard let candidate = observation.topCandidates(1).first else { continue }
            let text = String(candidate.string.prefix(4096)).trimmingCharacters(in: .whitespacesAndNewlines)
            guard !text.isEmpty else { continue }
            textBytes += text.utf8.count
            guard textBytes <= 262144 else { throw Failure.outputTooLarge }
            let rect = observation.boundingBox.intersection(CGRect(x: 0, y: 0, width: 1, height: 1))
            guard !rect.isNull, rect.width > 0, rect.height > 0 else { continue }
            elements.append(["text": text, "confidence": Double(min(1, max(0, candidate.confidence))),
                             "bounds": ["x": rect.minX * width, "y": (1 - rect.maxY) * height,
                                        "width": rect.width * width, "height": rect.height * height],
                             "role": "text", "source": "ocr"])
        }
        return ["engine": "vision", "languages": languages, "elements": elements]
    }
}
