import XCTest

final class SamwiseMacOSUITests: XCTestCase {
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
        path.click()
        path.typeText("/media/movie.mp4")
        XCTAssertTrue(addButton.isEnabled)
        app.checkBoxes["Audio scanning — Whisper"].click()
        app.checkBoxes["Video scanning — NudeNet"].click()
        XCTAssertFalse(addButton.isEnabled)
    }
}
