# Public source and connection documentation update — 2026-11-26

This update advances the standalone public tree from the earlier 0.1.0 source
to the current Developer Preview 0.2.0 development source, based on local revision
`c4c96ce` plus the new connection documentation. The source includes fixes added
after staged build 23; no exact-binary qualification is inferred from this tree.

The update contains first-party app/helper/service sources, contracts, dependency
locks, build scripts, fixtures, tests and selected generic documentation. It adds
English/Arabic MCP setup instructions, a token-free stdio example and a reusable
project-instruction template. The generated in-app configuration uses the actual
installation path. The guide covers project/workspace and scheme selection,
build-folder and operation approvals, verification, and independent device
selection for each client and the Mac interface.

Private profiles, credentials, hardware identifiers, raw device media/logs,
owner-specific deployment state, internal localization working notes, private
qualification workflow templates and historical reports containing private
machine data are excluded. Previously published sanitized historical reports
are retained. The public release-status page is a privacy-safe summary rather
than a copy of private investigation data.

This publishes source and documentation only. No binary asset is uploaded or
published, no release tag is changed and production appcast.xml stays byte-for-byte
unchanged. Final distribution still needs the attended device gates, notarization,
matching corresponding source and exact final installation/update qualification.

## English presentation and DMG packaging

The repository front page now includes an original conceptual cover illustration
and editable English diagrams for connection and development workflow.
`docs/ARCHITECTURE.md` describes the implemented components and verification
methodology. `scripts/package_dmg.py` creates a signed DMG from an existing
Developer ID app without modifying it or overwriting previous artifacts.
Documentation assets and the packaging script are included in corresponding source.
Binary candidates remain draft-only while release gates are open.

## Verified review corrections

Six deterministic, device-free regression cases reproduced stale post-input
discovery, in-flight live-view admission across update preparation, and Stop
cancelling recording finalization. The fixes require fresh post-send discovery,
fence live handshakes across update/shutdown, and preserve already running
recording cleanup. The corrected source requires a new candidate build.

Validation of the corrected source: 417 Python tests and 28 subtests pass; four
explicit live opt-ins are skipped. The 139 directly affected tests pass.
A geometry fixture now replaces the discovery source rather than bypassing
the real cache/refresh interface. No live device qualification is inferred.

The DMG tooling follow-up keeps app copies and signing in the local build
cache, validates the copied app seal, and copies only finished image bytes to
the requested output before revalidating. This packaging-only correction
does not alter build 25 app/runtime bytes. Its exact tooling source is supplied
alongside the app's embedded corresponding source. Network-volume behavior
requires its own host qualification and is not claimed by the local DMG test.

The final publication helper uses a temporary image on the output volume,
verifies it and atomically exposes the final name without overwriting existing
artifacts. Three injected copy/signature/image failures demonstrate cleanup and
successful retry. [Build 25 packaging report](BUILD25_PACKAGING_20261126.md).
