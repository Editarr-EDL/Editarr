import AVKit
import Observation
import SamwiseShared
import SwiftUI
#if os(macOS)
import AppKit
#else
import UIKit
#endif

@MainActor
@Observable
final class SamwiseStore {
    var serverText = UserDefaults.standard.string(forKey: "samwise.serverURL") ?? "http://localhost:8000"
    var videos: [Video] = []
    var queue: [ScanQueueItem] = []
    var findings: [Finding] = []
    var selectedVideo: Video?
    var errorMessage: String?
    var isLoading = false

    func refresh() async {
        do {
            isLoading = true
            let client = try makeClient()
            async let loadedVideos = client.videos()
            async let loadedQueue = client.queue()
            videos = try await loadedVideos
            queue = try await loadedQueue
            if let selectedVideo, let refreshed = videos.first(where: { $0.id == selectedVideo.id }) {
                self.selectedVideo = refreshed
                findings = try await client.findings(videoID: refreshed.id)
            }
            UserDefaults.standard.set(serverText, forKey: "samwise.serverURL")
            errorMessage = nil
        } catch { errorMessage = error.localizedDescription }
        isLoading = false
    }

    func select(_ video: Video) async {
        selectedVideo = video
        do { findings = try await makeClient().findings(videoID: video.id) }
        catch { errorMessage = error.localizedDescription }
    }

    func setSelected(_ items: [Finding], selected: Bool) async {
        do {
            let updated = try await makeClient().updateSelection(findingIDs: items.map(\.id), selected: selected)
            let byID = Dictionary(uniqueKeysWithValues: updated.map { ($0.id, $0) })
            findings = findings.map { byID[$0.id] ?? $0 }
        } catch { errorMessage = error.localizedDescription }
    }

    func enqueue(path: String, audio: Bool, video: Bool, speedUp: Bool) async {
        guard audio || video else { errorMessage = "Select Audio scanning, Video scanning, or both."; return }
        do {
            _ = try await makeClient().enqueue(
                videoPath: path,
                options: ScanOptions(frameInterval: video ? 1 : nil, speedUp: video && speedUp, scanAudio: audio, audioModel: audio ? "whisper" : nil, scanVideo: video, videoModel: video ? "nudenet" : nil)
            )
            await refresh()
        } catch { errorMessage = error.localizedDescription }
    }

    func exportEDL(for video: Video) async -> Data? {
        do { return try await makeClient().exportEDL(videoID: video.id) }
        catch { errorMessage = error.localizedDescription; return nil }
    }

    func makeClient() throws -> SamwiseClient {
        guard let url = URL(string: serverText) else { throw SamwiseClientError.invalidServerURL }
        return try SamwiseClient(serverURL: url)
    }
}

public struct SamwiseRootView: View {
    @State private var store = SamwiseStore()
    @State private var path = ""
    @State private var scanAudio = true
    @State private var scanVideo = true
    @State private var speedUp = false

    public init() {}

    public var body: some View {
        NavigationSplitView {
            List(store.videos, selection: $store.selectedVideo) { video in
                Button(video.filename) { Task { await store.select(video) } }
            }
            .navigationTitle("Samwise")
        } detail: {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    SettingsCard(serverText: $store.serverText, refresh: { Task { await store.refresh() } })
                    ScanCard(path: $path, scanAudio: $scanAudio, scanVideo: $scanVideo, speedUp: $speedUp, enqueue: { Task { await store.enqueue(path: path, audio: scanAudio, video: scanVideo, speedUp: speedUp) } })
                    if let video = store.selectedVideo {
                        ReviewView(store: store, video: video)
                    } else {
                        ContentUnavailableView("Choose a video", systemImage: "film", description: Text("The shared queue and completed scans appear here."))
                    }
                }.padding()
            }
            .alert("Samwise", isPresented: .constant(store.errorMessage != nil), actions: { Button("OK") { store.errorMessage = nil } }, message: { Text(store.errorMessage ?? "") })
        }
        .task { await store.refresh() }
    }
}

private struct SettingsCard: View {
    @Binding var serverText: String
    let refresh: () -> Void
    var body: some View {
        GroupBox("Samwise server") { HStack { TextField("http://server:8000", text: $serverText).textContentType(.URL); Button("Connect", action: refresh) } }
    }
}

private struct ScanCard: View {
    @Binding var path: String
    @Binding var scanAudio: Bool
    @Binding var scanVideo: Bool
    @Binding var speedUp: Bool
    let enqueue: () -> Void
    var body: some View {
        GroupBox("Add a scan") {
            VStack(alignment: .leading) {
                TextField("Absolute path on the server", text: $path)
                Toggle("Audio scanning — Whisper", isOn: $scanAudio)
                Toggle("Video scanning — NudeNet", isOn: $scanVideo)
                Toggle("Speed-up mode", isOn: $speedUp).disabled(!scanVideo)
                Button("Add Scan To Queue", action: enqueue).disabled(path.isEmpty || (!scanAudio && !scanVideo))
            }
        }
    }
}

