# Build 25 packaging and draft delivery — 2026-11-26

TapMesh 0.2.0 Developer Preview build 25 is a signed, Apple-notarized candidate.
The application and DMG have stapled tickets and pass Gatekeeper. Public binary
publication remains blocked by the attended physical-control matrix and exact
final-artifact clean-Mac installation/update and Finder first-open checks.
The production appcast is unchanged.

## Source and review

Application source: `b66f8b46cd772dec00ce1559a91f6daeb56371e4`.
DMG assembly tooling: `484942cf004b1dc397206434c4ba737907d5784a`.
The final atomic-publication helper is included in the companion packaging-source
archive; its exact revision and hash are recorded in the release manifest.
This tooling-only follow-up changes no app, Swift or Python runtime bytes.

Six regressions reproduced cached post-input discovery, live admission across
update preparation, and Stop cancelling recording finalization. The fixes pass
417 Python tests and 28 subtests, with four explicit live opt-ins skipped.
Three additional fault-injection cases reproduce failed DMG copies/verification
leaving a final path; the corrected helper keeps unverified bytes under a private
temporary name, cleans failures and permits retry. Copied application seals and
finished image copies are verified before the final name is exposed atomically.
Unsupported output-volume link operations refuse safely; network-volume behavior
was not qualified in this local run.

Build 24 was accepted by Apple, then superseded before upload when the six
runtime regressions were demonstrated. Its receipts remain separate. Historical
failed and refused trials are not converted into passes by these checks.

## Exact artifact checks

- All 186 embedded source files and the bundled project Python modules match
  the application source snapshot.
- The source collector retains 112 exact Python source distributions and the
  checked native dependency evidence, with zero reported source gaps. This is
  source-closure verification, not an independent reproducible binary rebuild.
- Ten relocated-runtime checks pass, including authenticated local HTTP, OCR,
  MCP stdio, proxy isolation, restart and cooperative service shutdown.
- The read-only DMG mount and copied application match the signed candidate.
  Its Applications shortcut is correct; mounted app signatures and ticket pass.
- Six native-entrypoint installation smoke checks pass on the development Mac.
  They include visible workspace, local services/MCP and stopped owned listeners.
  They do not qualify clean Finder first-open or update installation.
- The final stapled DMG is mounted again read-only and still contains the exact
  same application bundle.

| Artifact | SHA-256 |
| --- | --- |
| `TapMesh-0.2.0-build25-macos-arm64.zip` | `58f29a1610f45cada1faae56a174280e00cbd5ee8acd0c2e00448c18ed528c2f` |
| `TapMesh-0.2.0-build25-macos-arm64.dmg` | `b206ada9616094d1d49586a57881c69f840531b65c45600b04e93f453a421460` |
| `TapMesh-0.2.0-build25-complete-source.tar` | `9521d5a5f6c119e0db0270116e244d907ea97a0ffa1e35919d1111ab1429403d` |

App Apple submission: `f2b7f697-b999-44fc-b989-315bcd4bf2a0`.
DMG Apple submission: `a53f38de-81f7-456b-a8a1-b7442b5228dc`.

Artifacts are uploaded only to an owner-visible GitHub draft candidate. The
release manifest and SHA256SUMS bind the exact uploaded files. No device input
or reset was performed during this packaging round. The installed user app and
private connection environment were not replaced.

The English front page includes a conceptual cover illustration and editable
architecture/workflow diagrams. These are explanatory visuals, not product
screenshots or evidence of a physical-device pass.

[Connection guide](CONNECTING_AGENTS.md) · [Architecture](ARCHITECTURE.md) · [Release status](RELEASE_STATUS.md)
