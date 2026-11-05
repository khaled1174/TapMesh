import SwiftUI
import TapMeshCore

struct ToolExplorerView: View {
    @ObservedObject var store: WorkspaceStore
    @State private var selected: String?
    @State private var search = ""
    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "Tool Catalog", subtitle: "Every tool uses the shared service schema, capability checks, and local approval policy.", icon: "curlybraces") {
                Text("\(store.tools.count) tools").font(.callout).foregroundStyle(.secondary)
            }
            Divider()
            HSplitView {
                VStack(spacing: 0) {
                    TextField("Find a tool", text: $search).textFieldStyle(.roundedBorder).padding(12)
                    List(filtered, selection: $selected) { tool in
                        VStack(alignment: .leading, spacing: 5) { Text(tool.name).font(.callout.monospaced()); Text(tool.description).font(.caption).foregroundStyle(.secondary).lineLimit(2) }.padding(.vertical, 5).tag(tool.name)
                    }.listStyle(.inset)
                }.frame(minWidth: 240, idealWidth: 280, maxWidth: 370)
                if let tool = store.tools.first(where: { $0.name == selected }) { ToolDetailView(store: store, tool: tool).id(tool.name).frame(minWidth: 400) }
                else { ContentUnavailableView("Explore the service tools", systemImage: "curlybraces", description: Text(store.tools.isEmpty ? "Connect to the server to load the canonical tool catalog." : "Choose a tool to see its schema and execute a validated request.")).frame(maxWidth: .infinity) }
            }
        }
    }
    private var filtered: [ToolDefinition] { store.tools.filter { search.isEmpty || $0.name.localizedCaseInsensitiveContains(search) || $0.description.localizedCaseInsensitiveContains(search) } }
}