private struct ReviewView: View {
    @Bindable var store: SamwiseStore
    let video: Video
    @State private var player = AVPlayer()
    #if os(iOS)
    @State private var shareItem: ShareItem?
    #endif
    var selected: [Finding] { store.findings.filter(\.selected) }
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack { H2("Review: \(video.filename)"); Spacer(); exportButton }
            VideoPlayer(player: player).frame(minHeight: 240).onAppear { player.replaceCurrentItem(with: AVPlayerItem(url: store.makeMediaURL(video))) }
            EDLTimeline(duration: video.durationSeconds ?? 0, findings: selected)
            FindingGroup(title: "Audio", findings: store.findings.filter { $0.source == "audio" }, player: player, update: { items, value in Task { await store.setSelected(items, selected: value) } })
            FindingGroup(title: "Video content", findings: store.findings.filter { $0.source != "audio" }, player: player, update: { items, value in Task { await store.setSelected(items, selected: value) } })
        }
        #if os(iOS)
        .sheet(item: $shareItem) { item in ShareSheet(items: [item.url]) }
        #endif
    }

    @ViewBuilder private var exportButton: some View {
        Button("Export EDL") {
            Task {
                guard let data = await store.exportEDL(for: video) else { return }
                #if os(macOS)
                presentMacSavePanel(data: data, video: video)
                #else
                shareItem = ShareItem(url: writeTemporaryEDL(data: data, video: video))
                #endif
            }
        }
    }
}

private extension SamwiseStore {
    func makeMediaURL(_ video: Video) -> URL {
        guard let client = try? makeClient() else { return URL(string: "about:blank")! }
        return client.mediaURL(for: video)
    }
}

private struct H2: View { let text: String; init(_ text: String) { self.text = text }; var body: some View { Text(text).font(.title2.bold()) } }

private struct EDLTimeline: View {
    let duration: Double
    let findings: [Finding]
    var body: some View { GeometryReader { geometry in ZStack(alignment: .leading) { Capsule().fill(.blue); ForEach(ReviewPresentation.timelineSegments(findings: findings, duration: duration)) { segment in Rectangle().fill(segment.editAction == .mute ? .white : .black).frame(width: max(2, geometry.size.width * (segment.endFraction - segment.startFraction))).offset(x: geometry.size.width * segment.startFraction) } } }.frame(height: 24) }
}

private struct FindingGroup: View {
    let title: String
    let findings: [Finding]
    let player: AVPlayer
    let update: ([Finding], Bool) -> Void
    var body: some View {
        GroupBox(title) {
            VStack(alignment: .leading) {
                Toggle("Select all", isOn: Binding(
                    get: { findings.allSatisfy(\.selected) },
                    set: { update(findings, $0) }
                ))
                Text("\(findings.count) individual findings")
                    .foregroundStyle(.secondary)
                if let first = findings.first {
                    Button("Play first occurrence") {
                        player.seek(to: CMTime(seconds: first.startSeconds, preferredTimescale: 600))
                        player.play()
                    }
                }
                ForEach(findings, id: \Finding.id) { (finding: Finding) in
                    FindingRow(finding: finding, player: player) { selected in
                        update([finding], selected)
                    }
                }
            }
        }
    }
}

@MainActor
private struct FindingRow: View {
    let finding: Finding
    let player: AVPlayer
    let update: (Bool) -> Void

    var body: some View {
        HStack {
            Button {
                player.seek(to: CMTime(seconds: finding.startSeconds, preferredTimescale: 600))
                player.play()
            } label: { Image(systemName: "play.fill") }
            Toggle(finding.label ?? finding.category, isOn: Binding(
                get: { finding.selected }, set: { selected in update(selected) }
            ))
            Spacer()
            Text(finding.editAction.rawValue.uppercased())
                .font(.caption.weight(.semibold))
                .foregroundStyle(finding.editAction == .mute ? Color.secondary : Color.red)
        }
    }
}

#if os(macOS)
@MainActor
private func presentMacSavePanel(data: Data, video: Video) {
    let panel = NSSavePanel()
    panel.nameFieldStringValue = EDLExportAdapter.filename(for: video)
    let videoURL = URL(fileURLWithPath: video.filepath)
    if FileManager.default.fileExists(atPath: videoURL.path) {
        panel.directoryURL = videoURL.deletingLastPathComponent()
    }
    panel.begin { response in
        guard response == .OK, let destination = panel.url else { return }
        try? data.write(to: destination, options: .atomic)
    }
}
#else
private struct ShareItem: Identifiable {
    let id = UUID()
    let url: URL
}

private func writeTemporaryEDL(data: Data, video: Video) -> URL {
    (try? EDLExportAdapter.writeTemporary(data: data, video: video))
        ?? FileManager.default.temporaryDirectory.appending(path: EDLExportAdapter.filename(for: video))
}

private struct ShareSheet: UIViewControllerRepresentable {
    let items: [Any]
    func makeUIViewController(context: Context) -> UIActivityViewController { UIActivityViewController(activityItems: items, applicationActivities: nil) }
    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}
#endif
