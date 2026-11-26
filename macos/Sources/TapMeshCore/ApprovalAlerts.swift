import Foundation

/// What a system notification may say about one waiting approval: a readable action
/// name and a device name only. Arguments, typed text, the argument summary and the
/// request ID never reach the notification text.
public struct PendingApprovalSummary: Equatable, Sendable {
    /// The service's approval ID; used only to remember what was already announced.
    public let id: String
    /// Readable action name, e.g. "Type text". Nil when the record names no action.
    public let action: String?
    /// The device's display name when TapMesh knows it; never an identifier.
    public let deviceName: String?
    /// A step the person must finish on the device themselves (policy "U").
    public let handoff: Bool
    /// Created by this window's own session. The store allows its own ordinary requests
    /// itself, so these never produce a notification unless they are hand-offs, which only
    /// the person can finish on the device.
    public let ownSession: Bool
    /// The one-time grant for ordinary agent control of a physical device.
    public let deviceGrant: Bool
    /// Whether this request may be announced at all: anything from another session, and a
    /// hand-off from this window's own session.
    var announceable: Bool { !ownSession || handoff || deviceGrant }

    public init(id: String, action: String?, deviceName: String?, handoff: Bool = false, ownSession: Bool = false, deviceGrant: Bool = false) {
        self.id = id; self.action = action; self.deviceName = deviceName; self.handoff = handoff; self.ownSession = ownSession
        self.deviceGrant = deviceGrant
    }
}

/// One notification to deliver.
public struct ApprovalAlert: Equatable, Sendable {
    /// Notification identifier; opaque, never derived from a request ID.
    public let identifier: String
    public let title: String
    public let body: String
    /// How many requests this notification announces.
    public let count: Int
}

/// What to do after one look at the pending approvals.
public struct ApprovalAlertPlan: Equatable, Sendable {
    /// At most one notification, however many requests are new.
    public let alert: ApprovalAlert?
    /// Delivered notifications whose requests are all no longer pending (decided or expired).
    public let withdrawnIdentifiers: [String]
    /// Dock badge value: every request waiting in the Approvals page; zero clears the badge.
    public let badgeCount: Int
}

/// Decides when TapMesh tells the person about approval requests while they are not looking.
///
/// Every pending request is considered exactly once. A request seen while the person is
/// looking at TapMesh, while notifications are turned off, or created by this window's own
/// session (other than a hand-off) is remembered as handled and never announced later.
/// A hand-off from the window's own session follows the same rules as any other request.
public struct ApprovalAlertTracker: Sendable {
    public static let title = String(localized: "TapMesh needs your approval")

    /// Requests already considered: announced, or deliberately not announced.
    public private(set) var handledIDs: Set<String> = []
    /// Delivered notification identifier → the requests it announced that may still be pending.
    public private(set) var deliveredGroups: [String: Set<String>] = [:]
    private let identifierPrefix: String
    private var sequence = 0

    /// `identifierPrefix` keeps identifiers from different launches apart.
    public init(identifierPrefix: String) { self.identifierPrefix = identifierPrefix }

    public mutating func update(pending: [PendingApprovalSummary], lookingAtApp: Bool, notificationsEnabled: Bool) -> ApprovalAlertPlan {
        let pendingIDs = Set(pending.map(\.id))
        var seen = Set<String>()
        let fresh = pending.filter { !handledIDs.contains($0.id) && $0.announceable && seen.insert($0.id).inserted }

        // Requests that went away (decided, expired) are forgotten; their notifications are withdrawn
        // once nothing they announced is still waiting.
        var withdrawn: [String] = []
        for (identifier, ids) in deliveredGroups {
            let remaining = ids.intersection(pendingIDs)
            if remaining.isEmpty { withdrawn.append(identifier); deliveredGroups[identifier] = nil }
            else { deliveredGroups[identifier] = remaining }
        }
        handledIDs = pendingIDs

        var alert: ApprovalAlert?
        if !fresh.isEmpty, notificationsEnabled, !lookingAtApp {
            sequence += 1
            let identifier = "\(identifierPrefix)-\(sequence)"
            if fresh.count == 1, let grant = fresh.first, grant.deviceGrant {
                alert = ApprovalAlert(identifier: identifier, title: DeviceGrantCopy.title(for: grant.deviceName), body: DeviceGrantCopy.explanation, count: 1)
            } else {
                alert = ApprovalAlert(identifier: identifier, title: Self.title, body: Self.body(for: fresh), count: fresh.count)
            }
            deliveredGroups[identifier] = Set(fresh.map(\.id))
        }
        return ApprovalAlertPlan(alert: alert, withdrawnIdentifiers: withdrawn.sorted(), badgeCount: pendingIDs.count)
    }

    /// "Type text on Khaled's iPhone", or a count when several requests arrive together.
    static func body(for requests: [PendingApprovalSummary]) -> String {
        guard requests.count == 1, let request = requests.first else { return String(localized: "\(requests.count) requests need your approval.") }
        let device = request.deviceName.flatMap { $0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : $0 }
        let action = request.action.flatMap { $0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : $0 }
        // Whole sentences per case, so each language can order the action and the device itself.
        switch (request.handoff, action, device) {
        case (true, let action?, let device?): return String(localized: "Finish “\(action)” yourself on \(device).")
        case (true, let action?, nil): return String(localized: "Finish “\(action)” yourself on your device.")
        case (true, nil, let device?): return String(localized: "Finish a step yourself on \(device).")
        case (true, nil, nil): return String(localized: "Finish a step yourself on your device.")
        case (false, let action?, let device?): return String(localized: "\(action) on \(device).")
        case (false, let action?, nil): return "\(action)."
        case (false, nil, let device?): return String(localized: "Run an action on \(device).")
        case (false, nil, nil): return String(localized: "Run an action.")
        }
    }
}
