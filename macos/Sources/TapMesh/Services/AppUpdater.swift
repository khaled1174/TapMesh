import AppKit
import Combine
import Sparkle
import TapMeshCore

/// Sparkle owns download/signature verification; TapMesh owns the transition
/// from live device work to a stopped, replaceable application runtime.
@MainActor
final class AppUpdater: NSObject, ObservableObject, SPUUpdaterDelegate {
    static let shared = AppUpdater()
    @Published private(set) var configured = false
    @Published private(set) var canCheckForUpdates = false
    @Published private(set) var isPreparingInstallation = false {
        didSet { preparationStateChanged(isPreparingInstallation) }
    }
    private(set) var hasPendingUpdate = false
    private var preparationComplete = false
    private var controller: SPUStandardUpdaterController?
    private var checkObservation: AnyCancellable?
    private var installationTask: Task<Void, Never>?
    private var runtimePreparation: Task<Void, any Error>?
    private var installationHandler: (() -> Void)?
    private var activityProvider: @MainActor () -> UpdateActivity = { UpdateActivity(connecting: true) }
    private var afterRecovery: @MainActor () async -> Void = { }
    private var preparationStateChanged: @MainActor (Bool) -> Void = { _ in }

    var activity: UpdateActivity { activityProvider() }
    var requiresDeferringTermination: Bool { isPreparingInstallation && !preparationComplete }

    func configure(activity: @escaping @MainActor () -> UpdateActivity,
                   afterRecovery: @escaping @MainActor () async -> Void,
                   preparationStateChanged: @escaping @MainActor (Bool) -> Void = { _ in }) {
        activityProvider = activity
        self.afterRecovery = afterRecovery
        self.preparationStateChanged = preparationStateChanged
        preparationStateChanged(isPreparingInstallation)
        guard controller == nil,
              UpdateConfiguration.isValid(feedURL: Bundle.main.object(forInfoDictionaryKey: "SUFeedURL") as? String,
                                           publicKey: Bundle.main.object(forInfoDictionaryKey: "SUPublicEDKey") as? String,
                                           allowLocalStaging: Bundle.main.object(forInfoDictionaryKey: "TapMeshUpdateStagingMode") as? Bool == true)
        else { return }
        let created = SPUStandardUpdaterController(startingUpdater: false, updaterDelegate: self, userDriverDelegate: nil)
        controller = created
        configured = true
        checkObservation = created.updater.publisher(for: \.canCheckForUpdates)
            .receive(on: RunLoop.main)
            .sink { [weak self] value in self?.canCheckForUpdates = value }
        created.startUpdater()
    }

    func checkForUpdates() {
        guard configured, canCheckForUpdates, activity.allowsInstallation, !isPreparingInstallation else { return }
        controller?.checkForUpdates(nil)
    }

    func updater(_ updater: SPUUpdater, mayPerform updateCheck: SPUUpdateCheck) throws {
        guard activity.allowsInstallation, !isPreparingInstallation else { throw LocalRuntimeError.updateWorkActive }
    }

    func updater(_ updater: SPUUpdater, shouldProceedWithUpdate updateItem: SUAppcastItem, updateCheck: SPUUpdateCheck) throws {
        guard activity.allowsInstallation, !isPreparingInstallation else { throw LocalRuntimeError.updateWorkActive }
    }

    func updater(_ updater: SPUUpdater, willInstallUpdateOnQuit item: SUAppcastItem,
                 immediateInstallationBlock immediateInstallHandler: @escaping () -> Void) -> Bool {
        hasPendingUpdate = true
        // Preserve Sparkle's normal scheduling and user choice. Quit is guarded
        // separately even when Sparkle omits its relaunch-deferral callback.
        return false
    }

    func updater(_ updater: SPUUpdater, willInstallUpdate item: SUAppcastItem) {
        hasPendingUpdate = true
    }

    func updater(_ updater: SPUUpdater, shouldPostponeRelaunchForUpdate item: SUAppcastItem,
                 untilInvokingBlock installHandler: @escaping () -> Void) -> Bool {
        hasPendingUpdate = true
        installationHandler = installHandler
        beginInstallationWhenIdle()
        return true
    }

    private func beginInstallationWhenIdle() {
        guard installationTask == nil else { return }
        installationTask = Task { [weak self] in
            guard let self else { return }
            defer { self.installationTask = nil }
            while !Task.isCancelled {
                guard self.activity.allowsInstallation else {
                    do { try await Task.sleep(for: .milliseconds(500)) } catch { return }
                    continue
                }
                self.isPreparingInstallation = true
                do {
                    // This is the last pre-relaunch hook: no drain lease is held
                    // during feed checks, downloads or signature verification.
                    try await prepareRuntime()
                    guard !Task.isCancelled else { return }
                    self.preparationComplete = true
                    let handler = self.installationHandler
                    self.installationHandler = nil
                    handler?()
                    return
                } catch LocalRuntimeError.updateWorkActive {
                    self.isPreparingInstallation = false
                    do { try await Task.sleep(for: .seconds(1)) } catch { return }
                } catch {
                    await self.restoreAfterFailedPreparation()
                    self.showPausedUpdate(error)
                    return
                }
            }
        }
    }

    func prepareForTermination() async throws {
        guard hasPendingUpdate, !preparationComplete else { return }
        guard activity.allowsInstallation else { throw LocalRuntimeError.updateWorkActive }
        isPreparingInstallation = true
        do {
            try await prepareRuntime()
            preparationComplete = true
        } catch {
            await restoreAfterFailedPreparation()
            throw error
        }
    }

    func updater(_ updater: SPUUpdater, didAbortWithError error: any Error) {
        let task = installationTask
        let preparation = runtimePreparation
        task?.cancel()
        installationHandler = nil
        hasPendingUpdate = false
        guard isPreparingInstallation || preparationComplete else { return }
        Task { [weak self] in
            await task?.value
            _ = try? await preparation?.value
            await self?.restoreAfterFailedPreparation()
        }
    }

    private func prepareRuntime() async throws {
        if let runtimePreparation { try await runtimePreparation.value; return }
        // A cancelled Sparkle cycle must not cancel the supervisor's graceful
        // shutdown halfway through. The abort handler waits for this task before
        // reopening services and reconnecting the native client.
        let task = Task { try await LocalRuntimeController.shared.prepareForUpdate() }
        runtimePreparation = task
        defer { runtimePreparation = nil }
        try await task.value
    }

    private func restoreAfterFailedPreparation() async {
        await LocalRuntimeController.shared.resumeAfterFailedUpdate()
        preparationComplete = false
        isPreparingInstallation = false
        await afterRecovery()
    }

    func showPausedUpdate(_ error: (any Error)? = nil) {
        let alert = NSAlert()
        alert.messageText = "TapMesh update paused"
        alert.informativeText = "Finish the current recording, build, or device action, then try updating again. Keep TapMesh open while that work completes."
        if let local = error as? LocalRuntimeError, let message = local.errorDescription,
           message.utf8.count <= 512, message.unicodeScalars.allSatisfy({ !CharacterSet.controlCharacters.contains($0) }) {
            alert.informativeText = message
        }
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }
}
