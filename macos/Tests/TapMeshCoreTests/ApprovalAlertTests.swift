import XCTest
@testable import TapMeshCore

final class ApprovalAlertTests: XCTestCase {
    private func request(_ id: String, _ action: String? = "Type text", device: String? = "Khaled's iPhone", handoff: Bool = false, own: Bool = false) -> PendingApprovalSummary {
        PendingApprovalSummary(id: id, action: action, deviceName: device, handoff: handoff, ownSession: own)
    }

    func testNewRequestNotifiesOnceWithPlainText() {
        var tracker = ApprovalAlertTracker(identifierPrefix: "t")
        let first = tracker.update(pending: [request("a1")], lookingAtApp: false, notificationsEnabled: true)
        XCTAssertEqual(first.alert, ApprovalAlert(identifier: "t-1", title: "TapMesh needs your approval", body: "Type text on Khaled's iPhone.", count: 1))
        XCTAssertEqual(first.badgeCount, 1)
        XCTAssertEqual(first.withdrawnIdentifiers, [])

        let again = tracker.update(pending: [request("a1")], lookingAtApp: false, notificationsEnabled: true)
        XCTAssertNil(again.alert, "The same request is never announced twice")
        XCTAssertEqual(again.badgeCount, 1)
    }

    func testTextNeverCarriesTheRequestID() {
        var tracker = ApprovalAlertTracker(identifierPrefix: "t")
        let alert = tracker.update(pending: [request("secret-id-42")], lookingAtApp: false, notificationsEnabled: true).alert
        XCTAssertNotNil(alert)
        XCTAssertFalse(alert!.title.contains("secret-id-42")); XCTAssertFalse(alert!.body.contains("secret-id-42"))
        XCTAssertFalse(alert!.identifier.contains("secret-id-42"), "The notification identifier is opaque")
    }

    func testSeveralNewRequestsProduceOneNotification() {
        var tracker = ApprovalAlertTracker(identifierPrefix: "t")
        let plan = tracker.update(pending: [request("a1"), request("a2", "Tap"), request("a3", "Press button")], lookingAtApp: false, notificationsEnabled: true)
        XCTAssertEqual(plan.alert?.body, "3 requests need your approval.")
        XCTAssertEqual(plan.alert?.title, "TapMesh needs your approval")
        XCTAssertEqual(plan.alert?.count, 3)
        XCTAssertEqual(plan.badgeCount, 3)

        let later = tracker.update(pending: [request("a1"), request("a2"), request("a3"), request("a4", "Tap", device: nil)], lookingAtApp: false, notificationsEnabled: true)
        XCTAssertEqual(later.alert?.body, "Tap.", "Only the newly arrived request is announced")
        XCTAssertEqual(later.alert?.identifier, "t-2")
        XCTAssertEqual(later.badgeCount, 4)
    }

    func testOwnSessionNonHandoffRequestsNeverNotifyButCountOnTheBadge() {
        var tracker = ApprovalAlertTracker(identifierPrefix: "t")
        let plan = tracker.update(pending: [request("own", own: true)], lookingAtApp: false, notificationsEnabled: true)
        XCTAssertNil(plan.alert)
        XCTAssertEqual(plan.badgeCount, 1)
        XCTAssertNil(tracker.update(pending: [request("own", own: true)], lookingAtApp: false, notificationsEnabled: true).alert)

        let mixed = tracker.update(pending: [request("own", own: true), request("ext")], lookingAtApp: false, notificationsEnabled: true)
        XCTAssertEqual(mixed.alert?.count, 1, "Only the external request is announced")
    }

    func testOwnSessionHandoffNotifiesOnceWhenNotLooking() {
        var tracker = ApprovalAlertTracker(identifierPrefix: "t")
        let plan = tracker.update(pending: [request("own-h", "Sign in", handoff: true, own: true)], lookingAtApp: false, notificationsEnabled: true)
        XCTAssertEqual(plan.alert?.body, "Finish “Sign in” yourself on Khaled's iPhone.")
        XCTAssertEqual(plan.alert?.count, 1)
        XCTAssertNil(tracker.update(pending: [request("own-h", "Sign in", handoff: true, own: true)], lookingAtApp: false, notificationsEnabled: true).alert,
                     "Announced once per request")

        let mixed = tracker.update(pending: [request("own-h", handoff: true, own: true), request("own-plain", own: true), request("own-h2", handoff: true, own: true)],
                                   lookingAtApp: false, notificationsEnabled: true)
        XCTAssertEqual(mixed.alert?.count, 1, "Only the new own-session hand-off is announced; an ordinary own-session request is not")
        XCTAssertEqual(mixed.badgeCount, 3)

        let cleared = tracker.update(pending: [], lookingAtApp: false, notificationsEnabled: true)
        XCTAssertEqual(cleared.withdrawnIdentifiers, ["t-1", "t-2"], "Hand-off notifications are withdrawn once finished")
    }