struct ToolDetailView: View {
    @ObservedObject var store: WorkspaceStore
    let tool: ToolDefinition
    @State private var mode = "Form"
    @State private var fields: [String: String] = [:]
    @State private var included: Set<String> = []
    @State private var json = "{}"
    @State private var validation: [String] = []
    @State private var confirmation = false
    @State private var showSchema = false
    @State private var result: JSONValue?
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Text(tool.name).font(.title2.monospaced().weight(.semibold))
                Text(tool.description).foregroundStyle(.secondary)
                HStack {
                    Label(tool.mutation ? "Changes device state" : "Observation", systemImage: tool.mutation ? "hand.tap" : "eye")
                    Spacer()
                    Text("Policy \(tool.policy)")
                }.font(.caption).foregroundStyle(.secondary)
                if tool.deviceBound { Text("Target: \(store.selectedDevice?.name ?? "No device selected")").font(.callout).foregroundStyle(store.selectedID == nil ? .orange : .secondary) }
                if let capability = store.selectedDevice?.capabilities[tool.capability] { Text("\(capability.status.replacingOccurrences(of: "_", with: " ")) · \(capability.reason)").font(.caption).foregroundStyle(.secondary) }
                Picker("Argument editor", selection: $mode) { Text("Form").tag("Form"); Text("JSON").tag("JSON") }.pickerStyle(.segmented)
                if mode == "Form" {
                    if properties.isEmpty { Text("This tool has no additional arguments.").foregroundStyle(.secondary) }
                    ForEach(properties.keys.sorted(), id: \.self) { key in
                        if let schema = properties[key] {
                            SchemaField(name: key, schema: schema, required: required.contains(key), included: Binding(get: { included.contains(key) || required.contains(key) }, set: { if $0 { included.insert(key) } else { included.remove(key) } }), value: Binding(get: { fields[key] ?? "" }, set: { fields[key] = $0 }))
                        }
                    }
                    if tool.schema["oneOf"] != nil || tool.schema["anyOf"] != nil { Text("This tool has alternative argument shapes. Use JSON to supply the exact shape shown in the schema.").font(.caption).foregroundStyle(.secondary) }
                } else {
                    TextEditor(text: $json).font(.system(.body, design: .monospaced)).frame(minHeight: 180).padding(6).overlay(RoundedRectangle(cornerRadius: 6).stroke(.quaternary)).accessibilityLabel("Tool arguments as JSON")
                    Button("Use Form Values") { do { json = try formArguments().prettyPrinted; validation = [] } catch { validation = [error.localizedDescription] } }
                }
                if !validation.isEmpty { VStack(alignment: .leading, spacing: 5) { ForEach(validation, id: \.self) { Text($0).font(.callout).foregroundStyle(.orange) } } }
                HStack {
                    Button(showSchema ? "Hide Schema" : "Show Schema") { showSchema.toggle() }
                    Spacer()
                    Button("Execute \(tool.name)") { prepareExecution() }.buttonStyle(.borderedProminent).disabled(!store.online || store.busy || (tool.deviceBound && store.selectedID == nil))
                }
                if showSchema { GroupBox("Input schema") { JSONResultView(value: tool.schema).padding(8) } }
                if let result { Divider(); Text("Result").font(.headline); JSONResultView(value: result) }
            }.padding(24)
        }
        .onAppear { initialize() }
        .confirmationDialog("Run \(tool.name)?", isPresented: $confirmation, titleVisibility: .visible) {
            Button("Run Requested Action", role: tool.policy.contains("D") ? .destructive : nil) { execute() }
            Button("Cancel", role: .cancel) { }
        } message: { Text("Review the arguments and target. This tool may change data, export sensitive information, or run a host build. The local authorization applies only to this exact request.") }
    }
    private var properties: [String: JSONValue] { (tool.schema["properties"]?.objectValue ?? [:]).filter { $0.key != "device_id" } }
    private var required: Set<String> { Set(tool.schema["required"]?.arrayValue?.compactMap(\.stringValue) ?? []) }
    private func initialize() {
        for (key, schema) in properties {
            if let value = schema["default"] { fields[key] = value.stringValue ?? value.prettyPrinted; included.insert(key) }
            else if let first = schema["enum"]?.arrayValue?.first { fields[key] = first.stringValue ?? first.prettyPrinted }
            else if schema["type"]?.stringValue == "boolean" { fields[key] = "false" }
            else if schema["type"]?.stringValue == "object" { fields[key] = "{}" }
            else if schema["type"]?.stringValue == "array" { fields[key] = "[]" }
        }
    }
    private func formArguments() throws -> JSONValue {
        var arguments: [String: JSONValue] = [:]
        for (key, schema) in properties where included.contains(key) || required.contains(key) {
            let raw = fields[key] ?? ""
            if schema["type"]?.stringValue == "string" || schema["enum"]?.arrayValue?.first?.stringValue != nil { arguments[key] = .string(raw) }
            else {
                do { arguments[key] = try JSONValue.parse(raw) }
                catch { throw ClientError.protocolError("\(key): enter valid \(schema["type"]?.stringValue ?? "JSON").") }
            }
        }
        return .object(arguments)
    }
    private func arguments() throws -> JSONValue {
        var value = mode == "Form" ? try formArguments() : try JSONValue.parse(json)
        guard var object = value.objectValue else { throw ClientError.protocolError("Arguments must be a JSON object.") }
        if tool.deviceBound, let selectedID = store.selectedID {
            if let explicit = object["device_id"]?.stringValue, explicit != selectedID { throw ClientError.protocolError("The JSON device_id differs from the active target. Select that device in the sidebar first.") }
            object["device_id"] = .string(selectedID); value = .object(object)
        }
        return value
    }
    private func prepareExecution() {
        do {
            let value = try arguments(); validation = SchemaValidator.errors(for: value, schema: tool.schema)
            guard validation.isEmpty else { return }
            if tool.policy != "O" && tool.policy != "C" { confirmation = true } else { execute() }
        } catch { validation = [error.localizedDescription] }
    }
    private func execute() {
        do {
            let value = try arguments()
            guard let args = value.objectValue else { return }
            Task {
                if let envelope = await store.run(tool.name, arguments: args, bindDevice: false) {
                    result = (try? JSONDecoder().decode(JSONValue.self, from: JSONEncoder().encode(envelope))) ?? envelope.data
                }
            }
        } catch { validation = [error.localizedDescription] }
    }
}

struct SchemaField: View {
    let name: String
    let schema: JSONValue
    let required: Bool
    @Binding var included: Bool
    @Binding var value: String
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                if required { Text(name).font(.callout.weight(.medium)); Text("Required").font(.caption).foregroundStyle(.secondary) }
                else { Toggle(name, isOn: $included).toggleStyle(.checkbox).font(.callout.weight(.medium)) }
                Spacer()
                Text(schema["type"]?.stringValue ?? "JSON").font(.caption.monospaced()).foregroundStyle(.tertiary)
            }
            Group {
                if let values = schema["enum"]?.arrayValue {
                    Picker(name, selection: $value) { ForEach(values.map(\.scalarDescription), id: \.self) { Text($0).tag($0) } }.labelsHidden()
                } else if schema["type"]?.stringValue == "boolean" {
                    Toggle("Enabled", isOn: Binding(get: { value == "true" }, set: { value = $0 ? "true" : "false" })).toggleStyle(.switch).controlSize(.small)
                } else if schema["type"]?.stringValue == "object" || schema["type"]?.stringValue == "array" || schema["oneOf"] != nil {
                    TextEditor(text: $value).font(.system(.callout, design: .monospaced)).frame(height: 85).padding(4).overlay(RoundedRectangle(cornerRadius: 5).stroke(.quaternary))
                } else { TextField(name, text: $value).textFieldStyle(.roundedBorder) }
            }.disabled(!included && !required).accessibilityLabel(name)
            if let description = schema["description"]?.stringValue { Text(description).font(.caption).foregroundStyle(.secondary) }
        }
    }
}
