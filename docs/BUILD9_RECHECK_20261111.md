# Build 9 recheck, 2026-11-11

The final retained build 9 variant is Developer ID signed, Apple notarized and
stapled, with fresh automated, integrity and local-runtime checks passing.
**Physical text-input qualification remains blocked.**
The phone is currently unavailable to CoreDevice; no phone input, phone app
launch, screen capture or personal-app interaction occurred in this round.

## Device readiness

Read-only discovery succeeds. The physical iPhone has cached `paired` status and
cached Developer Mode enabled, but its connection state is `unavailable`.
These cached values do not establish current trust, lock or developer-service
readiness. The separate simulator returned by discovery was left untouched.

One bounded read-only lock-state request fails with CoreDevice error 4016.
Apple reports that the power assertion and trusted remote-service connectivity
requirements cannot be fulfilled. Current lock state and foreground
`io.tapmesh.fixture` presence therefore remain unverified. No unlock, security
bypass, fixture activation or input was attempted. Build 7's physical evidence
and the earlier locked/deferred rounds remain historical.

## Fresh automated results

| Check | Result |
| --- | --- |
| Tool contracts | 50 validated; generated catalog matches |
| Python | 312 passed, 28 subtests passed, four live simulator opt-ins skipped |
| Swift | 37 passed, zero failures |
| CoreGraphics color calibration | All 2,560 rendered samples accepted by the tested model |
| Final notarized build 9 native helper | 10 passed, including English, Arabic and combined-language OCR, coordinates, safe errors and finalized MP4 probing |
| Final notarized build 9 services | 10 passed, including isolated profile/authentication, system-only PATH, OCR, 50-tool MCP, restart/shutdown and preserved signatures |
| Final ZIP native launch on the development Mac | Six passed: notarization/Gatekeeper, isolated HTTP services, visible workspace, MCP/credentials, cooperative quit/stopped listeners, preserved signature |

The full script ran through Bash with `TAPMESH_QUALIFICATION_DEVICE` unset.
The skipped live tests are not passes. The existing stale-observation,
unexplained-pixel, protected/unknown-state and default-`exact` regressions remain
passing. No application source or guard exception was changed.

## Retained artifact and source

The original submitted ZIP SHA-256 remains
`07728f5ac2f52c2c7a17f931e095c5bc682937e21c87a1a10a38d46f201c3d1a`.
All 12,846 checked ZIP entries match the retained app. Deep strict signature
verification, Developer ID authority and hardened runtime pass. All 22 embedded
application Python source files match the current source.

The embedded archive contains 130 source files. Application code, tests, build
recipes and other archived files match the worktree; the sole difference is
`docs/RELEASE_STATUS.md`, which is the documented packaging-time snapshot.
Subsequent qualification reports are separate receipts. The original package
was preserved; a separate notarized ZIP was created as described below.

The complete-source TAR SHA-256 remains
`5a60a84b52b652c6ddec860c150d29e06be9ffa53b75585a72d288886a9fb85a`.
Its embedded application-source digest, runtime-manifest binding and lockfile
binding match. All 482 source/license files referenced by its manifest were
rehashed directly from the archive and match, including 112 Python sdists and
four upstream archives. The retained source review reports zero gaps.
Independent bit-for-bit rebuilding is not claimed.

## Apple notarization and final package

The initial pre-notary Gatekeeper assessment rejected the original package as
`Unnotarized Developer ID` (exit 3), and stapler reported no ticket (exit 65).
These earlier results remain recorded. The existing project Keychain profile
successfully authenticated to Apple without exporting credentials.

Apple accepted submission `a64b44d8-8076-40ac-bba4-57f537724f49`; its log binds
acceptance to the original submitted ZIP hash and reports no issues. An initial
45-second wait expired while Apple processing continued; the next bounded wait
observed acceptance. The ticket was stapled to a separate copy of build 9.

The first post-notary runtime attempt stopped before startup because stapling
under the private preparation umask created `Contents/CodeResources` with mode
`0600`. Its ZIP, valid publisher signature, native-launch pass and failed runtime
log were retained. Applying the project's existing bundle-permission normalizer
changed only this added ticket to `0644`. Application code and all original
12,846 bundle files remained byte-identical. No re-signing of application code
or new notarization submission was needed; ticket/signature/Gatekeeper checks
pass after normalization.

The final ZIP SHA-256 is
`c3eed4e33528dd253487e1e541ad26033e090a64c6d0938e7542318a57878a33`.
It is retained in the private `Release/Build9-Notarized-20261111-Final` directory.
All 12,847 checked entries match its app, including the added ticket. Retained
and independently extracted copies have qualified permissions and pass ticket,
strict signature and Gatekeeper checks (`Notarized Developer ID`). The original
complete-source archive remains an exact source match.

All final runtime, native-helper and native-entrypoint checks in the table above
were rerun against this final extracted ZIP. Native launch was on the existing
arm64 macOS 27.2 development Mac with a fresh isolated profile; it is not
clean-host or Finder first-open qualification. The protected production Sparkle
key signed the final ZIP. The official verifier and an independent embedded
public-key verifier both accept it and reject modified bytes; private key bytes
were not exported. No public upload or production appcast edit was made.

## Open release gates

Repeated physical `exact` and `text_target` coordinate fill, independent
English/Arabic app readback, deletion/focused input, the native Mac option and
safe stale-frame/unexplained-change rejection still require the available,
unlocked foreground owned fixture. Exact final-artifact installation/update on
clean supported Macs remains open. The installed
Mac app, private TNAS setup and other simulators were preserved.

The machine-readable results and hashes of retained private command logs are in
[BUILD9_RECHECK_20261111.json](BUILD9_RECHECK_20261111.json). The original build 9
qualification remains in
[CARET_COLOR_BUILD9_QUALIFICATION.json](CARET_COLOR_BUILD9_QUALIFICATION.json).
Final notarization and publisher evidence is in
[NOTARIZATION_BUILD9_QUALIFICATION.json](NOTARIZATION_BUILD9_QUALIFICATION.json).
