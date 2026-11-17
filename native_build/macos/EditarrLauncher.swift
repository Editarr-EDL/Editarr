import AppKit
import Foundation
import ServiceManagement

private struct RuntimeResult {
    let status: Int32
    let stdout: String
}

private struct RuntimeStatus: Decodable {
    let running: Bool
    let show_in_dock: Bool?
}

private final class EditarrAppDelegate: NSObject, NSApplicationDelegate {
    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let statusMenuItem = NSMenuItem(title: "Status: Starting…", action: nil, keyEquivalent: "")
    private let startStopMenuItem = NSMenuItem(title: "Stop Editarr", action: #selector(toggleServer), keyEquivalent: "")
    private let loginMenuItem = NSMenuItem(title: "Launch at Login", action: #selector(toggleLaunchAtLogin), keyEquivalent: "")
    private var backendProcess: Process?
    private var statusTimer: Timer?
    private var refreshingStatus = false
    private var isRunning = false
    private var quitting = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        configureMenu()
        refreshLoginItem()
        startBackend(openBrowser: true)
        statusTimer = Timer.scheduledTimer(
            timeInterval: 3,
            target: self,
            selector: #selector(refreshStatus),
            userInfo: nil,
            repeats: true
        )
        refreshStatus()
    }

    func applicationShouldHandleReopen(
        _ sender: NSApplication,
        hasVisibleWindows flag: Bool
    ) -> Bool {
        openEditarr()
        return false
    }

    func applicationWillTerminate(_ notification: Notification) {
        statusTimer?.invalidate()
        if !quitting {
            _ = runRuntimeSynchronously(arguments: ["lifecycle", "quit"])
        }
    }

    private func configureMenu() {
        if let button = statusItem.button {
            button.image = NSImage(systemSymbolName: "film.stack", accessibilityDescription: "Editarr")
            button.toolTip = "Editarr"
        }
        statusMenuItem.isEnabled = false

        let menu = NSMenu()
        let openItem = NSMenuItem(title: "Open Editarr", action: #selector(openEditarr), keyEquivalent: "o")
        openItem.target = self
        let logsItem = NSMenuItem(title: "Open Logs", action: #selector(openLogs), keyEquivalent: "l")
        logsItem.target = self
        startStopMenuItem.target = self
        loginMenuItem.target = self
        let quitItem = NSMenuItem(title: "Quit Editarr", action: #selector(quitEditarr), keyEquivalent: "q")
        quitItem.target = self

        menu.addItem(statusMenuItem)
        menu.addItem(.separator())
        menu.addItem(openItem)
        menu.addItem(logsItem)
        menu.addItem(startStopMenuItem)
        menu.addItem(.separator())
        menu.addItem(loginMenuItem)
        menu.addItem(.separator())
        menu.addItem(quitItem)
        statusItem.menu = menu
    }

    private var runtimeExecutable: URL {
        Bundle.main.resourceURL!
            .appendingPathComponent("runtime", isDirectory: true)
            .appendingPathComponent("Editarr", isDirectory: true)
            .appendingPathComponent("Editarr", isDirectory: false)
    }

    private func configuredProcess(arguments: [String]) -> Process {
        let process = Process()
        process.executableURL = runtimeExecutable
        process.arguments = arguments
        var environment = ProcessInfo.processInfo.environment
        environment["EDITARR_MACOS_APP"] = "1"
        process.environment = environment
        process.currentDirectoryURL = FileManager.default.temporaryDirectory
        return process
    }

    private func startBackend(openBrowser: Bool) {
        if backendProcess?.isRunning == true { return }
        let process = configuredProcess(arguments: openBrowser ? [] : ["--no-browser"])
        process.terminationHandler = { [weak self, weak process] _ in
            DispatchQueue.main.async {
                if self?.backendProcess === process { self?.backendProcess = nil }
                self?.refreshStatus()
            }
        }
        do {
            try process.run()
            backendProcess = process
            statusMenuItem.title = "Status: Starting…"
        } catch {
            statusMenuItem.title = "Status: Failed to Start"
            NSLog("Editarr launcher could not start its runtime: %@", error.localizedDescription)
        }
    }

    private func runRuntimeSynchronously(arguments: [String]) -> RuntimeResult {
        let process = configuredProcess(arguments: arguments)
        let output = Pipe()
        process.standardOutput = output
        process.standardError = Pipe()
        do {
            try process.run()
            process.waitUntilExit()
            let data = output.fileHandleForReading.readDataToEndOfFile()
            return RuntimeResult(
                status: process.terminationStatus,
                stdout: String(data: data, encoding: .utf8) ?? ""
            )
        } catch {
            NSLog("Editarr lifecycle action failed: %@", error.localizedDescription)
            return RuntimeResult(status: 127, stdout: "")
        }
    }

    private func runLifecycle(_ action: String, completion: @escaping (RuntimeResult) -> Void) {
        DispatchQueue.global(qos: .utility).async { [weak self] in
            let result = self?.runRuntimeSynchronously(arguments: ["lifecycle", action])
                ?? RuntimeResult(status: 127, stdout: "")
            DispatchQueue.main.async { completion(result) }
        }
    }

    @objc private func refreshStatus() {
        guard !refreshingStatus else { return }
        refreshingStatus = true
        runLifecycle("status") { [weak self] result in
            guard let self else { return }
            self.refreshingStatus = false
            let status = try? JSONDecoder().decode(RuntimeStatus.self, from: Data(result.stdout.utf8))
            self.isRunning = result.status == 0 && status?.running == true
            if let showInDock = status?.show_in_dock {
                let policy: NSApplication.ActivationPolicy = showInDock ? .regular : .accessory
                if NSApp.activationPolicy() != policy {
                    NSApp.setActivationPolicy(policy)
                }
            }
            self.statusMenuItem.title = self.isRunning ? "Status: Running" : "Status: Stopped"
            self.startStopMenuItem.title = self.isRunning ? "Stop Editarr" : "Start Editarr"
        }
    }

    @objc private func openEditarr() {
        runLifecycle("open-ui") { [weak self] result in
            guard let self, result.status == 1, !self.isRunning else { return }
            self.startBackend(openBrowser: true)
        }
    }

    @objc private func openLogs() {
        runLifecycle("open-logs") { _ in }
    }

    @objc private func toggleServer() {
        if isRunning {
            runLifecycle("quit") { [weak self] _ in self?.refreshStatus() }
        } else {
            startBackend(openBrowser: true)
        }
    }

    private func refreshLoginItem() {
        if #available(macOS 13.0, *) {
            let enabledStatuses: [SMAppService.Status] = [.enabled, .requiresApproval]
            loginMenuItem.state = enabledStatuses.contains(SMAppService.mainApp.status) ? .on : .off
        }
    }

    @objc private func toggleLaunchAtLogin() {
        guard #available(macOS 13.0, *) else { return }
        do {
            if SMAppService.mainApp.status == .enabled || SMAppService.mainApp.status == .requiresApproval {
                try SMAppService.mainApp.unregister()
            } else {
                try SMAppService.mainApp.register()
            }
        } catch {
            NSLog("Editarr could not update Launch at Login: %@", error.localizedDescription)
        }
        refreshLoginItem()
    }

    @objc private func quitEditarr() {
        quitting = true
        runLifecycle("quit") { _ in NSApp.terminate(nil) }
    }
}

private func runCommandLinePassThrough(_ arguments: [String]) -> Never {
    let runtime = Bundle.main.resourceURL!
        .appendingPathComponent("runtime", isDirectory: true)
        .appendingPathComponent("Editarr", isDirectory: true)
        .appendingPathComponent("Editarr", isDirectory: false)
    let process = Process()
    process.executableURL = runtime
    process.arguments = arguments
    process.currentDirectoryURL = FileManager.default.temporaryDirectory
    process.standardInput = FileHandle.standardInput
    process.standardOutput = FileHandle.standardOutput
    process.standardError = FileHandle.standardError
    do {
        try process.run()
        process.waitUntilExit()
        exit(process.terminationStatus)
    } catch {
        fputs("Editarr could not start its embedded runtime.\n", stderr)
        exit(127)
    }
}

let forwardedArguments = CommandLine.arguments.dropFirst().filter { !$0.hasPrefix("-psn_") }
if !forwardedArguments.isEmpty {
    runCommandLinePassThrough(Array(forwardedArguments))
}

let application = NSApplication.shared
private let delegate = EditarrAppDelegate()
application.delegate = delegate
application.run()
