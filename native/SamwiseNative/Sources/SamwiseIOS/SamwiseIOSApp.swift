#if SWIFT_PACKAGE
import SamwiseUI
#endif
import SwiftUI

@main
struct SamwiseIOSApp: App {
    var body: some Scene {
        WindowGroup {
            SamwiseRootView()
        }
    }
}
