import AVFoundation
import Darwin
import Foundation
import Speech

private struct Word: Encodable {
    let word: String
    let start: Double
    let end: Double
}

private struct Segment: Encodable {
    let text: String
    let start: Double
    let end: Double
    let words: [Word]
}

private struct SpeechError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

@main
struct AppleSpeech {
    static func main() async {
        let arguments = CommandLine.arguments
        if arguments.dropFirst().elementsEqual(["-version"]) {
            print("Editarr Apple Speech helper 1")
            return
        }
        do {
            guard arguments.count == 4 else {
                throw SpeechError(message: "Expected a locale, audio file and output file.")
            }
            guard #available(macOS 26.0, *) else {
                throw SpeechError(message: "Apple Speech requires macOS 26 or later.")
            }
            // A stopped scan or replay can terminate the Python parent during transcription.
            let parent = getppid()
            let parentExit = DispatchSource.makeProcessSource(
                identifier: parent, eventMask: .exit, queue: .global()
            )
            parentExit.setEventHandler { exit(EXIT_FAILURE) }
            parentExit.activate()
            defer { parentExit.cancel() }
            guard parent > 1, getppid() == parent else { exit(EXIT_FAILURE) }
            let segments = try await transcribe(
                URL(fileURLWithPath: arguments[2]), localeIdentifier: arguments[1]
            )
            try JSONEncoder().encode(segments).write(to: URL(fileURLWithPath: arguments[3]))
        } catch {
            FileHandle.standardError.write(Data("\(error.localizedDescription)\n".utf8))
            exit(EXIT_FAILURE)
        }
    }

    @available(macOS 26.0, *)
    private static func transcribe(_ input: URL, localeIdentifier: String) async throws -> [Segment] {
        let file = try AVAudioFile(forReading: input)
        guard file.length > 0 else { return [] }
        guard SpeechTranscriber.isAvailable else {
            throw SpeechError(message: "Apple Speech transcription is unavailable on this Mac.")
        }
        let requested = Locale(identifier: Locale.canonicalLanguageIdentifier(from: localeIdentifier))
        guard let locale = await SpeechTranscriber.supportedLocale(equivalentTo: requested) else {
            throw SpeechError(message: "Apple Speech does not support the locale \(localeIdentifier).")
        }
        let transcriber = SpeechTranscriber(
            locale: locale, transcriptionOptions: [], reportingOptions: [],
            attributeOptions: [.audioTimeRange]
        )
        if let request = try await AssetInventory.assetInstallationRequest(supporting: [transcriber]) {
            do {
                try await request.downloadAndInstall()
            } catch {
                throw SpeechError(
                    message: "Could not install the \(locale.identifier) Apple Speech assets: "
                        + error.localizedDescription
                )
            }
        }

        let analyzer = SpeechAnalyzer(modules: [transcriber])
        let results = Task { try await collectResults(from: transcriber) }
        defer { results.cancel() }
        do {
            guard let lastSample = try await analyzer.analyzeSequence(from: file) else {
                await analyzer.cancelAndFinishNow()
                return []
            }
            try await analyzer.finalizeAndFinish(through: lastSample)
            return try await results.value
        } catch {
            await analyzer.cancelAndFinishNow()
            throw error
        }
    }

    @available(macOS 26.0, *)
    private static func collectResults(from transcriber: SpeechTranscriber) async throws -> [Segment] {
        var segments: [Segment] = []
        for try await result in transcriber.results where result.isFinal {
            let text = String(result.text.characters).trimmingCharacters(in: .whitespacesAndNewlines)
            let start = result.range.start.seconds
            let end = result.range.end.seconds
            guard !text.isEmpty, start.isFinite, end.isFinite, end > start else { continue }
            let words = result.text.runs.compactMap { run -> Word? in
                guard let range = run.audioTimeRange else { return nil }
                let word = String(result.text[run.range].characters)
                let start = range.start.seconds
                let end = range.end.seconds
                guard !word.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                      start.isFinite, end.isFinite, end > start else { return nil }
                return Word(word: word, start: start, end: end)
            }
            segments.append(Segment(text: text, start: start, end: end, words: words))
        }
        return segments
    }
}
