# Build 9: bounded caret color qualification

Date: 2026-11-10 (Asia/Riyadh). Signed local build 9 passes the automated suite,
exact-package runtime checks and matching-source verification. **Physical text
repeatability remains unqualified and is deferred by the owner while the phone
is locked.** No phone discovery, activation, capture or input occurred this round.

A deterministic Router regression found that the existing 90-percent blue-caret
threshold ignored an arbitrary saturated non-blue minority pixel inside the
otherwise valid caret. Six cases (red, green and magenta, with ordinary and
oversized OCR rectangles) incorrectly transmitted with source baseline 45ba65b.
All six now reject before input. The baseline overlay also passes 21 positive
antialias/shadow cases. The initial failing reproduction and an intermediate
overstrict neutral-shadow rejection are retained in the work evidence.

Changed caret pixels without a strongly blue endpoint must now be neutral
grayscale in both images or compatible with an 8-bit rounded source-over blend
from an allowed blue foreground, in either direction. The inverse-opacity check
includes half-channel-level rounding tolerance. The existing 90-percent strongly
blue minimum, 65-percent coverage, geometry, 1,000-logical-pixel budget, unchanged
context, target confidence, lock/epoch/expiry checks and action approvals remain
in force. The default remains `exact`; no retargeting or automatic retry is added.
This does not establish that every arbitrary color change can be distinguished
from a real blue-caret transition, nor resolve AX-induced scrolling.

Fresh verification passes **312 Python tests, 28 subtests, 37 Swift tests and
50 catalog contracts**. Four live simulator opt-ins are intentionally skipped
with `TAPMESH_QUALIFICATION_DEVICE` unset; they are not passes. The caret cases
cover appearance/disappearance on both sides at 1x, 2x and 3x. A repeatable macOS
test renders 2,560 actual CoreGraphics DeviceRGB samples (two blues, five
backgrounds, all 256 opacity levels); every sample fits the accepted color model.
It uses no device and adds no runtime dependency. Synthetic samples do not
certify an actual iPhone keyboard or field outcome.

The exact final build 9 native helper passes all ten black-box checks, including
English, Arabic and combined-language Vision OCR, coordinates, safe request
rejection and finalized MP4 probing. All ten relocated-runtime checks also pass:
system-only PATH, private profile isolation, local authentication, bundled OCR,
50-tool MCP, restart/shutdown and preserved signatures. The previously installed
Mac app and TNAS environment were preserved.

The Developer ID signed ZIP SHA-256 is `07728f5ac2f52c2c7a17f931e095c5bc682937e21c87a1a10a38d46f201c3d1a`.
Every checked ZIP bundle file matches the retained app, the changed production
guard and all three changed/added test files match the embedded source, and the
runtime guard matches the working source. The first build 9 package was retained
privately and superseded before runtime qualification to include the native
calibration test in the source snapshot. Direct invocation of the nonexecutable
test script initially returned 126; invoking the full script through Bash passed.

Matching complete source includes 112/112 checksum-verified Python sdists,
assessed native dependencies and zero source gaps. TAR SHA-256:
`5a60a84b52b652c6ddec860c150d29e06be9ffa53b75585a72d288886a9fb85a`. Independent bit-for-bit rebuilding is not claimed. Embedded docs
are the packaging snapshot; this report and JSON are separate final receipts.
All application changes remain local; this checkout has no configured remotes.

Build 7 physical attempts and build 8 evidence remain historical and unchanged.
Build 9 has not been notarized or published. Final physical qualification,
notarization and exact-artifact clean-host install/update remain release gates.
Production appcast and immutable draft build 3 assets were not changed. See
[CARET_COLOR_BUILD9_QUALIFICATION.json](CARET_COLOR_BUILD9_QUALIFICATION.json).
