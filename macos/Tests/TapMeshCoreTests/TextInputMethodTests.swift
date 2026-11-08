import XCTest
@testable import TapMeshCore

final class TextInputMethodTests: XCTestCase {
    func testUnicodeRemainsExactAndClipboardRequiresExplicitMethod() throws {
        let text = "العَرَبِيَّة 👩🏽‍💻\nA e\u{301}"
        let keyboard = TextInputMethod.keyboard.typeArguments(text: text)
        let clipboard = TextInputMethod.clipboard.typeArguments(text: text)
        XCTAssertEqual(keyboard["input_method"], .string("keyboard"))
        XCTAssertEqual(clipboard["input_method"], .string("clipboard"))
        XCTAssertEqual(keyboard["text"], clipboard["text"])
        let roundTrip = try JSONDecoder().decode(JSONValue.self, from: JSONEncoder().encode(JSONValue.object(clipboard)))
        XCTAssertEqual(roundTrip["text"]?.stringValue?.utf8.map { $0 }, Array(text.utf8))
        let target = JSONValue.object(["ref": .string("field"), "snapshot_id": .string("snapshot")])
        let fill = TextInputMethod.clipboard.fillArguments(text: text, target: target)
        XCTAssertEqual(fill["target"], target)
        XCTAssertEqual(fill["submit"], .bool(false))
        XCTAssertEqual(Set(fill.keys), Set(["text", "target", "submit", "input_method"]))
    }
    func testLayoutDeclarationAppliesOnlyToExplicitKeyboardMethod() {
        XCTAssertNil(TextInputMethod.keyboard.typeArguments(text: "A")["keyboard_layout"])
        XCTAssertEqual(TextInputMethod.keyboard.typeArguments(text: "A", keyboardLayout: .us)["keyboard_layout"], .string("us"))
        let target = JSONValue.object(["ref": .string("field"), "snapshot_id": .string("snapshot")])
        XCTAssertEqual(TextInputMethod.keyboard.fillArguments(text: "A", target: target, keyboardLayout: .us)["keyboard_layout"], .string("us"))
        XCTAssertNil(TextInputMethod.clipboard.typeArguments(text: "العربية", keyboardLayout: .us)["keyboard_layout"])
        XCTAssertNil(TextInputMethod.clipboard.fillArguments(text: "العربية", target: target, keyboardLayout: .us)["keyboard_layout"])
    }
    func testOrdinaryFieldDeclarationRequiresExplicitInput() {
        XCTAssertNil(TextInputMethod.keyboard.typeArguments(text: "A")["field_context"])
        XCTAssertNil(TextInputMethod.clipboard.typeArguments(text: "العربية")["field_context"])
        XCTAssertEqual(TextInputMethod.keyboard.typeArguments(text: "A", fieldContext: .ordinaryApp)["field_context"], .string("ordinary_app"))
        XCTAssertEqual(TextInputMethod.clipboard.typeArguments(text: "العربية", fieldContext: .ordinaryApp)["field_context"], .string("ordinary_app"))
        let target = JSONValue.object(["ref": .string("field"), "snapshot_id": .string("snapshot")])
        XCTAssertEqual(TextInputMethod.clipboard.fillArguments(text: "العربية", target: target, fieldContext: .ordinaryApp)["field_context"], .string("ordinary_app"))
    }
    func testTextTargetValidationIsAnExplicitReplacementOption() {
        let target = JSONValue.object(["ref": .string("field"), "snapshot_id": .string("snapshot")])
        let exact = TextInputMethod.keyboard.fillArguments(text: "A", target: target, fieldContext: .ordinaryApp)
        XCTAssertNil(exact["frame_validation"])
        let tolerant = TextInputMethod.keyboard.fillArguments(text: "A", target: target, fieldContext: .ordinaryApp, frameValidation: .textTarget)
        XCTAssertEqual(tolerant["frame_validation"], .string("text_target"))
        XCTAssertEqual(tolerant["target"], target)
        XCTAssertEqual(tolerant["submit"], .bool(false))
        XCTAssertEqual(tolerant["field_context"], .string("ordinary_app"))
        XCTAssertNil(TextInputMethod.keyboard.typeArguments(text: "A", fieldContext: .ordinaryApp)["frame_validation"])
    }
}
