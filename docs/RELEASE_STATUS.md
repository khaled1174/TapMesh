# TapMesh release qualification

Updated 2026-11-07 (Asia/Riyadh). The public repository contains the standalone local product
source. The candidate is notarized and remains in an unpublished draft release. It is
Apple Silicon only, with macOS 15 as its minimum deployment target.

| Area | Evidence and current limit |
| --- | --- |
| Portable services | Linux automated contracts and regressions pass in public CI. |
| Supported Mac builds | Public CI passes on macOS 15 and 26, including standalone packaging and relocated local HTTP/MCP runtime execution. |
| Local automated tests | Current source: 238 Python tests, 28 subtests and 36 Swift tests pass; four physical opt-in tests are skipped. Eighteen additional regressions cover frame-bound text inspection, invalidation and final input guards. Build 3 retains its earlier 220-test artifact evidence. |
| Runtime independence | Bundled interpreter, packages, companion and native OCR/media helper run with system-only PATH; profile isolation, authentication, restart, shutdown and preserved bundle signatures pass. |
| Native interface and clean hosts | The exact notarized build 3 ZIP passes native first launch, a visible workspace, authenticated HTTP/MCP, cooperative quit, stopped listeners and preserved signatures on fresh arm64 macOS 15.8.2 and 26.7.2 GitHub runners. Gatekeeper and stapled-ticket checks pass on both. Finder's first-open confirmation was not exercised. |
| iPhone/iPad simulators | Fresh standalone HTTP/MCP, capture, Vision OCR, observed taps/text, finalized media and SHA-256 transfers pass on both owned simulators (25 checks). Explicit accessibility fails after relaunch on both (two failed checks); a 3,600-second capture soak passes with 240 decoded captures, zero errors, stopped owned listeners and preserved signatures. Both owned simulators were shut down afterward. |
| Physical devices | Fresh build 3 iPhone 18 Pro Max / iOS 27.2 tests pass observed tap, double tap, long-press activation, pinch, fresh-coordinate drag/swipe, lifecycle, local Vision OCR, independent MCP ImageContent and checksum-verified screenshot/container transfers. Unicode clipboard insertion and focused US-layout keyboard insertion with Backspace readback pass. Its read-only portrait hour soak passes 121 fixture-verified samples over 3,602.41 seconds with zero errors (30-second cadence; mean sample 3.241 s, maximum 12.823 s). Earlier missing-fixture observations stopped input; the owner reconfirmed, and explicit fixture activation restored a verified view before the hour. Coordinate-fill stale-frame rejections remain recorded and generic text replacement repeatability is not certified. Build 3 correctly reports unsupported physical recording and rejects it as not_sent before a writer starts. Fresh build 3 iPad Pro 13-inch (M5) / iPadOS 27.0 tests pass observed controls, lifecycle, MCP ImageContent and checksum-verified transfers. Its portrait hour soak passes 121 fixture-verified samples over 3,603.97 seconds with zero errors; mean sample 3.573 s, maximum 12.369 s. Unicode insertion is visually verified and a later fresh-frame ASCII fill/Backspace readback passes. The initial iPad 30-second read deadline, safe stale-frame rejections, variable Arabic OCR and Xcode remote/synchronized clipboard refusal (26006) remain recorded. A current native-window recheck was not exercised because the CUA host pipe failed before inventory. Historical build 2 attempts remain separate. |
| Updates | Official signing and independent public verification pass. Native modified-download rejection and interrupted-download handling pass locally. Disposable notarized build 3→4 fixtures pass native Sparkle download, installation and relaunch on fresh macOS 15.8.2 and 26.7.2 runners, preserving default-profile credentials, clearing the update fence, retaining valid signatures and stopping owned listeners after quit. Fixtures use an ephemeral test key. The protected production key signed the final production ZIP; independent verification against its public key passes and modified bytes are rejected. |
| Signing and notarization | Apple accepted build 3 submission `773fdb80-ffcf-4127-8695-82aca0a7ddf7`. Stapler validation, deep strict signature verification and Gatekeeper assessment pass on the development Mac (`Notarized Developer ID`). The accepted build 2 is retained as historical qualification evidence. |
| Corresponding source | A matching complete offline source archive and checksums are attached to the draft candidate: 112/112 Python sdists and assessed native dependencies, with zero source-availability gaps. Independent bit-for-bit rebuilding has not been performed. |

A local build 5 follow-up on 2026-11-08 adds a ten-second, device/epoch/serial/full-frame-bound text-inspection advisory. It invalidates on actual input, other mutations, lock/binding changes, expiry or failed reinspection. It does not authorize actions or reclassify protected/unknown states. Native input checks lock, geometry and coordinate-frame freshness again after media setup. Focused keyboard input retains its separate no-coordinate contract. Current source passes [CI 42442515613](https://github.com/khaled1174/TapMesh/actions/runs/42442515613).

Signed local build 5 passes native Finder launch, a connected workspace and the 50-tool catalog, focused English replacement/Backspace, visually verified Arabic clipboard input, MCP ImageContent, checksum-verified transfers and cooperative quit. Strict Arabic OCR readback remains inconclusive. The initial build 4 coordinate-fill sequence passed one of eight attempts; seven stale-frame requests were rejected before input. Read-only comparisons observed changes in the status area and text caret. Generic coordinate-fill repeatability remains unqualified. An overstrict focused-text check in build 4 was corrected and its three rejected attempts remain recorded. Build 5 is a local test candidate, has not been notarized, and has no new complete distribution-source TAR. The public draft binary and matching source still identify immutable build 3; no build 5 binary or update feed was published.

The clean-host install/update evidence is public CI run
[42235936847](https://github.com/khaled1174/TapMesh/actions/runs/42235936847).
The workflow binds the downloaded production ZIP and disposable update fixtures
to their SHA-256 digests. Product source verification also passes on Linux,
macOS 15 and macOS 26 in
[42234681170](https://github.com/khaled1174/TapMesh/actions/runs/42234681170).

Simulator evidence does not certify physical-device behavior. A later diagnostic
observed a zero-frame simulator AX read recover to 60 real elements after a short
wait; it does not erase the two historical packaged failures or certify repeated
AX readiness. Intel support, every Apple OS/device combination and unsupported
backend operations are not claimed. Both physical-device periodic capture soaks pass. Generic physical text-entry
repeatability remains open before public binary publication. The public app-source reference
and complete-source archive remain the immutable build 3 snapshot; this newer
qualification documentation does not change application bytes.

The production update feed is
`https://raw.githubusercontent.com/khaled1174/TapMesh/main/appcast.xml`. It remains
empty until a qualified public binary and matching complete source are ready.
Internal staging copies alone may use an explicit literal-loopback HTTP feed
and their own disposable signing key. They do not change the production key or
release feed.
