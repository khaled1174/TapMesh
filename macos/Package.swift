// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "TapMesh",
    platforms: [.macOS(.v15)],
    products: [.executable(name: "TapMesh", targets: ["TapMesh"]), .library(name: "TapMeshCore", targets: ["TapMeshCore"]), .executable(name: "TapMeshTools", targets: ["TapMeshTools"])],
    targets: [
        .binaryTarget(name: "Sparkle", url: "https://github.com/sparkle-project/Sparkle/releases/download/2.10.1/Sparkle-for-Swift-Package-Manager.zip", checksum: "4c2e48a3e50ebcc2c88a58a4c7264a1a99c3c31857ce754357afb9cdb323022a"),
        .target(name: "TapMeshCore"),
        .executableTarget(name: "TapMeshTools"),
        .executableTarget(name: "TapMesh", dependencies: ["TapMeshCore", "Sparkle"], linkerSettings: [.unsafeFlags(["-Xlinker", "-rpath", "-Xlinker", "@executable_path/../Frameworks"])]),
        .testTarget(name: "TapMeshCoreTests", dependencies: ["TapMeshCore"])
    ]
)
