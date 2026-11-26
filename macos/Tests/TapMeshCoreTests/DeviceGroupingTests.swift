import XCTest
@testable import TapMeshCore

final class DeviceGroupingTests: XCTestCase {
    func testSectionsAndOrder() {
        let devices = [
            device("shutdown-old", name: "iPhone 16", backend: "simulator", ready: false, version: "17.5"),
            device("running", name: "iPhone 16", backend: "simulator", ready: true, version: "18.2"),
            device("phone", name: "iPhone", backend: "physical", ready: true, version: "18.2"),
            device("shutdown-new", name: "iphone 16", backend: "simulator", ready: false, version: "18.2"),
            device("tablet", name: "iPad", backend: "physical", ready: false, version: "18.2"),
            device("another-tablet", name: "iPad", backend: "physical", ready: true, version: "18.1"),
        ]

        let sections = DeviceGrouping.sections(devices)

        XCTAssertEqual(sections.map(\.section), [.connected, .needsSetup, .runningSimulators, .otherSimulators])
        XCTAssertEqual(sections.map { $0.devices.map(\.id) }, [["another-tablet", "phone"], ["tablet"], ["running"], ["shutdown-new", "shutdown-old"]])
    }

    func testEmptySectionsAreOmittedAndVersionOrderIsNumeric() {
        let devices = [
            device("old", name: "iPhone", backend: "simulator", ready: false, version: "9.3"),
            device("unknown", name: "iPhone", backend: "simulator", ready: false, version: "unknown"),
            device("new", name: "iPhone", backend: "simulator", ready: false, version: "18.0"),
        ]

        let sections = DeviceGrouping.sections(devices)

        XCTAssertEqual(sections.map(\.section), [.otherSimulators])
        XCTAssertEqual(sections.first?.devices.map(\.id), ["new", "old", "unknown"])
    }

    func testDecodesCurrentAndLegacyOSVersions() {
        XCTAssertEqual(device("phone", name: "iPhone", backend: "physical", ready: true, version: "26.0").osVersion, "26.0")
        XCTAssertEqual(device("sim", name: "iPhone", backend: "simulator", ready: true, version: "iOS 18 2").osVersion, "18.2")
        XCTAssertNil(device("unknown", name: "iPhone", backend: "physical", ready: false, version: "unknown").osVersion)
        XCTAssertNil(device("missing", name: "iPhone", backend: "physical", ready: false, version: nil).osVersion)
    }

    private func device(_ id: String, name: String, backend: String, ready: Bool, version: String?) -> Device {
        Device(.object([
            "device_id": .string(id),
            "name": .string(name),
            "backend": .string(backend),
            "ready": .bool(ready),
            "os_version": version.map(JSONValue.string) ?? .null,
        ]))!
    }
}
