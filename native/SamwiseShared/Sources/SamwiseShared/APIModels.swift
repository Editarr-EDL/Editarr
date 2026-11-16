import Foundation

public enum EditAction: String, Codable, Sendable {
    case mute
    case skip
}

public struct Video: Codable, Identifiable, Hashable, Sendable {
    public let id: String
    public let filename: String
    public let filepath: String
    public let durationSeconds: Double?
    public let status: String
    public let findingCount: Int

    enum CodingKeys: String, CodingKey {
        case id, filename, filepath, status
        case durationSeconds = "duration_seconds"
        case findingCount = "finding_count"
    }
}

public struct Finding: Codable, Identifiable, Hashable, Sendable {
    public let id: Int
    public let videoID: String
    public let category: String
    public let startSeconds: Double
    public let endSeconds: Double?
    public let label: String?
    public let source: String
    public let selected: Bool
    public let editAction: EditAction
    public let groupKey: String?

    enum CodingKeys: String, CodingKey {
        case id, category, label, source, selected
        case videoID = "video_id"
        case startSeconds = "start_seconds"
        case endSeconds = "end_seconds"
        case editAction = "edit_action"
        case groupKey = "group_key"
    }
}

public struct ScanModelCapability: Codable, Hashable, Sendable, Identifiable {
    public let id: String
    public let displayName: String

    enum CodingKeys: String, CodingKey { case id; case displayName = "display_name" }
}

public struct ScanCapabilities: Codable, Sendable {
    public let audioModels: [ScanModelCapability]
    public let videoModels: [ScanModelCapability]

    enum CodingKeys: String, CodingKey {
        case audioModels = "audio_models"
        case videoModels = "video_models"
    }
}

public struct ScanOptions: Codable, Sendable {
    public var frameInterval: Double?
    public var speedUp: Bool
    public var scanAudio: Bool
    public var audioModel: String?
    public var scanVideo: Bool
    public var videoModel: String?

    public init(
        frameInterval: Double?,
        speedUp: Bool,
        scanAudio: Bool,
        audioModel: String?,
        scanVideo: Bool,
        videoModel: String?
    ) {
        self.frameInterval = frameInterval
        self.speedUp = speedUp
        self.scanAudio = scanAudio
        self.audioModel = audioModel
        self.scanVideo = scanVideo
        self.videoModel = videoModel
    }

    enum CodingKeys: String, CodingKey {
        case frameInterval = "frame_interval"
        case speedUp = "speed_up"
        case scanAudio = "scan_audio"
        case audioModel = "audio_model"
        case scanVideo = "scan_video"
        case videoModel = "video_model"
    }
}

public struct ScanQueueItem: Codable, Identifiable, Hashable, Sendable {
    public let id: Int
    public let videoPath: String
    public let status: String
    public let videoID: String?

    enum CodingKeys: String, CodingKey {
        case id, status
        case videoPath = "video_path"
        case videoID = "video_id"
    }
}

public struct QueueScanRequest: Codable, Sendable {
    public let videoPath: String
    public let frameInterval: Double?
    public let speedUp: Bool
    public let scanAudio: Bool
    public let audioModel: String?
    public let scanVideo: Bool
    public let videoModel: String?

    public init(videoPath: String, options: ScanOptions) {
        self.videoPath = videoPath
        self.frameInterval = options.frameInterval
        self.speedUp = options.speedUp
        self.scanAudio = options.scanAudio
        self.audioModel = options.audioModel
        self.scanVideo = options.scanVideo
        self.videoModel = options.videoModel
    }

    enum CodingKeys: String, CodingKey {
        case videoPath = "video_path"
        case frameInterval = "frame_interval"
        case speedUp = "speed_up"
        case scanAudio = "scan_audio"
        case audioModel = "audio_model"
        case scanVideo = "scan_video"
        case videoModel = "video_model"
    }
}
