# TapMesh release qualification

Updated 2026-11-07 (Asia/Riyadh). The public repository contains the standalone local product
source. The candidate is notarized and remains in an unpublished draft release. It is
Apple Silicon only, with macOS 15 as its minimum deployment target.

| Area | Evidence and current limit |
| --- | --- |
| Portable services | Linux automated contracts and regressions pass in public CI. |
| Supported Mac builds | Public CI passes on macOS 15 and 26, including standalone packaging and relocated local HTTP/MCP runtime execution. |
| Local automated tests | 220 Python tests, 28 subtests and 36 Swift tests pass; four physical opt-in tests are skipped. Eight new regressions cover device-specific recording support and safe recording finalization. |
| Runtime independence | Bundled interpreter, packages, companion and native OCR/media helper run with system-only PATH; profile isolation, authentication, restart, shutdown and preserved bundle signatures pass. |
| Native interface and clean hosts | The exact notarized build 3 ZIP passes native first launch, a visible workspace, authenticated HTTP/MCP, cooperative quit, stopped listeners and preserved signatures on fresh arm64 macOS 15.8.2 and 26.7.2 GitHub runners. Gatekeeper and stapled-ticket checks pass on both. Finder's first-open confirmation was not exercised. |
| iPhone/iPad simulators | Fresh standalone HTTP/MCP, capture, Vision OCR, observed taps/text, finalized media and SHA-256 transfers pass on both owned simulators (25 checks). Explicit accessibility fails after relaunch on both (two failed checks); a 3,600-second capture soak passes with 240 decoded captures, zero errors, stopped owned listeners and preserved signatures. Both owned simulators were shut down afterward. |
| Physical devices | Build 2 fixture tests on iPhone 18 Pro Max / iOS 27.2 pass capture/OCR, tap, double tap, long-press activation, pinch, drag, Unicode clipboard fill, text/delete, lifecycle and checksum-verified screenshot/container transfer. Two initial stale-frame actions were rejected before input; fresh inspected operations later passed. Build 3 fixes the incorrect recording advertisement using the complete per-device Xcode capability list; the packaged HTTP route reports unsupported and rejects recording as not_sent before starting a writer. The iPhone capture-stability follow-up remains owner-deferred. Fresh build 3 iPad Pro 13-inch (M5) / iPadOS 27.0 tests pass observed tap/double tap/long-press activation, pinch, drag, swipe, lifecycle, MCP ImageContent, and checksum-verified screenshot/container transfer. Portrait Unicode text is visually verified; a later fresh-frame ASCII fill and Backspace readback pass. An hour-long read-only iPad run passes 121 fixture-verified samples over 3,603.97 seconds, zero errors (30-second cadence; mean sample 3.573 s, maximum 12.369 s). Qualification remains partial: the initial 30-second read deadline and rejected stale-frame requests are retained, Arabic OCR exactness varies, and Xcode refuses a remote/synchronized clipboard read (26006). A current native-window recheck was not exercised because the CUA host pipe failed before inventory. |
| Updates | Official signing and independent public verification pass. Native modified-download rejection and interrupted-download handling pass locally. Disposable notarized build 3→4 fixtures pass native Sparkle download, installation and relaunch on fresh macOS 15.8.2 and 26.7.2 runners, preserving default-profile credentials, clearing the update fence, retaining valid signatures and stopping owned listeners after quit. Fixtures use an ephemeral test key. The protected production key signed the final production ZIP; independent verification against its public key passes and modified bytes are rejected. |
| Signing and notarization | Apple accepted build 3 submission `773fdb80-ffcf-4127-8695-82aca0a7ddf7`. Stapler validation, deep strict signature verification and Gatekeeper assessment pass on the development Mac (`Notarized Developer ID`). The accepted build 2 is retained as historical qualification evidence. |
| Corresponding source | A matching complete offline source archive and checksums are attached to the draft candidate: 112/112 Python sdists and assessed native dependencies, with zero source-availability gaps. Independent bit-for-bit rebuilding has not been performed. |

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
backend operations are not claimed. Final physical iPhone stability and generic physical text-entry repeatability
remain open before public binary publication. The public app-source reference
and complete-source archive remain the immutable build 3 snapshot; this newer
qualification documentation does not change application bytes.

The production update feed is
`https://raw.githubusercontent.com/khaled1174/TapMesh/main/appcast.xml`. It remains
empty until a qualified public binary and matching complete source are ready.
Internal staging copies alone may use an explicit literal-loopback HTTP feed
and their own disposable signing key. They do not change the production key or
release feed.
