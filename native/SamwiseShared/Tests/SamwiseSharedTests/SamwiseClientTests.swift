import Foundation
import XCTest
@testable import SamwiseShared

final class SamwiseClientTests: XCTestCase {
    func testRejectsUnsupportedServerScheme() {
        XCTAssertThrowsError(try SamwiseClient(serverURL: URL(string: "ftp://samwise.example")!))
    }

    func testAcceptsHTTPAndHTTPS() throws {
        _ = try SamwiseClient(serverURL: URL(string: "http://localhost:8000")!)
        _ = try SamwiseClient(serverURL: URL(string: "https://samwise.example")!)
    }
}
