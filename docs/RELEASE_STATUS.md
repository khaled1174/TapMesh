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

The release gates remain open: the owner-attended physical-control matrix,
latency and landscape/lock interruption checks, Apple notarization, and clean-Mac
installation/update for the exact final ZIP. Earlier attended trials retained
failed latency and landscape outcomes. Source tests and read-only observations
do not substitute for those gates. No new live device qualification is claimed
by this publication.

See [0.2.0 behavior and limits](RELEASE_NOTES_0.2.0.md),
[build instructions](SOURCE_BUILD.md), [tool contracts](TOOL_CONTRACTS.md) and
[publication scope](PUBLIC_SOURCE_UPDATE_20261126.md).

## Build 24 packaging follow-up

A new build 24 candidate is being prepared from this publication branch. Its
English documentation includes the MCP connection architecture, project workflow
and a conceptual cover illustration. The candidate adds no device-control
behavior changes relative to this branch's tested source.

Signed app, ZIP, DMG, notarization, source and checksum receipts are tracked with
the exact draft release artifacts. A build number or Apple acceptance alone is
not a public-release qualification. Attended physical-control and exact-artifact
clean-Mac installation/update remain required. No production appcast update is
authorized while these gates are open.

## Historical 0.1.0 evidence

Existing build 3/7/8/9 qualification reports and failed or refused attempts are
retained as historical records. They describe their original artifacts, not the
current 0.2.0 source or a new binary release. The current preview's input policy
and permissions are described in its release notes and tool contracts.
Public distribution requires a corresponding-source archive matched to the exact
binary that actually passes the release gates.
