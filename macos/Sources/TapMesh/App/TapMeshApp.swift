import SwiftUI
import AppKit
import TapMeshCore

@main
struct TapMeshApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var store = WorkspaceStore()
    @StateObject private var updater = AppUpdater.shared
    var body: some Scene {
        WindowGroup("TapMesh", id: "workspace") {
            ContentView(store: store)
                .frame(minWidth: 1060, minHeight: 680)
                .disabled(updater.isPreparingInstallation)
                .task {
                    updater.configure(activity: {
                        UpdateActivity(commandActive: store.busy, buildActive: store.buildRunning,
                                       recordingActive: store.recordingID != nil, recordingStopping: store.stoppingRecording,
                                       takeoverActive: store.takingControl, connecting: store.connecting)
                    }, afterRecovery: { await store.connect() },
                       preparationStateChanged: { store.setUpdatePreparation($0) })
                    await store.start()
                }
        }
        .defaultSize(width: 1360, height: 900)
        .commands {
            CommandGroup(replacing: .newItem) { }
            CommandGroup(after: .appInfo) {
                Button("Check for Updates…") { updater.checkForUpdates() }
                    .disabled(!updater.configured || !updater.canCheckForUpdates || !updater.activity.allowsInstallation || updater.isPreparingInstallation)
            }
            CommandMenu("Device") {
                Button("Refresh Devices") { Task { await store.refreshDevices() } }.keyboardShortcut("r", modifiers: [.command, .shift])
                Button("Capture Screen") { Task { await store.capture() } }.keyboardShortcut("r").disabled(!store.canControl)
                Button("Inspect Elements") { Task { await store.inspect() } }.keyboardShortcut("i", modifiers: [.command, .shift]).disabled(!store.canControl)
                Divider()
                Button("Home") { Task { await store.run("press_button", arguments: ["button": .string("home")]) } }.keyboardShortcut("h", modifiers: [.command, .shift]).disabled(!store.canControl)
            }
        }
        Settings { SettingsView(store: store).disabled(updater.isPreparingInstallation) }
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private var terminating = false
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard !terminating else { return .terminateLater }
        let updater = AppUpdater.shared
        if updater.requiresDeferringTermination { return .terminateCancel }
        if updater.hasPendingUpdate && !updater.activity.allowsInstallation {
            updater.showPausedUpdate()
            return .terminateCancel
        }
        terminating = true
        Task {
            if updater.hasPendingUpdate {
                do { try await updater.prepareForTermination() }
                catch {
                    terminating = false
                    sender.reply(toApplicationShouldTerminate: false)
                    updater.showPausedUpdate(error)
                    return
                }
            } else {
                await LocalRuntimeController.shared.stop()
            }
            sender.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }
}
