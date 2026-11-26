import AppKit
import Combine
import TapMeshCore
import UserNotifications

/// Tells the person about waiting approvals when they are not looking at TapMesh:
/// a system notification for new requests and the pending count on the Dock icon.
///
/// Notifications only say which action on which device is waiting; approving always happens
/// in the Approvals page, where the person can read what the action will do. There are no
/// Allow or Deny buttons on the notification.
@MainActor
final class ApprovalNotifier: NSObject, ObservableObject, UNUserNotificationCenterDelegate {
    enum Permission: Equatable { case unknown, notAsked, allowed, denied, unavailable }

    /// UserDefaults key of the "Notify me when a request needs approval" setting (default on).
    static let enabledKey = "approvals.notify"

    /// The system permission as last checked. Checking never asks for permission.
    @Published private(set) var permission: Permission = .unknown
    /// Called on the main actor when the person clicks a TapMesh notification.
    var openApprovals: @MainActor () -> Void = { }

    private var tracker = ApprovalAlertTracker(identifierPrefix: "tapmesh-approval-\(UUID().uuidString)")
    private var centerPrepared = false

    /// `UNUserNotificationCenter.current()` traps when the process is not a real app bundle
    /// (`swift run`, tests). Then TapMesh only updates the Dock badge.
    static var notificationsAvailable: Bool {
        Bundle.main.bundleURL.pathExtension == "app" && Bundle.main.bundleIdentifier != nil
    }

    private var enabled: Bool { UserDefaults.standard.object(forKey: Self.enabledKey) as? Bool ?? true }

    /// The center, set up on first use: never touched at launch, and only when it is safe to use.
    private var center: UNUserNotificationCenter? {
        guard Self.notificationsAvailable else { return nil }
        let center = UNUserNotificationCenter.current()
        if !centerPrepared {
            centerPrepared = true
            center.delegate = self
            // Requests from an earlier launch no longer exist; clear what may still be shown.
            center.removeAllDeliveredNotifications()
        }
        return center
    }

    /// Call after every successful read of the pending approvals.
    func update(pending: [PendingApprovalSummary], lookingAtApp: Bool) {
        let plan = tracker.update(pending: pending, lookingAtApp: lookingAtApp, notificationsEnabled: enabled)
        NSApp.dockTile.badgeLabel = plan.badgeCount > 0 ? "\(plan.badgeCount)" : nil
        if !plan.withdrawnIdentifiers.isEmpty { center?.removeDeliveredNotifications(withIdentifiers: plan.withdrawnIdentifiers) }
        if let alert = plan.alert { deliver(alert) }
    }

    private func deliver(_ alert: ApprovalAlert) {
        guard let center else { return }
        let content = UNMutableNotificationContent()
        content.title = alert.title
        content.body = alert.body
        content.sound = .default
        let request = UNNotificationRequest(identifier: alert.identifier, content: content, trigger: nil)
        Task {
            // Permission is asked for here, the first time a request actually needs a notification.
            guard await self.authorize(center) else { return }
            try? await center.add(request)
        }
    }

    private func authorizationStatus(from center: UNUserNotificationCenter) async -> Int {
        await withCheckedContinuation { continuation in
            center.getNotificationSettings { @Sendable settings in
                continuation.resume(returning: settings.authorizationStatus.rawValue)
            }
        }
    }

    private func authorize(_ center: UNUserNotificationCenter) async -> Bool {
        switch await authorizationStatus(from: center) {
        case UNAuthorizationStatus.notDetermined.rawValue:
            let granted = (try? await center.requestAuthorization(options: [.alert, .sound])) ?? false
            permission = granted ? .allowed : .denied
            return granted
        case UNAuthorizationStatus.denied.rawValue:
            permission = .denied
            return false
        default:
            permission = .allowed
            return true
        }
    }

    /// Reads the current permission for Settings without asking for it.
    func refreshPermission() async {
        guard Self.notificationsAvailable else { permission = .unavailable; return }
        switch await authorizationStatus(from: UNUserNotificationCenter.current()) {
        case UNAuthorizationStatus.notDetermined.rawValue: permission = .notAsked
        case UNAuthorizationStatus.denied.rawValue: permission = .denied
        default: permission = .allowed
        }
    }

    /// Opens System Settings at TapMesh's notification settings, or the Notifications pane.
    func openSystemNotificationSettings() {
        let base = "x-apple.systempreferences:com.apple.Notifications-Settings.extension"
        let specific = Bundle.main.bundleIdentifier.flatMap { URL(string: "\(base)?id=\($0)") }
        if let specific, NSWorkspace.shared.open(specific) { return }
        if let url = URL(string: base) { NSWorkspace.shared.open(url) }
    }

    // MARK: UNUserNotificationCenterDelegate

    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification) async -> UNNotificationPresentationOptions {
        // A notification is only sent when the window isn't in front, so show it even if TapMesh
        // is the active app (for example when its window is closed or minimized).
        [.banner, .list, .sound]
    }

    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse) async {
        guard response.actionIdentifier == UNNotificationDefaultActionIdentifier else { return }
        await MainActor.run { self.showApprovals() }
    }

    /// Brings TapMesh forward and shows the Approvals page, reopening the window if it was closed.
    private func showApprovals() {
        openApprovals()
        NSApp.activate()
        // SwiftUI usually names the `WindowGroup(id: "workspace")` window "workspace-…", but that is not
        // documented, so fall back to an ordinary titled "TapMesh" window (not a panel or Settings).
        let window = NSApp.windows.first { $0.identifier?.rawValue.hasPrefix("workspace") == true }
            ?? NSApp.windows.first {
                !($0 is NSPanel) && $0.styleMask.contains(.titled) && $0.title == "TapMesh"
                    && ($0.isVisible || $0.isMiniaturized)
            }
        if let window {
            if window.isMiniaturized { window.deminiaturize(nil) }
            window.makeKeyAndOrderFront(nil)
        } else {
            // No workspace window: opening the running app sends a reopen event, which makes SwiftUI create one.
            NSWorkspace.shared.openApplication(at: Bundle.main.bundleURL, configuration: NSWorkspace.OpenConfiguration())
        }
    }
}
