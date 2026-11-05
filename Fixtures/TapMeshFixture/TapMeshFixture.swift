import SwiftUI

@main
struct TapMeshFixtureApp: App {
    var body: some Scene {
        WindowGroup { FixtureView() }
    }
}

struct FixtureView: View {
    @State private var name = ""
    @State private var replacement = "Existing value"
    @State private var count = 0
    @State private var showAlert = false
    @State private var scale = 1.0
    @State private var offset = CGSize.zero
    @State private var selected = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    Text("TapMesh test device")
                        .font(.title.bold())
                        .accessibilityIdentifier("fixture-title")
                    Text("Tap count: \(count)")
                        .accessibilityIdentifier("tap-count")
                    Button("Increment") { count += 1 }
                        .buttonStyle(.borderedProminent)
                        .accessibilityIdentifier("increment-button")
                    TextField("Enter name", text: $name)
                        .textFieldStyle(.roundedBorder)
                        .accessibilityIdentifier("name-field")
                    Text("Entered: \(name)")
                        .accessibilityIdentifier("entered-value")
                    TextField("Replace value", text: $replacement)
                        .textFieldStyle(.roundedBorder)
                        .accessibilityIdentifier("replace-field")
                    Toggle("Fixture toggle", isOn: $selected)
                        .accessibilityIdentifier("fixture-toggle")
                    HStack {
                        Button("Duplicate") { count += 10 }
                            .accessibilityIdentifier("duplicate-left")
                        Spacer()
                        Button("Duplicate") { count += 20 }
                            .accessibilityIdentifier("duplicate-right")
                    }
                    Button("Show alert") { showAlert = true }
                        .accessibilityIdentifier("alert-button")
                    NavigationLink("Open detail") {
                        Text("TapMesh detail screen")
                            .accessibilityIdentifier("detail-content")
                            .navigationTitle("Fixture detail")
                    }
                    .accessibilityIdentifier("detail-link")
                    RoundedRectangle(cornerRadius: 20)
                        .fill(.indigo.gradient)
                        .frame(height: 100)
                        .overlay(Text("Drag and pinch").foregroundStyle(.white))
                        .scaleEffect(scale)
                        .offset(offset)
                        .gesture(DragGesture().onChanged { offset = $0.translation })
                        .simultaneousGesture(MagnifyGesture().onChanged { scale = $0.magnification })
                        .accessibilityLabel("Gesture surface")
                        .accessibilityIdentifier("gesture-surface")
                    Text("Scale: \(scale, specifier: "%.2f")")
                    ForEach(1...20, id: \.self) { index in
                        Text("Fixture row \(index)")
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(12)
                            .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
                            .accessibilityIdentifier("row-\(index)")
                    }
                    Button("Crash fixture") { fatalError("TapMesh intentional qualification crash") }
                        .tint(.red)
                        .accessibilityIdentifier("crash-fixture")
                }
                .padding(24)
            }
            .navigationTitle("TapMesh Fixture")
            .alert("Fixture alert", isPresented: $showAlert) {
                Button("Cancel", role: .cancel) { }
                Button("Continue") { count += 100 }
            } message: { Text("An app-owned alert for automation qualification.") }
            .onAppear {
                // Keep the disposable test screen awake only while this app is
                // foreground, without changing the user's Auto-Lock setting.
                UIApplication.shared.isIdleTimerDisabled = true
                let url = URL.documentsDirectory.appending(path: "tapmesh-fixture.json")
                try? Data("{\"fixture\":\"TapMesh\",\"version\":1}".utf8).write(to: url, options: .atomic)
            }
        }
    }
}
