import SwiftUI
import TapMeshCore

/// Collapsed-by-default home for identifiers, raw service data and other technical text.
/// The summary above it should be readable without opening this.
struct TechnicalDetails<Content: View>: View {
    var title: LocalizedStringKey = "Details"
    @ViewBuilder let content: () -> Content
    @State private var expanded = false
    var body: some View {
        DisclosureGroup(title, isExpanded: $expanded) {
            VStack(alignment: .leading, spacing: 8) { content() }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.top, 6)
        }
        .font(.callout)
        .foregroundStyle(.secondary)
    }
}

/// One labeled technical value, selectable so it can be copied into a bug report.
struct DetailRow: View {
    let label: LocalizedStringKey
    let value: String
    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(label).font(.caption).foregroundStyle(.secondary)
            // Identifiers, epochs and codes read left to right in every language.
            Text(verbatim: value).font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled)
                .environment(\.layoutDirection, .leftToRight)
        }
    }
}

enum DisplayText {
    /// "press_button" becomes "Press button" in English, or its translation when the app's strings file has one.
    /// The readable English words are the lookup key, so an id without a translation falls back to those words.
    static func toolName(_ tool: String) -> String {
        let words = tool.replacingOccurrences(of: "_", with: " ")
        return localized(words.prefix(1).uppercased() + words.dropFirst())
    }

    /// What a device row or header says about the device, in the user's terms.
    static func deviceKind(_ backend: String) -> String {
        backend == "simulator" ? String(localized: "Simulator") : String(localized: "iPhone / iPad")
    }

    static func serviceError(_ error: ToolError) -> String {
        if let guidance = ErrorGuide.guidance(code: error.code, serviceMessage: error.message) {
            return [guidance.messageKey, guidance.actionKey].compactMap { $0 }.map(localized).joined(separator: "\n")
        }
        return [error.message, error.suggestedAction].compactMap { $0 }.map(sentence).joined(separator: "\n")
    }

    private static func sentence(_ text: String) -> String {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let last = trimmed.last else { return trimmed }
        return ".!?".contains(last) ? trimmed : trimmed + "."
    }

    /// Looks up a key that is only known at run time. Unlike `String(localized:)`, the key and
    /// its fallback are used as plain text, so a "%" in a service-provided id is never read as a format.
    private static func localized(_ english: String) -> String {
        Bundle.main.localizedString(forKey: english, value: english, table: nil)
    }
}
