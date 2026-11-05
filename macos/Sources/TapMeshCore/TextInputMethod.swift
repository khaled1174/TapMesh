import Foundation

public enum KeyboardLayoutDeclaration: String, Sendable { case us }
public enum TextFieldContextDeclaration: String, Sendable { case ordinaryApp = "ordinary_app" }

public enum TextInputMethod: String, CaseIterable, Sendable {
    case keyboard, clipboard
    public func typeArguments(text: String, keyboardLayout: KeyboardLayoutDeclaration? = nil, fieldContext: TextFieldContextDeclaration? = nil) -> [String: JSONValue] {
        var arguments: [String: JSONValue] = ["text": .string(text), "input_method": .string(rawValue)]
        if self == .keyboard, let keyboardLayout { arguments["keyboard_layout"] = .string(keyboardLayout.rawValue) }
        if let fieldContext { arguments["field_context"] = .string(fieldContext.rawValue) }
        return arguments
    }
    public func fillArguments(text: String, target: JSONValue, submit: Bool = false, keyboardLayout: KeyboardLayoutDeclaration? = nil, fieldContext: TextFieldContextDeclaration? = nil) -> [String: JSONValue] {
        var arguments = typeArguments(text: text, keyboardLayout: keyboardLayout, fieldContext: fieldContext)
        arguments["target"] = target; arguments["submit"] = .bool(submit)
        return arguments
    }
}
