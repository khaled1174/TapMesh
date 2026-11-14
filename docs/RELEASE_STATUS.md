# TapMesh release qualification

Updated 2026-11-14 (Asia/Riyadh). The public repository contains the standalone local product
source. Public draft build 3 is notarized and remains unpublished. The final local
build 9 variant is Developer ID signed, Apple notarized and stapled, with matching
complete corresponding source, a caret color
regression fix and fresh automated verification. The 2026-11-14 physical round
confirms eight fill outcomes independently, including English, exact Arabic AX
readback, the native Mac option and one actual non-exact clock/status dispatch.
Twelve other fill attempts safely refused before input. Active-field/caret
repeatability remains unqualified. Final build 9 clean arm64 Mac installation
and native update now pass 26 checks on macOS 15.8.2 and 26.7.2, including exact
post-update bundle comparison and credential preservation.
Fresh artifact/source, signature, ticket and Gatekeeper checks pass; the previous
November 11 CoreDevice 4016 blocker is retained as historical evidence.
It remains unpublished. Original pre-notary build 9 artifacts are retained.
The product is
Apple Silicon only, with macOS 15 as its minimum deployment target.

| Area | Evidence and current limit |
| --- | --- |
| Portable services | Linux automated contracts and regressions pass in public CI. |
| Supported Mac builds | Public CI passes on macOS 15 and 26, including standalone packaging and relocated local HTTP/MCP runtime execution. |
| Local automated tests | Current local source: 312 Python tests, 28 subtests and 37 Swift tests pass; four live simulator opt-ins are skipped. This includes a native CoreGraphics calibration of 2,560 blue compositing samples. Coverage includes text-target validation, negative dispatch, OCR transport bounds and explicit option serialization. Build 3 retains its earlier 220-test artifact evidence. |
| Runtime independence | Bundled interpreter, packages, companion and native OCR/media helper run with system-only PATH; profile isolation, authentication, restart, shutdown and preserved bundle signatures pass. |
| Native interface and clean hosts | The exact notarized build 3 ZIP passes native first launch, a visible workspace, authenticated HTTP/MCP, cooperative quit, stopped listeners and preserved signatures on fresh arm64 macOS 15.8.2 and 26.7.2 GitHub runners. Gatekeeper and stapled-ticket checks pass on both. Finder's first-open confirmation was not exercised. |
| Final build 9 installation/update | Exact final ZIP native installation and a production-key Sparkle update to that same ZIP pass 26 checks on fresh arm64 macOS 15.8.2 and 26.7.2 hosts. Credentials/canary survive, old services stop, the update fence clears, all 14,795 bundle entries match, and updated signatures/tickets/Gatekeeper and quit pass. The initial test-harness bytecode-write failure is retained. Starting build 8 is a staged fixture derived from build 9; clean-host Finder confirmation and historical migration coverage are not inferred. See [CLEAN_MAC_BUILD9_REPORT](CLEAN_MAC_BUILD9_REPORT.md). |
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

A final source review further restricts reuse to identical images before and after AX inspection. A changed inspection is never cached, so a scrolled or replaced view cannot inherit the earlier classification. This stricter source-only check is not included in local build 5; its packaged physical qualification remains open.


## Opt-in text-target validation, 2026-11-08

Current source adds explicit `fill.frame_validation: "text_target"` for physical
portrait ordinary app fields without submit. The default remains `exact`.
Complete private OCR must confirm a unique high-confidence text target at the
original point. Exact pixel comparison permits only bounded detected clock/status
changes and a standard blue caret near a text edge. Other visual changes, missing
or truncated evidence, expired observations and protected/unknown states stop
input. Generic editable AX semantics are not inferred and app readback is still
required. The native replacement sheet exposes this as an unchecked option.
Private frame evidence expires from RAM after 30 seconds, has a 16 MiB aggregate
budget and is never written to logs or disk by this guard.

Real UIKit simulator captures and local Vision OCR pass six final comparisons
with a forced minute/status-indicator change and a blinking caret. The independent
keyboard-settling precondition, five earlier six-sample rejected runs and the
initial zero-input startup failure remain recorded. These are algorithm checks on
actual simulator pixels, not physical HID qualification. The owned simulator was
restored to Shutdown and another running simulator was left untouched.

The owner explicitly deferred the unavailable iPhone. No new physical input was
sent. The new signed local build 7 must still pass repeated coordinate-fill and
independent readback on the foreground unlocked physical fixture; its new native
option has not been exercised interactively. Build 7 is not notarized or published
and has no new complete distribution-source TAR. The public draft binary, matching
complete source and empty production appcast remain bound to immutable build 3.
See [TEXT_FRAME_GUARD_QUALIFICATION.json](TEXT_FRAME_GUARD_QUALIFICATION.json) for
exact candidate runtime evidence, retained attempts and remaining gates.

