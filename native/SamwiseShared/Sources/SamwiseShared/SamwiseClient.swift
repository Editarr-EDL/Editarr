import Foundation

public enum SamwiseClientError: LocalizedError, Sendable {
    case invalidServerURL
    case invalidResponse
    case server(String)

    public var errorDescription: String? {
        switch self {
        case .invalidServerURL: "Enter a valid http or https Samwise server URL."
        case .invalidResponse: "The Samwise server returned an unexpected response."
        case .server(let message): message
        }
    }
}

public struct SamwiseClient: Sendable {
    public let serverURL: URL
    private let session: URLSession

    public init(serverURL: URL, session: URLSession = .shared) throws {
        guard let scheme = serverURL.scheme?.lowercased(), ["http", "https"].contains(scheme),
              serverURL.host != nil else { throw SamwiseClientError.invalidServerURL }
        self.serverURL = serverURL
        self.session = session
    }

    public func capabilities() async throws -> ScanCapabilities {
        try await get("api/capabilities")
    }

    public func videos() async throws -> [Video] {
        try await get("api/videos")
    }

    public func queue() async throws -> [ScanQueueItem] {
        try await get("api/scan-queue")
    }

    public func enqueue(videoPath: String, options: ScanOptions) async throws -> ScanQueueItem {
        let body = try JSONEncoder().encode(QueueScanRequest(videoPath: videoPath, options: options))
        return try await request("api/scan-queue", method: "POST", body: body)
    }

    public func findings(videoID: String) async throws -> [Finding] {
        try await get("api/findings?video_id=\(videoID)")
    }

    public func updateSelection(findingIDs: [Int], selected: Bool) async throws -> [Finding] {
        let body = try JSONEncoder().encode(BulkSelection(findingIDs: findingIDs, selected: selected))
        return try await request("api/findings/selection", method: "PATCH", body: body)
    }

    public func mediaURL(for video: Video) -> URL {
        serverURL.appending(path: "api/videos/\(video.id)/media")
    }

    public func exportEDL(videoID: String) async throws -> Data {
        guard let url = URL(string: "api/videos/\(videoID)/export", relativeTo: serverURL) else {
            throw SamwiseClientError.invalidServerURL
        }
        let (data, response) = try await session.data(from: url)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw SamwiseClientError.server("Unable to export EDL.")
        }
        return data
    }

    private func get<T: Decodable>(_ path: String) async throws -> T { try await request(path, method: "GET") }

    private func request<T: Decodable>(_ path: String, method: String, body: Data? = nil) async throws -> T {
        guard let url = URL(string: path, relativeTo: serverURL) else { throw SamwiseClientError.invalidServerURL }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.httpBody = body
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if body != nil { request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw SamwiseClientError.invalidResponse }
        guard (200..<300).contains(http.statusCode) else {
            throw SamwiseClientError.server(String(data: data, encoding: .utf8) ?? "Server error \(http.statusCode)")
        }
        return try JSONDecoder().decode(T.self, from: data)
    }
}

private struct BulkSelection: Encodable {
    let findingIDs: [Int]
    let selected: Bool

    enum CodingKeys: String, CodingKey {
        case findingIDs = "finding_ids"
        case selected
    }
}
