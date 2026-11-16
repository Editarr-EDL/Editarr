// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "SamwiseNative",
    platforms: [.macOS(.v15), .iOS(.v18)],
    products: [
        .library(name: "SamwiseUI", targets: ["SamwiseUI"]),
        .executable(name: "SamwiseMacOS", targets: ["SamwiseMacOS"]),
        .executable(name: "SamwiseIOS", targets: ["SamwiseIOS"]),
    ],
    dependencies: [.package(path: "../SamwiseShared")],
    targets: [
        .target(
            name: "SamwiseUI",
            dependencies: [.product(name: "SamwiseShared", package: "SamwiseShared")],
            path: "Sources/Samwise",
            resources: [.process("Resources")]
        ),
        .executableTarget(name: "SamwiseMacOS", dependencies: ["SamwiseUI"]),
        .executableTarget(name: "SamwiseIOS", dependencies: ["SamwiseUI"]),
    ]
)
