// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "SpecterQARunner",
    platforms: [.iOS(.v15)],
    targets: [
        .target(
            name: "SpecterQARunner",
            path: "Sources"
        )
    ]
)
