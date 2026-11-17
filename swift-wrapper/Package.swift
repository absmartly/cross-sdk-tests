// swift-tools-version:6.0
import PackageDescription

let package = Package(
    name: "ABsmartlyWrapper",
    platforms: [
        .macOS(.v13)
    ],
    dependencies: [
        .package(url: "https://github.com/vapor/vapor.git", from: "4.89.0"),
        .package(path: "../../swift-sdk")
    ],
    targets: [
        .target(
            name: "App",
            dependencies: [
                .product(name: "Vapor", package: "vapor"),
                .product(name: "ABsmartly", package: "swift-sdk")
            ],
            path: "Sources/App",
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
        .executableTarget(
            name: "Run",
            dependencies: [
                .target(name: "App")
            ],
            path: "Sources/Run",
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
        .testTarget(
            name: "AppTests",
            dependencies: [
                .target(name: "App"),
                .product(name: "ABsmartly", package: "swift-sdk")
            ],
            path: "Tests/AppTests",
            swiftSettings: [.swiftLanguageMode(.v5)]
        )
    ]
)