## Physical build 7 follow-up, 2026-11-09

The owner made the wired iPhone available and authorized immediate fixture-only
work. Its paired/Developer Mode/unlocked prerequisites pass. The exact signed
build 7 ZIP, embedded source and recorded runtime source copies match. Fresh
local checks pass 269 Python tests, 28 subtests, four physical opt-in skips and
37 Swift tests; a new relocated-runtime round passes all ten checks.

Eleven HTTP coordinate fills retain four transmissions and seven safe not-sent
rejections. Two English clipboard replacements have independent app readbacks;
one Arabic clipboard replacement is visually confirmed, with exact Arabic OCR
still inconclusive. The other transmission used an operator-assumed US keyboard
declaration on the actual Arabic layout and is not passed keyboard evidence.
All transmitted fills used an exact-frame proof. Generic physical repeatability
and transmission after a non-exact text-target proof remain unqualified. The AX
sensitivity audit can scroll the fixture; caret-only diagnostics have mixed
results across retained rounds. No tolerance or protection was relaxed.

The native replacement option was observed unchecked, explicitly selected and
exercised through a request that rejected its changed frame before input. Native
focused clipboard input and Backspace have independent app readback. An aged
physical observation is rejected with stale_observation/not_sent. The fixture
was reset, the test Mac window quit, owned listeners stopped, signatures stayed
valid and temporary fixture pixels were removed. The previously running Mac app
and private TNAS environment were preserved.

A private complete-source archive for build 7 is now assembled and verified:
112/112 lock-hashed Python sdists, assessed native sources and zero source gaps.
This closes the earlier build 7 source-availability gap. Physical repeatability,
final candidate notarization and exact-artifact clean-host install/update remain
open before public binary publication. The production appcast and immutable
draft build 3 assets are unchanged. Full evidence, including unsuccessful probes,
is in [PHYSICAL_TEXT_BUILD7_REPORT.md](PHYSICAL_TEXT_BUILD7_REPORT.md) and
[PHYSICAL_TEXT_BUILD7_QUALIFICATION.json](PHYSICAL_TEXT_BUILD7_QUALIFICATION.json).

## Pixel-derived caret edge, build 8, 2026-11-10

A failing Router/image regression demonstrated rejection of a standard caret
beside unchanged glyphs when OCR included excess blank space. The opt-in guard
can now measure the glyph edge in a frame that shows unchanged background at
every changed caret pixel. Existing shape/color/budget checks remain; glyph
changes and unprovable backgrounds stop input. Four appearance/disappearance
and 1x/3x cases fail on the old guard and pass with this correction. Three new
negative cases and all earlier safety regressions pass.

Signed local build 8 matches the changed source and tests. Fresh 276-case Python,
37-case Swift, contract and ten-check relocated-runtime rounds pass. Matching
complete source is assembled privately with 112/112 Python sdists and zero gaps.
The device initially required a passcode; a subsequent lock query timed out and
the final read returned CoreDevice 4016 (trusted connectivity/power assertions unavailable).
No new physical input was sent. AX scrolling and generic physical repeatability
remain open; automated success does not certify the new candidate on the phone.
Notarization, final physical/distribution qualification and public binary
publication remain pending. See [CARET_GLYPH_BUILD8_REPORT](CARET_GLYPH_BUILD8_REPORT.md).

## Caret color qualification, build 9, 2026-11-10

Six Router cases prove that source build 8 ignored a saturated non-blue minority
pixel inside the bounded blue caret. Build 9 rejects these before input while
retaining tested grayscale shadows and rounded blue antialias transitions.
The exact default and previous geometry, budgets, confidence, context and
authorization checks remain. The 312-case Python suite, 37 Swift tests,
50 contracts, ten exact-package native-helper tests and ten relocated-runtime
checks pass. A native CoreGraphics calibration passes all 2,560 rendered samples.

Signed local build 9 and its complete source (112/112 sdists, zero gaps) are
retained privately. The owner deferred phone work while it is locked; no device
query or input occurred this round. Physical repeatability, notarization and
final clean-host install/update are still open. Earlier build 7/8 attempts,
immutable draft build 3 and the empty production appcast are preserved. Changes
are local; no remote is configured or pushed. See
[CARET_COLOR_BUILD9_REPORT](CARET_COLOR_BUILD9_REPORT.md).

## Retained build 9 recheck, 2026-11-11

Fresh checks pass 312 Python tests, 28 subtests, 37 Swift tests, 50 contracts,
ten native-helper checks and ten relocated-runtime checks. Four live simulator
opt-ins are skipped. Original ZIP and complete-source hashes remain unchanged; all
12,846 checked ZIP entries, 22 runtime source copies and 482 manifest-referenced
source/license files match. The embedded release-status document is the retained
packaging snapshot; later qualification reports are separate receipts.

