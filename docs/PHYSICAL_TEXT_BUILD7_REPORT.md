# Physical text qualification: signed build 7

Date: 2026-11-09 (Asia/Riyadh). Verdict: **partial; public release gate remains open**.
The connected, paired, wired iPhone 18 Pro Max runs iOS 27.2 with Developer Mode
enabled and no passcode required. All device work was confined to the installed
`io.tapmesh.fixture`. The exact Developer ID signed build 7 ZIP matches
`d168406ab14d0afb7e432b8a3a0a18b989478143bb299137d3890bff3dea08a2`.
The 14 recorded changed source files match both the working source and embedded
source archive; the Python/contract runtime copies also match. No product code,
safety tolerance or approval boundary was changed during this qualification.

## Outcomes

- Eleven HTTP coordinate-fill attempts were retained: four transmitted and seven
  rejected with `stale_observation` / `not_sent`. Two clipboard replacements have
  independent English field and `Entered:` readbacks. A Unicode clipboard
  replacement is visually confirmed in both app locations; exact Arabic OCR is
  inconclusive. The fourth transmitted attempt used an operator-assumed US
  keyboard declaration while the actual keyboard was Arabic, and produced Arabic
  text instead of the expected English. That setup error is not passed keyboard
  evidence. The fixture was reset afterward.
- The default `exact` request rejected a changed view before input. The opt-in
  requests retained `large_pixel_change`, `unexplained_pixels` and
  `target_changed` rejections. Every transmitted coordinate fill reported
  `target_validation.mode: exact_frame`; transmission after a non-exact
  `stable_text_target` proof is **not** qualified by this round.
- The native replacement sheet starts with the new option unchecked. Clipboard,
  ordinary-field confirmation and the option were selected interactively. Its
  dispatched `fill` request safely rejected a changed view before input. A later
  native focused clipboard insertion appended ` Focus`; independent app
  screenshot/OCR readback after Backspace shows ` Focu` in both the field and its
  `Entered:` mirror. This does not establish generic coordinate-fill repeatability.
- A previously issued physical observation, older than 60 seconds, was explicitly
  rejected with `stale_observation` / `not_sent` after restoring the fixture.
  A separate 31-second probe stopped at its initial unidentifiable capture,
  before its wait or any fill; it is not counted as an executed expiry check.

## Diagnosis and retained unsuccessful work

The physical AX sensitivity audit can scroll this long fixture. A direct Router
diagnostic using the packaged production classes and an error-only wrapper
recorded a changed viewport and `ambiguous_current_target`, then stopped when the
title marker was absent. A later public OCR read identified the same fixture's
rows farther down the page. This is not evidence that another app was opened.
The direct diagnostic is kept separate from HTTP/native-interface qualification.

Read-only captures around the packaged AX audit isolated a 432-pixel vertical
strip at the text caret. The initial three comparisons rejected two strips and
accepted one unchanged image. A subsequent three detailed comparisons accepted
the standard blue caret. Those algorithm observations do not resolve the
intermittent public-dispatch rejections or certify physical HID after a soft proof.

The first public inspection exceeded its deadline, followed by a successful read.
Two planned chain commands stopped because their expected preceding field value
was absent after a not-sent rejection; neither sent input. An earlier focused
sequence after Escape transmitted commands but did not obtain the expected
visible readback and is inconclusive. A separate native attribute-readback probe
could not identify the fixture and stopped without input. A status-bar tap intended
to restore the fixture viewport rejected a changed frame before input. Every
recorded attempt is retained in the companion JSON; none is silently retried or
reclassified as success.

## Automated, runtime and source evidence

Fresh local verification passes 50 catalog schemas, **269 Python tests and 28
subtests**, with four physical opt-ins skipped, and **37 Swift tests**. The explicit
physical work above is separate from those skips. A fresh relocated-runtime round
passes all ten checks: system-only PATH, private profile/authentication, Vision
OCR, the 50-tool MCP bridge, duplicate launch, credential-preserving restart,
owned-listener shutdown and preserved bundle signatures.

Complete corresponding source for build 7 was assembled privately and its embedded
application source verified. The fresh dependency collector retains 112/112
lock-hashed Python sdists and the assessed native dependency sources with zero
source gaps. Its first invocation used the wrong native-evidence manifest path
and failed; the corrected offline invocation passed. The complete archive is
`TapMesh-0.1.0-build7-complete-source.tar`, SHA-256
`943cd3a92346e7abd3d045d5b72532fd7d6b9891c547601b992e7fbcc249efed`.
Independent bit-for-bit rebuilding is not claimed.

The fixture was reset to its default state, the test Mac window quit, owned
listeners stopped and the bundle signature remained valid. The pre-existing
installed Mac app was left running. The single temporary fixture PNG was removed;
diagnostic pixel comparisons retained numerical evidence only. No global GUI
environment setting, private TNAS configuration, production appcast or draft
build 3 binary/source asset was changed. Build 7 was neither notarized nor published.

The next release work is a repeatable physical text-target workflow that preserves
the existing protected/unknown-state, frame and approval checks, followed by final
candidate notarization and exact-artifact clean-host installation/update tests.
The complete-source availability gap for build 7 is now closed; the physical
repeatability and public-distribution gates remain open. See
[the machine-readable evidence](PHYSICAL_TEXT_BUILD7_QUALIFICATION.json).
