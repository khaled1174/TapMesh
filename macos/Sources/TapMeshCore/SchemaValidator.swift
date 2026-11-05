import Foundation

/// Local feedback only. The shared server remains the authority for all validation and policy.
public enum SchemaValidator {
    public static func errors(for value: JSONValue, schema: JSONValue, path: String = "arguments") -> [String] {
        var errors: [String] = []
        if let choices = schema["enum"]?.arrayValue, !choices.contains(value) { errors.append("\(path): choose a listed value.") }
        if let oneOf = schema["oneOf"]?.arrayValue {
            if oneOf.filter({ Self.errors(for: value, schema: $0, path: path).isEmpty }).count != 1 { errors.append("\(path): must match exactly one allowed shape.") }
        }
        let type = schema["type"]?.stringValue
        switch (type, value) {
        case ("object", .object(let object)):
            let props = schema["properties"]?.objectValue ?? [:]
            for key in schema["required"]?.arrayValue?.compactMap(\.stringValue) ?? [] where object[key] == nil { errors.append("\(path).\(key) is required.") }
            for (key, child) in object {
                if let childSchema = props[key] { errors += Self.errors(for: child, schema: childSchema, path: "\(path).\(key)") }
                else if schema["additionalProperties"]?.boolValue == false { errors.append("\(path).\(key) is not an accepted field.") }
            }
        case ("array", .array(let array)):
            if let minimum = schema["minItems"]?.doubleValue, Double(array.count) < minimum { errors.append("\(path) needs at least \(Int(minimum)) entries.") }
            if let maximum = schema["maxItems"]?.doubleValue, Double(array.count) > maximum { errors.append("\(path) accepts at most \(Int(maximum)) entries.") }
            if let items = schema["items"] { for (i, item) in array.enumerated() { errors += Self.errors(for: item, schema: items, path: "\(path)[\(i)]") } }
        case ("string", .string(let string)):
            if let max = schema["maxLength"]?.doubleValue, Double(string.count) > max { errors.append("\(path) is too long.") }
            if let min = schema["minLength"]?.doubleValue, Double(string.count) < min { errors.append("\(path) is too short.") }
        case ("integer", .number(let number)), ("number", .number(let number)):
            if type == "integer", number.rounded() != number { errors.append("\(path) must be a whole number.") }
            if let min = schema["minimum"]?.doubleValue, number < min { errors.append("\(path) must be at least \(min).") }
            if let max = schema["maximum"]?.doubleValue, number > max { errors.append("\(path) must be at most \(max).") }
        case ("boolean", .bool), ("null", .null), (nil, _): break
        default: if let type { errors.append("\(path) must be \(type).") }
        }
        return errors
    }
}
