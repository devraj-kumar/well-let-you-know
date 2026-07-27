import AVFoundation
import AudioToolbox
import CoreAudio
import Foundation

// capturecli — dual-channel interview recorder.
//   mic.wav    : default input device  (the candidate)
//   system.wav : system audio output via a Core Audio process tap (the interviewer)
// Runs until SIGINT/SIGTERM (or --duration N seconds), then finalizes both files.

struct CaptureError: Error, CustomStringConvertible {
    let description: String
    init(_ description: String) { self.description = description }
}

func log(_ message: String) {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
}

func fail(_ message: String) -> Never {
    log("error: " + message)
    exit(1)
}

// MARK: - Microphone (candidate)

final class MicRecorder {
    private let engine = AVAudioEngine()
    private var file: AVAudioFile?

    func start(writingTo url: URL) throws {
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else {
            throw CaptureError("no usable microphone input device found")
        }
        let file = try AVAudioFile(
            forWriting: url, settings: format.settings,
            commonFormat: .pcmFormatFloat32, interleaved: format.isInterleaved)
        self.file = file
        input.installTap(onBus: 0, bufferSize: 4096, format: format) { buffer, _ in
            try? file.write(from: buffer)
        }
        try engine.start()
    }

    func stop() {
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        file = nil
    }
}

// MARK: - System audio via Core Audio process tap (interviewer)

final class SystemAudioRecorder {
    private var tapID = AudioObjectID(kAudioObjectUnknown)
    private var aggregateID = AudioObjectID(kAudioObjectUnknown)
    private var ioProcID: AudioDeviceIOProcID?
    private var file: AVAudioFile?
    private let queue = DispatchQueue(label: "coach.system-audio-io")

    func start(writingTo url: URL) throws {
        // Global tap: everything the system plays, excluding no processes.
        let tapDescription = CATapDescription(stereoGlobalTapButExcludeProcesses: [])
        tapDescription.uuid = UUID()
        tapDescription.name = "wlyk-system-tap"
        tapDescription.isPrivate = true
        tapDescription.muteBehavior = .unmuted

        var status = AudioHardwareCreateProcessTap(tapDescription, &tapID)
        guard status == noErr, tapID != kAudioObjectUnknown else {
            throw CaptureError(
                "could not create system-audio tap (OSStatus \(status)). "
                    + "Grant \"System Audio Recording\" to your terminal in "
                    + "System Settings → Privacy & Security → Screen & System Audio Recording, then retry.")
        }

        var address = AudioObjectPropertyAddress(
            mSelector: kAudioTapPropertyFormat,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain)
        var size = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
        var asbd = AudioStreamBasicDescription()
        status = AudioObjectGetPropertyData(tapID, &address, 0, nil, &size, &asbd)
        guard status == noErr else {
            throw CaptureError("could not read tap format (OSStatus \(status))")
        }
        guard let format = AVAudioFormat(streamDescription: &asbd) else {
            throw CaptureError("unsupported tap audio format")
        }

        let description: [String: Any] = [
            kAudioAggregateDeviceNameKey as String: "wlyk-aggregate",
            kAudioAggregateDeviceUIDKey as String: UUID().uuidString,
            kAudioAggregateDeviceIsPrivateKey as String: true,
            kAudioAggregateDeviceTapAutoStartKey as String: true,
            kAudioAggregateDeviceSubDeviceListKey as String: [[String: Any]](),
            kAudioAggregateDeviceTapListKey as String: [
                [
                    kAudioSubTapUIDKey as String: tapDescription.uuid.uuidString,
                    kAudioSubTapDriftCompensationKey as String: true,
                ]
            ],
        ]
        status = AudioHardwareCreateAggregateDevice(description as CFDictionary, &aggregateID)
        guard status == noErr, aggregateID != kAudioObjectUnknown else {
            throw CaptureError("could not create aggregate device (OSStatus \(status))")
        }

        let file = try AVAudioFile(
            forWriting: url, settings: format.settings,
            commonFormat: .pcmFormatFloat32, interleaved: format.isInterleaved)
        self.file = file

        status = AudioDeviceCreateIOProcIDWithBlock(&ioProcID, aggregateID, queue) {
            _, inInputData, _, _, _ in
            guard
                let buffer = AVAudioPCMBuffer(
                    pcmFormat: format, bufferListNoCopy: inInputData, deallocator: nil)
            else { return }
            try? file.write(from: buffer)
        }
        guard status == noErr, let ioProcID else {
            throw CaptureError("could not create IO proc (OSStatus \(status))")
        }
        status = AudioDeviceStart(aggregateID, ioProcID)
        guard status == noErr else {
            throw CaptureError("could not start aggregate device (OSStatus \(status))")
        }
    }

    func stop() {
        if let ioProcID, aggregateID != kAudioObjectUnknown {
            AudioDeviceStop(aggregateID, ioProcID)
            AudioDeviceDestroyIOProcID(aggregateID, ioProcID)
        }
        ioProcID = nil
        if aggregateID != kAudioObjectUnknown {
            AudioHardwareDestroyAggregateDevice(aggregateID)
            aggregateID = kAudioObjectUnknown
        }
        if tapID != kAudioObjectUnknown {
            AudioHardwareDestroyProcessTap(tapID)
            tapID = kAudioObjectUnknown
        }
        queue.sync {}  // drain in-flight writes before closing the file
        file = nil
    }
}