    func testOwnSessionHandoffFollowsLookingAndSettingRules() {
        var tracker = ApprovalAlertTracker(identifierPrefix: "t")
        XCTAssertNil(tracker.update(pending: [request("h1", handoff: true, own: true)], lookingAtApp: true, notificationsEnabled: true).alert)
        XCTAssertNil(tracker.update(pending: [request("h1", handoff: true, own: true)], lookingAtApp: false, notificationsEnabled: true).alert,
                     "Seen while looking, so not announced after switching away")
        XCTAssertNil(tracker.update(pending: [request("h1", handoff: true, own: true), request("h2", handoff: true, own: true)], lookingAtApp: false, notificationsEnabled: false).alert,
                     "Setting off means no notification")
        XCTAssertNil(tracker.update(pending: [request("h2", handoff: true, own: true)], lookingAtApp: false, notificationsEnabled: true).alert,
                     "Turning the setting on does not announce old hand-offs")
    }

    func testNoNotificationWhileLookingAndNotLaterEither() {
        var tracker = ApprovalAlertTracker(identifierPrefix: "t")
        let looking = tracker.update(pending: [request("a1")], lookingAtApp: true, notificationsEnabled: true)
        XCTAssertNil(looking.alert)
        XCTAssertEqual(looking.badgeCount, 1, "The badge is kept regardless")
        XCTAssertNil(tracker.update(pending: [request("a1")], lookingAtApp: false, notificationsEnabled: true).alert,
                     "A request seen while the person was looking is not announced after they switch away")
    }

    func testSettingOffStopsNotificationsButKeepsBadge() {
        var tracker = ApprovalAlertTracker(identifierPrefix: "t")
        let off = tracker.update(pending: [request("a1")], lookingAtApp: false, notificationsEnabled: false)
        XCTAssertNil(off.alert)
        XCTAssertEqual(off.badgeCount, 1)
        XCTAssertNil(tracker.update(pending: [request("a1")], lookingAtApp: false, notificationsEnabled: true).alert, "Turning it on does not announce old requests")
        XCTAssertNotNil(tracker.update(pending: [request("a1"), request("a2")], lookingAtApp: false, notificationsEnabled: true).alert)
    }

    func testDecidedRequestsWithdrawTheirNotification() {
        var tracker = ApprovalAlertTracker(identifierPrefix: "t")
        _ = tracker.update(pending: [request("a1"), request("a2")], lookingAtApp: false, notificationsEnabled: true)   // t-1 covers a1, a2
        _ = tracker.update(pending: [request("a1"), request("a2"), request("b1")], lookingAtApp: false, notificationsEnabled: true) // t-2 covers b1

        let partly = tracker.update(pending: [request("a2"), request("b1")], lookingAtApp: false, notificationsEnabled: true)
        XCTAssertEqual(partly.withdrawnIdentifiers, [], "A notification stays while one of its requests still waits")
        XCTAssertEqual(partly.badgeCount, 2)

        let cleared = tracker.update(pending: [], lookingAtApp: false, notificationsEnabled: true)
        XCTAssertEqual(cleared.withdrawnIdentifiers, ["t-1", "t-2"])
        XCTAssertEqual(cleared.badgeCount, 0)
        XCTAssertTrue(tracker.handledIDs.isEmpty, "Finished requests are forgotten")
        XCTAssertTrue(tracker.deliveredGroups.isEmpty)
        XCTAssertEqual(tracker.update(pending: [], lookingAtApp: false, notificationsEnabled: true).withdrawnIdentifiers, [], "Withdrawn only once")
    }

    func testDuplicateRecordsCountOnce() {
        var tracker = ApprovalAlertTracker(identifierPrefix: "t")
        let plan = tracker.update(pending: [request("a1"), request("a1")], lookingAtApp: false, notificationsEnabled: true)
        XCTAssertEqual(plan.alert?.count, 1)
        XCTAssertEqual(plan.alert?.body, "Type text on Khaled's iPhone.")
        XCTAssertEqual(plan.badgeCount, 1)
    }

    func testWordingForHandoffsAndMissingNames() {
        XCTAssertEqual(ApprovalAlertTracker.body(for: [request("h", "Sign in", handoff: true)]), "Finish “Sign in” yourself on Khaled's iPhone.")
        XCTAssertEqual(ApprovalAlertTracker.body(for: [request("h", nil, device: nil, handoff: true)]), "Finish a step yourself on your device.")
        XCTAssertEqual(ApprovalAlertTracker.body(for: [request("x", nil, device: nil)]), "Run an action.")
        XCTAssertEqual(ApprovalAlertTracker.body(for: [request("x", "Launch app", device: "  ")]), "Launch app.")
        XCTAssertEqual(ApprovalAlertTracker.body(for: [request("x", nil)]), "Run an action on Khaled's iPhone.")
    }
}
