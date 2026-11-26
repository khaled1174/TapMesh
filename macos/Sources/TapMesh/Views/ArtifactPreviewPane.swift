import AVKit
import SwiftUI
import TapMeshCore

/// The inline preview above an artifact's facts on the Artifacts page.
struct ArtifactPreviewPane: View {
    let artifact: ArtifactEntry
    @ObservedObject var model: ArtifactPreviewModel
    @ObservedObject var store: WorkspaceStore

    private static let height: CGFloat = 300

    var body: some View {
        content
            .frame(maxWidth: .infinity)
            .frame(height: Self.height)
            .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 8))
            .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(.separator))
            .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    @ViewBuilder private var content: some View {
        switch model.phase {
        case .empty:
            Color.clear
        case .loading:
            VStack(spacing: 10) { ProgressView().controlSize(.small); Text("Loading preview…").foregroundStyle(.secondary) }
        case .notReady:
            message("hourglass", "This file is still being written. Its preview appears when it is finished.")
        case .unavailable:
            message("xmark.circle", "This file is no longer available, so it can't be previewed.")
        case .offline:
            message("bolt.horizontal.circle", "TapMesh isn't connected, so the preview can't be loaded. Reconnect, then select the file again.")
        case .needsConfirmation(let size):
            VStack(spacing: 10) {
                Image(systemName: icon).font(.largeTitle).foregroundStyle(.secondary).accessibilityHidden(true)
                Text(size.map { String(localized: "This file is \(ArtifactPreviewPolicy.sizeText($0)), so it isn't loaded automatically.") } ?? String(localized: "This file's size isn't known, so it isn't loaded automatically."))
                    .multilineTextAlignment(.center).foregroundStyle(.secondary)
                Button("Load Preview") { model.show(artifact, store: store, userRequested: true) }
            }.padding()
        case .failed(let sentence):
            VStack(spacing: 10) {
                Image(systemName: "exclamationmark.triangle").font(.title).foregroundStyle(.secondary).accessibilityHidden(true)
                Text(sentence).multilineTextAlignment(.center).foregroundStyle(.secondary)
                Button("Try Again") { model.show(artifact, store: store, userRequested: true) }
            }.padding()
        case .ready(.image(let image)):
            Image(nsImage: image)
                .resizable()
                .scaledToFit()
                .overlay(Rectangle().strokeBorder(.separator))
                .padding(8)
                .environment(\.layoutDirection, .leftToRight)
                .accessibilityLabel("Preview of \(artifact.filename)")
        case .ready(.video(let player)):
            VideoPlayer(player: player)
                .environment(\.layoutDirection, .leftToRight)
                .accessibilityLabel("Recording \(artifact.filename)")
        case .ready(.text(let text, let truncated)):
            VStack(alignment: .leading, spacing: 0) {
                ScrollView {
                    Text(verbatim: text)
                        .font(.caption.monospaced())
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(10)
                        .environment(\.layoutDirection, .leftToRight)
                }
                if truncated {
                    Divider()
                    Text("Showing the first \(ArtifactPreviewPolicy.sizeText(Int64(ArtifactPreviewPolicy.textPreviewLimitBytes))). Use Quick Look or save a copy to see the whole file.")
                        .font(.caption).foregroundStyle(.secondary)
                        .padding(.horizontal, 10).padding(.vertical, 6)
                }
            }
        case .ready(.none):
            VStack(spacing: 8) {
                Image(systemName: icon).font(.largeTitle).foregroundStyle(.secondary).accessibilityHidden(true)
                Text(verbatim: artifact.filename).font(.callout).lineLimit(2).truncationMode(.middle)
                Text("Preview isn't available for this kind of file.").font(.callout).foregroundStyle(.secondary)
            }.multilineTextAlignment(.center).padding()
        }
    }

    private var icon: String { ArtifactSymbol.name(for: artifact) }

    private func message(_ symbol: String, _ text: LocalizedStringKey) -> some View {
        VStack(spacing: 10) {
            Image(systemName: symbol).font(.title).foregroundStyle(.secondary).accessibilityHidden(true)
            Text(text).multilineTextAlignment(.center).foregroundStyle(.secondary)
        }.padding()
    }
}

/// One SF Symbol per artifact kind, for list rows and the no-preview state.
enum ArtifactSymbol {
    static func name(for artifact: ArtifactEntry) -> String {
        switch artifact.kind {
        case "crash": return "exclamationmark.triangle"
        case "app_container": return "shippingbox"
        case "build_log": return "hammer"
        default: break
        }
        switch ArtifactPreviewPolicy.previewKind(kind: artifact.kind, filename: artifact.filename) {
        case .image: return "photo"
        case .video: return "video"
        case .text: return "doc.text"
        case .unsupported: return "doc"
        }
    }
}
