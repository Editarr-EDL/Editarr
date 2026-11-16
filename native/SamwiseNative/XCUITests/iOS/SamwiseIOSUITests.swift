import XCTest

final class SamwiseIOSUITests: XCTestCase {
    func testSettingsAndScanControlsAppear() {
        let app = XCUIApplication()
        app.launch()

        XCTAssertTrue(app.textFields["http://server:8000"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.textFields["Absolute path on the server"].exists)
        XCTAssertTrue(app.buttons["Add Scan To Queue"].exists)
    }

    func testScanValidationRequiresAPathAndScanner() {
        let app = XCUIApplication()
        app.launch()

        let addButton = app.buttons["Add Scan To Queue"]
        XCTAssertTrue(addButton.waitForExistence(timeout: 5))
        XCTAssertFalse(addButton.isEnabled)
        let path = app.textFields["Absolute path on the server"]
        path.tap()
        path.typeText("/media/movie.mp4")
        XCTAssertTrue(addButton.isEnabled)
        app.switches["Audio scanning — Whisper"].tap()
        app.switches["Video scanning — NudeNet"].tap()
        XCTAssertFalse(addButton.isEnabled)
    }
}
