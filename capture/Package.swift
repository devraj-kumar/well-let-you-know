// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "capture",
    platforms: [.macOS("14.4")],
    targets: [
        .executableTarget(
            name: "capturecli",
            exclude: ["Info.plist"],
            linkerSettings: [
                // Embed Info.plist so TCC can show the mic / system-audio
                // usage descriptions for a plain CLI binary (no app bundle).
                .unsafeFlags([
                    "-Xlinker", "-sectcreate",
                    "-Xlinker", "__TEXT",
                    "-Xlinker", "__info_plist",
                    "-Xlinker", "Sources/capturecli/Info.plist",
                ])
            ]
        )
    ]
)