// MARK: - Permissions

func ensureMicrophoneAccess() {
    switch AVCaptureDevice.authorizationStatus(for: .audio) {
    case .authorized:
        return
    case .notDetermined:
        let semaphore = DispatchSemaphore(value: 0)
        var granted = false
        AVCaptureDevice.requestAccess(for: .audio) { ok in
            granted = ok
            semaphore.signal()
        }
        semaphore.wait()
        if !granted {
            fail("microphone access denied. Grant it in System Settings → Privacy & Security → Microphone.")
        }
    default:
        fail("microphone access denied. Grant it in System Settings → Privacy & Security → Microphone.")
    }
}

// There is no public API to request the System Audio Recording permission from a
// CLI tool — without it the tap silently records zeros. Use the TCC framework the
// same way AudioCap does: preflight, then request (shows the system prompt once).
func ensureSystemAudioCaptureAccess() {
    guard let handle = dlopen("/System/Library/PrivateFrameworks/TCC.framework/Versions/A/TCC", RTLD_NOW) else {
        log("warning: could not load TCC framework; if system.wav is silent, grant "
            + "System Audio Recording to your terminal in System Settings.")
        return
    }
    defer { dlclose(handle) }

    typealias PreflightFunc = @convention(c) (CFString, CFDictionary?) -> Int
    typealias RequestFunc = @convention(c) (CFString, CFDictionary?, @escaping (Bool) -> Void) -> Void
    guard
        let preflightSymbol = dlsym(handle, "TCCAccessPreflight"),
        let requestSymbol = dlsym(handle, "TCCAccessRequest")
    else { return }
    let preflight = unsafeBitCast(preflightSymbol, to: PreflightFunc.self)
    let request = unsafeBitCast(requestSymbol, to: RequestFunc.self)

    let service = "kTCCServiceAudioCapture" as CFString
    if preflight(service, nil) == 0 { return }  // already granted

    log("requesting System Audio Recording permission — check for a macOS prompt…")
    let semaphore = DispatchSemaphore(value: 0)
    var granted = false
    request(service, nil) { ok in
        granted = ok
        semaphore.signal()
    }
    semaphore.wait()
    if !granted {
        fail("System Audio Recording denied. Enable your terminal under System Settings "
            + "→ Privacy & Security → Screen & System Audio Recording, then retry.")
    }
}

// MARK: - Main

setvbuf(stdout, nil, _IONBF, 0)

var outputDir: String?
var duration: Double?
var arguments = Array(CommandLine.arguments.dropFirst())
while !arguments.isEmpty {
    let argument = arguments.removeFirst()
    switch argument {
    case "--duration":
        guard !arguments.isEmpty, let value = Double(arguments.removeFirst()) else {
            fail("--duration requires a number of seconds")
        }
        duration = value
    default:
        if outputDir == nil {
            outputDir = argument
        } else {
            fail("unexpected argument: \(argument)")
        }
    }
}
guard let outputDir else {
    fail("usage: capturecli <output-dir> [--duration seconds]")
}

let directory = URL(fileURLWithPath: outputDir, isDirectory: true)
try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
let micURL = directory.appendingPathComponent("mic.wav")
let systemURL = directory.appendingPathComponent("system.wav")

ensureMicrophoneAccess()
ensureSystemAudioCaptureAccess()

let mic = MicRecorder()
let system = SystemAudioRecorder()

do {
    try system.start(writingTo: systemURL)
    try mic.start(writingTo: micURL)
} catch {
    mic.stop()
    system.stop()
    fail("\(error)")
}

let startedAt = ISO8601DateFormatter().string(from: Date())
let sessionInfo: [String: Any] = ["started_at": startedAt]
if let data = try? JSONSerialization.data(withJSONObject: sessionInfo, options: [.prettyPrinted]) {
    try? data.write(to: directory.appendingPathComponent("capture.json"))
}

print("RECORDING")
log("recording… mic → mic.wav, system audio → system.wav (Ctrl-C to stop)")

var stopping = false
func stopAndExit() {
    if stopping { return }
    stopping = true
    log("stopping…")
    mic.stop()
    system.stop()
    log("saved \(micURL.path)")
    log("saved \(systemURL.path)")
    exit(0)
}

signal(SIGINT, SIG_IGN)
signal(SIGTERM, SIG_IGN)
let sigintSource = DispatchSource.makeSignalSource(signal: SIGINT, queue: .main)
sigintSource.setEventHandler { stopAndExit() }
sigintSource.resume()
let sigtermSource = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
sigtermSource.setEventHandler { stopAndExit() }
sigtermSource.resume()

if let duration {
    DispatchQueue.main.asyncAfter(deadline: .now() + duration) { stopAndExit() }
}

dispatchMain()
