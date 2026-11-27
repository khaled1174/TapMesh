# TapMesh release status

## Developer Preview 0.2.0 source

The repository now contains the current 0.2.0 Developer Preview source and the
[agent/project connection guide](CONNECTING_AGENTS.md) ([العربية](CONNECTING_AGENTS.ar.md)).
It includes the native AI Agents setup screen, per-device agent consent,
Stop/Resume, wired live video, screenshot fallback and the current MCP contracts.
This source publication does not publish a binary or change the production appcast.

Build 23 was staged locally from source revision `c61f995` with Developer ID
signing, hardened runtime, the configured updater and matching corresponding
source. Local launch, relocated services and archive-integrity checks passed.
The current development source also includes SDK/actor compatibility fixes added
after that staged candidate; it is not a claim that build 23 contains those fixes.

The [Build 25 follow-up](#build-25-packaging-follow-up) records the current
candidate's wired iPhone measurements and clean-macOS installation and update
checks. The attended control matrix and Finder first-open remain open.

See [0.2.0 behavior and limits](RELEASE_NOTES_0.2.0.md),
[build instructions](SOURCE_BUILD.md), [tool contracts](TOOL_CONTRACTS.md) and
[publication scope](PUBLIC_SOURCE_UPDATE_20261126.md).

## Physical swipe follow-up

The development source now validates physical swipe, drag and pinch
coordinates and maps them to native input using one current display read.
The same observation invalidation and lock checks still run before input.
Automated tests cover the single read and safe refusal on display changes.
The physical latency of a rebuilt candidate remains to be measured.

## Build 25 packaging follow-up

Build 25 is prepared from application source `b66f8b4`. The app and DMG are
Developer ID signed, Apple-notarized and stapled, with Gatekeeper acceptance.
The final DMG matches the candidate app after a read-only mount. Ten relocated
runtime checks and six native development-Mac installation smoke checks pass.
Complete application/dependency source and exact final packaging-tool source
accompany the draft release. [Artifact-bound packaging report](BUILD25_PACKAGING_20261126.md).

The English front page includes the MCP architecture, project workflow and a
conceptual cover illustration. Six verified runtime regressions and three DMG
publication-failure cases are corrected and retained as evidence. Build 24 was
superseded before upload; its Apple acceptance does not qualify build 25.

The Build 25 wired iPhone trial passed the read-only checks on 2026-11-26.
The first video frame arrived in 1,405.9 ms, the stream delivered 60.5 fps over
10 seconds, and the median of 20 screenshots was 108.4 ms. The viewer count
returned to zero after disconnect. [Wired iPhone report](LIVE_CONTROL_QUALIFICATION_BUILD25.md).

The physical iPhone grant was saved through the Mac app. All five USB swipes
changed the Home Screen. The median measured response was 328.9 ms,
above the 250 ms qualification target. The attended physical-control matrix
remains open. [Swipe measurements](LIVE_CONTROL_SWIPE_BUILD25.json).

The exact notarized Build 25 ZIP passed six native installation and seven
Sparkle update checks on each of two clean arm64 hosts, macOS 15.8.2
and 26.7.2. A signed, notarized Build 24 staging copy used a loopback feed
with the production verification key. Both updated apps matched the exact
Build 25 bundle and preserved the default profile's service credentials.
Gatekeeper, signature and ticket checks passed before and after installation.
[Native installation workflow results](https://github.com/khaled1174/TapMesh/actions/runs/46004113752).
Private qualification run `46005364236` records the exact-archive update.

The Finder preflight in private run `46005647187` observed Chrome waiting for
its normal first-open confirmation on both hosted Macs. The browser download
and TapMesh Finder first-open remain untested. Binary release assets remain
draft-only.

## Historical 0.1.0 evidence

Existing build 3/7/8/9 qualification reports and failed or refused attempts are
retained as historical records. They describe their original artifacts, not the
current 0.2.0 source or a new binary release. The current preview's input policy
and permissions are described in its release notes and tool contracts.
Public distribution requires a corresponding-source archive matched to the exact
binary that actually passes the release gates.
