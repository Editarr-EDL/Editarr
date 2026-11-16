#if SWIFT_PACKAGE
import SamwiseUI
#endif
import SwiftUI

@main
struct SamwiseMacOSApp: App {
    var body: some Scene {
        WindowGroup {
            SamwiseRootView()
        }
    }
}
