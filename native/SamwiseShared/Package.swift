// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "SamwiseShared",
    platforms: [.macOS(.v15), .iOS(.v18)],
    products: [.library(name: "SamwiseShared", targets: ["SamwiseShared"])],
    targets: [
        .target(name: "SamwiseShared"),
        .testTarget(name: "SamwiseSharedTests", dependencies: ["SamwiseShared"]),
    ]
)