Read-only phone discovery reports unavailable connectivity. A lock-state read
returns CoreDevice 4016; current lock and foreground fixture state are unknown.
No phone input, app activation or screen capture was attempted. Initial
pre-notary Gatekeeper/stapler failures are retained. Apple subsequently accepted
build 9, and the final normalized/stapled ZIP passes Gatekeeper, ten runtime,
ten native-helper and six isolated native-launch checks on the development Mac.
The final production-key update signature independently verifies and modified
bytes are rejected. The complete-source archive is unchanged. Final physical
qualification and clean-host install/update remain open; no public upload or
appcast change was made. See
[BUILD9_RECHECK_20261111](BUILD9_RECHECK_20261111.md).

## Physical final build 9 follow-up, 2026-11-14

Read-only checks now confirm the wired, paired iPhone 18 Pro Max / iOS 27.2.1,
Developer Mode, usable developer services, unlocked state and the foreground
owned fixture. The final notarized build 9 ZIP and complete-source digests are
unchanged. No production source or safety tolerance changed.

Twenty post-authorization fills retain eight transmissions with eight independent
fixture outcomes and twelve safe not-sent refusals. Twenty approval-required
responses are separate. Two HTTP requests used default exact and fifteen used
explicit text_target; three more came from the native Mac interface. Four
prepared fresh-fixture cases pass four of four with exact-frame proofs, which
does not certify continuous active editing. One fresh reference resolved to
coordinates and passed an actual stable_text_target clock/status proof over
2,942 native changed pixels, followed by field and Entered-mirror readback.
Repeated physical caret dispatch remains unqualified.

Exact named AX values confirm Arabic replacement, focused Unicode paste and
Arabic Backspace; English focused append/Backspace have independent visible
readbacks. Generic editable AX semantics and the current US-layout keyboard
method are not certified. The native unchecked option was explicitly selected;
two fills refused safely and one passed independent readback. Its full response
proof mode was not retained. Hide-keyboard delivery did not prove keyboard
disappearance; a later single-character paste did not pass literal concatenation.
AX viewport shifts, all refused frames, read-only probe failures and setup
stops remain documented without widening exceptions.

Fresh checks pass 312 Python tests, 28 subtests, 37 Swift tests, 50 schemas and
2,560 native compositing samples; four live simulator opt-ins are skipped.
Signature, stapled ticket and Gatekeeper still pass. The test GUI and owned
services stopped, and only the fixture's transient state was reset to empty and
left foreground. Private TNAS, the installed Mac app and other devices were
preserved. At the end of this physical round, active-field repeatability and
clean supported-Mac install/update were still open. The subsequent round below
passes native install/update; physical repeatability still blocks publication.
The production appcast remains empty. See
[PHYSICAL_TEXT_BUILD9_REPORT.md](PHYSICAL_TEXT_BUILD9_REPORT.md) and
[PHYSICAL_TEXT_BUILD9_QUALIFICATION.json](PHYSICAL_TEXT_BUILD9_QUALIFICATION.json).

## Exact final build 9 clean-Mac install/update, 2026-11-14

The owner requested final installation/update testing. A dedicated private CI
repository ran on fresh arm64 macOS 15.8.2 and 26.7.2. Both hosts pass six native
installation and seven native update checks: 26 total, plus two input-digest
receipts. The exact final ZIP is unchanged. Sparkle uses its existing production
signature to install that ZIP from an independently notarized disposable
staging fixture. The fixture's build number is 8 but its program is derived from
build 9; this does not certify historical build 8 migration.

Both updated copies independently match all 14,795 expected bundle entries,
retain credentials and a binary canary, clear the update fence and start a new
healthy instance with old listeners stopped. Updated signatures, tickets,
Gatekeeper, safe modes, cooperative quit and stopped services pass. The initial
CI attempt stopped before app launch because the test interpreter created
bytecode caches inside signed resources. A controlled reproduction and the
corrected -B workflow are retained; no security check or product source changed.
The affected local app copy was retained and restored from the immutable ZIP.

Temporary private remote binary inputs were deleted after evidence download.
The installed user app, public draft build 3 assets, private TNAS and production
appcast are unchanged. No phone or simulator operations occurred. Clean-host
Finder first-open confirmation remains unexercised. The final native install/
update gate is passed; physical active-field/caret repeatability still blocks
publication. See [CLEAN_MAC_BUILD9_REPORT.md](CLEAN_MAC_BUILD9_REPORT.md) and
[CLEAN_MAC_BUILD9_QUALIFICATION.json](CLEAN_MAC_BUILD9_QUALIFICATION.json).
