import Foundation
import XCTest
@testable import SamwiseShared

final class ReviewPresentationTests: XCTestCase {
    func testGroupsAudioByCanonicalGroupKeyAndTracksSelectionState() throws {
        let items = try [
            finding(id: 1, source: "audio", label: "damn", groupKey: "damn", selected: true),
            finding(id: 2, source: "audio", label: "Damn!", groupKey: "damn", selected: false),
            finding(id: 3, source: "audio", label: "hell", groupKey: "hell", selected: true),
        ]
        let groups = ReviewPresentation.groups(for: items, source: "audio")

        XCTAssertEqual(groups.map(\.key), ["damn", "hell"])
        XCTAssertEqual(groups[0].findings.map(\.id), [1, 2])
        XCTAssertTrue(groups[0].partiallySelected)
        XCTAssertFalse(groups[0].allSelected)
        XCTAssertTrue(groups[1].allSelected)
    }

    func testTimelineSegmentsClampToVideoDurationAndExcludeDeselectedItems() throws {
        let items = try [
            finding(id: 1, source: "audio", start: -2, end: 15, selected: true, action: .mute),
            finding(id: 2, source: "video", start: 110, end: 150, selected: true, action: .skip),
            finding(id: 3, source: "audio", start: 30, end: 31, selected: false, action: .mute),
        ]
        let segments = ReviewPresentation.timelineSegments(findings: items, duration: 120)

        XCTAssertEqual(segments.count, 2)
        XCTAssertEqual(segments[0].startFraction, 0)
        XCTAssertEqual(segments[0].endFraction, 0.125)
        XCTAssertEqual(segments[1].startFraction, 110.0 / 120.0)
        XCTAssertEqual(segments[1].endFraction, 1)
        XCTAssertEqual(segments[1].editAction, .skip)
    }

    func testEDLExportAdapterUsesVideoStemAndWritesData() throws {
        let video = try decodedVideo(filename: "My.Movie.mkv")
        XCTAssertEqual(EDLExportAdapter.filename(for: video), "My.Movie.edl")

        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString, directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let url = try EDLExportAdapter.writeTemporary(data: Data("1 2 1\n".utf8), video: video, directory: directory)
        XCTAssertEqual(try Data(contentsOf: url), Data("1 2 1\n".utf8))
    }

    private func finding(id: Int, source: String, label: String = "item", groupKey: String? = nil, start: Double = 10, end: Double? = 11, selected: Bool, action: EditAction = .mute) throws -> Finding {
        let data = try JSONSerialization.data(withJSONObject: [
            "id": id, "video_id": "video", "category": source == "audio" ? "profanity" : "nudity",
            "start_seconds": start, "end_seconds": end as Any, "label": label,
            "source": source, "selected": selected, "edit_action": action.rawValue,
            "group_key": groupKey as Any,
        ])
        return try JSONDecoder().decode(Finding.self, from: data)
    }

    private func decodedVideo(filename: String) throws -> Video {
        let data = try JSONSerialization.data(withJSONObject: [
            "id": "video", "filename": filename, "filepath": "/media/\(filename)",
            "duration_seconds": 120, "status": "complete", "finding_count": 0,
        ])
        return try JSONDecoder().decode(Video.self, from: data)
    }
}
