# Build 8: caret validation from unchanged glyph pixels

Date: 2026-11-10 (Asia/Riyadh). Status: source regression fixed and signed local
candidate verified; **physical repeatability remains unqualified**.

A deterministic Router reproduction found that a standard blue caret beside
unchanged visible text was rejected when OCR included extra blank space in the
text box. The guard measured the caret against the OCR rectangle rather than the
visible glyph edge. Four cases, covering appearance/disappearance at 1x and 3x,
fail against the original guard and pass with the correction. This demonstrates
one image-validation defect; it does not establish the cause of every retained
build 7 physical rejection or resolve AX-induced scrolling.

The opt-in guard now has a bounded pixel check when the OCR edge cannot explain
the caret. Both target crops must identify the same dominant background, and one
frame must contain exactly that background at every changed caret pixel. The
visible glyph edge is measured in that frame. A changed or erased glyph cannot
satisfy that background-only requirement. The existing caret size, position,
blue-color/density and total pixel-change limits remain in force, along with
unchanged context, high-confidence target text, lock/epoch/expiry checks and
explicit action approval. The default `exact` mode is unchanged. No automatic
retargeting or input retry was introduced. A successful pixel-derived caret proof
identifies `caret_edge_source: unchanged_glyph_pixels`.

Seven added regression cases cover the four positive variants and rejection of
blue changes over a glyph, blue erasure at the glyph edge and an unprovable
background. Existing protected/unknown-state, modal, stale-frame, changed-target,
unexplained-pixel and cache-invalidation cases remain passing.

Fresh verification passes 276 Python tests and 28 subtests, with four physical
opt-ins skipped, 37 Swift tests, all 50 catalog contracts and all ten relocated
runtime checks. Build 8 is Developer ID signed; its changed source and tests match
the embedded source, and its runtime guard matches the working code. The ZIP
SHA-256 is `ad303e8f48c523723ae82a47d00152de40d0e7e16a29943bd104f8afaf2998a8`.

Its privately assembled complete-source archive retains 112/112 lock-hashed
Python sdists and assessed native dependencies, with zero source gaps. The TAR
SHA-256 is `7cbe022b5b359c052cdc182740c4508c7f6620a3ec9fa8e1d8cd2da0e6933ffb`.
The embedded source is the packaging snapshot; this follow-up report is separate
qualification evidence. Independent bit-for-bit rebuilding is not claimed.

At the start of this round the iPhone was connected but required a passcode.
A subsequent read-only lock-state query timed out; the final query returned
CoreDevice 4016 because trusted connectivity/power assertions were unavailable.
No new phone input or app
activation was sent. Repeated packaged physical fills, independent app readback
and a physical non-exact text-target proof must still be exercised after the owner
unlocks/reconnects the device. Build 7 physical evidence remains historical.
Build 8 has not been notarized or published; production appcast and draft build 3
assets remain unchanged. Machine-readable receipts are in
[CARET_GLYPH_BUILD8_QUALIFICATION.json](CARET_GLYPH_BUILD8_QUALIFICATION.json).
