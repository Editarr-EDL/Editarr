import Foundation

public struct FindingGroup: Identifiable, Hashable, Sendable {
    public let key: String
    public let findings: [Finding]

    public var id: String { key }
    public var allSelected: Bool { !findings.isEmpty && findings.allSatisfy(\.selected) }
    public var partiallySelected: Bool { findings.contains(where: \.selected) && !allSelected }
}

public struct TimelineSegment: Identifiable, Hashable, Sendable {
    public let id: Int
    public let startFraction: Double
    public let endFraction: Double
    public let editAction: EditAction
}

public enum ReviewPresentation {
    public static func groups(for findings: [Finding], source: String) -> [FindingGroup] {
        let matching = findings.filter { $0.source == source }
        let grouped = Dictionary(grouping: matching) { finding in
            if source == "audio" {
                return finding.groupKey ?? normalizedProfanityKey(finding.label)
            }
            return finding.category
        }
        return grouped.map { key, items in
            FindingGroup(key: key, findings: items.sorted { $0.startSeconds < $1.startSeconds })
        }.sorted { $0.key.localizedCaseInsensitiveCompare($1.key) == .orderedAscending }
    }

    public static func timelineSegments(findings: [Finding], duration: Double) -> [TimelineSegment] {
        guard duration > 0 else { return [] }
        return findings.filter(\.selected).map { finding in
            let start = min(1, max(0, finding.startSeconds / duration))
            let end = min(1, max(start, (finding.endSeconds ?? finding.startSeconds + 1) / duration))
            return TimelineSegment(id: finding.id, startFraction: start, endFraction: end, editAction: finding.editAction)
        }
    }

    private static func normalizedProfanityKey(_ label: String?) -> String {
        let value = label?.lowercased().filter(\.isLetter) ?? ""
        return value.isEmpty ? "unidentified-profanity" : value
    }
}

public enum EDLExportAdapter {
    public static func filename(for video: Video) -> String {
        URL(fileURLWithPath: video.filename).deletingPathExtension().lastPathComponent + ".edl"
    }

    public static func writeTemporary(data: Data, video: Video, directory: URL = FileManager.default.temporaryDirectory) throws -> URL {
        let url = directory.appending(path: filename(for: video))
        try data.write(to: url, options: .atomic)
        return url
    }
}
