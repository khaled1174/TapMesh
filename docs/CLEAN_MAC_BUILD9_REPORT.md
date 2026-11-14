# Final build 9 clean-Mac installation and update

2026-11-14. **Passed: 26 native checks on two fresh arm64 hosts**, macOS 15.8.2
and 26.7.2. The exact final notarized ZIP was installed, launched, updated to
through Sparkle, independently compared after installation, and quit with owned
services stopped. This closes final build 9's clean-host native install/update
gate. Physical active-field/caret repeatability remains partial, so public
release is still blocked.

Successful private run:
[43593979301](https://github.com/khaled1174/TapMesh-qualification-private/actions/runs/43593979301),
harness commit `f3ee400` (full SHA in the structured receipt). This dedicated
private test repository keeps binary inputs and Actions artifacts private. No
production repository, public release, production appcast or installed user app
was changed.

## Artifact binding and exercised behavior

- Final production ZIP SHA-256:
  `c3eed4e33528dd253487e1e541ad26033e090a64c6d0938e7542318a57878a33`.
- Matching complete source SHA-256:
  `5a60a84b52b652c6ddec860c150d29e06be9ffa53b75585a72d288886a9fb85a`.
  Application source remains commit `782b79f`; no new product build was created.
- Fixed test-fixture ZIP SHA-256:
  `77adc2a77dbf86efd62f3081ad39983c7a7588b75c5465b7b4994ff7a5df3d57`.
- The installed starting point was a disposable fixture with build number 8,
  derived from build 9, carrying a loopback staging feed and the production
  public update key. It is not the historical build 8 binary. The fixture was
  re-sealed with the same Developer ID team and independently notarized under
  submission `7bfaaabb-b6b5-4217-8845-36702114374d`; Apple's log has no issues.
- The update target was the **unchanged final production ZIP**, with its existing
  production Ed25519 signature. Independent public-key verification passed; no
  private update key was exported and no production feed entry was added.

| Requirement | macOS 15.8.2 arm64 | macOS 26.7.2 arm64 |
| --- | --- | --- |
| Exact ZIP digest and isolated Applications installation | Passed | Passed |
| Developer ID, stapled ticket and Gatekeeper | Passed | Passed |
| Visible native workspace; authenticated 50-tool HTTP catalog | Passed | Passed |
| Native MCP attachment and credential preservation | Passed | Passed |
| Cooperative quit, stopped listeners and preserved signature | Passed | Passed |
| Native Sparkle download, verification, installation and relaunch | Passed | Passed |
| Three credentials and binary canary retained; update fence cleared | Passed | Passed |
| Old instance stopped; new healthy instance started | Passed | Passed |
| All 14,795 bundle entries match final ZIP-extracted expectation | Passed | Passed |
| Updated app signature/ticket/Gatekeeper/modes and final cleanup | Passed | Passed |

Each host passed six installation checks and seven update checks. Two additional
input-digest receipts separately bind the inputs. The bundle comparison checks
the entire path set, regular file hashes/sizes, directory/file modes and symlink
targets. It excludes timestamps and extended attributes. Server request receipts
confirm complete appcast and update downloads on both hosts. The final native
quit stopped the updated runtime, and both loopback fixture servers closed.

## Retained initial failure and correction

The initial private run
[43593434081](https://github.com/khaled1174/TapMesh-qualification-private/actions/runs/43593434081)
failed strict resource-seal verification on both hosts, before app launch; native
updates were skipped. Those failed reports and logs remain evidence.

The test workflow invoked the bundled Python interpreter without suppressing
bytecode writes. A controlled local comparison on two freshly extracted copies
reproduced the failure: ordinary imports added 681 `__pycache__` paths and broke
the signature; the same imports with `-B` changed zero paths and preserved it.
The corrected workflow uses `-B` and checks the signature directly before
interpreter execution. Fresh hosts then passed with the same production ZIP.
No product-signature check was bypassed and no product code was changed.

The preparer's earlier direct import also altered cache files in the retained
local app copy. Only cache paths differed from a fresh extraction. The altered
copy was retained privately, and the canonical app was restored from the frozen
ZIP; full tree equality, strict signature, ticket and Gatekeeper pass. Original
ZIP and complete-source digests never changed. The fixture's first expected
manifest had included those added caches; it was corrected to the fresh
ZIP-extracted expectation before any update execution. The installed staging
fixture includes preparer caches covered by its own valid Developer ID seal and
Apple acceptance; the final installed target matches the original production
bundle exactly.

The initial fixture archive (`9c7b751d2b886ba9611f46f53f4be830e6b8eb36ab0e7336b84effcc8ec1446b`)
is retained separately. An initial lookup of nonexistent Keychain profile
`TapMesh-Notary` is also retained; the existing project profile `TapMesh` was
used successfully, without creating or exporting credentials.

## Preservation and limits

Private temporary draft assets and the two private binary-input Actions artifacts
were removed after successful report download. Local inputs, failed attempts,
diagnostic copies, logs and report hashes remain retained. The private harness
and report history remain available. The original installed Mac app's binary,
Info.plist and resource-seal hashes are unchanged. Public draft build 3 assets
and the production appcast have matching before/after fingerprints. The worktree's
appcast is also unchanged. No phone, simulator, personal app or TNAS operation
occurred.

The clean-host first-open Finder confirmation and AppTranslocation UI were not
exercised; the native application entrypoint was. The staged baseline proves the
installer/handoff path to final build 9, not every historical data migration.
Intel, every OS patch and independent bit-for-bit source rebuilding are not
certified. The physical input limitations in
[PHYSICAL_TEXT_BUILD9_REPORT.md](PHYSICAL_TEXT_BUILD9_REPORT.md) remain open.

Four successful native reports, two initial failures, complete request outcomes,
artifact bindings, preservation receipts and raw-file hashes are in
[CLEAN_MAC_BUILD9_QUALIFICATION.json](CLEAN_MAC_BUILD9_QUALIFICATION.json).
Private raw evidence:
`<private-tapmesh>/qualification/CleanMac-Build9-20261114T120234Z`.
