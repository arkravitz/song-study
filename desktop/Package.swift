// swift-tools-version: 5.10
import PackageDescription
let package = Package(name: "SongStudy", platforms: [.macOS(.v14)], products: [
    .executable(name: "SongStudy", targets: ["SongStudy"])
], targets: [
    .executableTarget(name: "SongStudy"),
    .testTarget(name: "SongStudyTests", dependencies: ["SongStudy"])
])
