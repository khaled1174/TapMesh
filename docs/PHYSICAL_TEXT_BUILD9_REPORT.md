# Final build 9 physical text-input qualification

2026-11-14, Asia/Riyadh. **Partial; public release remains blocked.** The final
notarized build 9 ran against the owner's wired, unlocked iPhone 18 Pro Max on
iOS 27.2.1, using only `io.tapmesh.fixture`. Eight fill transmissions have
independent fixture readbacks. Twelve other authorized fill attempts stopped
before HID/clipboard input. These are distinct planned cases and retained safe
refusals, not a general reliability estimate. Repeated active-field replacement
and physical caret dispatch remain unqualified.

The previous missing physical non-exact dispatch evidence is now present: one
fresh OCR element reference resolved to coordinates, accepted 2,942 changed
native pixels under `stable_text_target` / `clock_status`, transmitted input,
and independently read back `TapMesh Reference One` in both the field and its
`Entered:` mirror. This single success does not qualify repeated caret changes.
No production source, security exception or numeric limit changed this round.

## Candidate and readiness

- Final ZIP SHA-256: `c3eed4e33528dd253487e1e541ad26033e090a64c6d0938e7542318a57878a33`.
- Complete corresponding source SHA-256: `5a60a84b52b652c6ddec860c150d29e06be9ffa53b75585a72d288886a9fb85a`.
- Source commit `782b79f`; documentation HEAD before this round `b3cba48`.
- Apple submission `a64b44d8-8076-40ac-bba4-57f537724f49` remains accepted.
  Fresh strict signature, stapled-ticket and Gatekeeper checks pass. The
  production update signature was verified in the retained November 11 receipt.
- Before documentation edits, 130 embedded source files were compared with the
  worktree; only the intentional packaging-time `RELEASE_STATUS.md` snapshot
  differed. All 22 bundled runtime Python source copies matched. Archive hashes
  are unchanged. Later qualification documents are separate receipts.
- Read-only discovery confirms wired pairing, Developer Mode, usable developer
  services and `passcodeRequired=false`. The existing developer-built fixture
  version 0.1.0 (1) was explicitly activated and identified before capture/input.
  Portrait geometry was 440×956 logical points, 1320×2868 native pixels.

The owner said they would hide the live Dynamic Island counter by swiping it;
later fixture captures show it hidden. The agent did not operate or terminate
the personal activity. The counter has not been established as the sole cause
of earlier frame differences. No passcode, authentication bypass, personal-app
inspection or device/simulator reset was performed.

## Executed fills and independent outcomes

The private input-history export retains 58 rows. There were **20 executed
post-authorization fills: eight successful transmissions with eight independent
app outcomes, and twelve safe refusals** (eleven `stale_observation`, one
`stale_element_reference`). Twenty separate `approval_required` fill responses
are excluded from that count. Setup failures and the later rejected focus tap
are also excluded. Every transmission result alone still reports `verified=false`;
the evidence below comes from a separate observation of the fixture.

| Successful case | Requested validation / retained proof | Independent outcome |
| --- | --- | --- |
| `TapMesh Exact` | Default exact / `exact_frame` | Fresh capture/OCR of field and `Entered:` mirror |
| `TapMesh تجربة` | Explicit text_target / `exact_frame` | Named AX field Value and exact mirror with SwiftUI directional wrappers |
| `TapMesh Nominal One` | Default exact / `exact_frame` | Named AX field and mirror |
| `TapMesh Nominal Two` | Explicit text_target / `exact_frame` | Named AX field and mirror |
| `TapMesh اختبار` | Explicit text_target / `exact_frame` | Named AX field and mirror |
| `TapMesh Clock Proof` | Explicit text_target / `exact_frame` | Named AX field and mirror; capture already crossed the minute, so no non-exact proof |
| `TapMesh Native Quick` | Native opt-in selected / proof body not retained | Visible native preview plus separate fresh HTTP capture/OCR of field and mirror |
| `TapMesh Reference One` | Explicit text_target / `stable_text_target`, `clock_status`, 2,942 native pixels | Fresh capture/OCR of field and mirror |

Four prepared fresh-fixture cases passed four of four: each relaunched only the
disposable fixture, completed an explicit AX inspection **before a new anchor**,
and still ran the ordinary production safety checks. All four used exact-frame
proofs. This prepared-state result does not certify continuous active editing.

The seventeen HTTP fill bodies retain two default-exact requests and fifteen
explicit text_target requests. Their ten refusals comprise two `target_changed`,
five `unexplained_pixels`, one expired observation before frame comparison, one
`large_pixel_change` and one `unqualified_caret`. The three additional native UI
fills retained two safe refusals and one success. Their full response bodies and
detailed frame reasons were not captured; no proof mode is inferred for the
successful UI fill. The structured report lists all twenty request IDs and
outcomes, including unsuccessful attempts.

## Focused input, Arabic and the Mac option

Focused clipboard append ` Focus` and Backspace have fresh, independent visible
readbacks: `TapMesh Exact Focus` then `TapMesh Exact Focu`, in the field and
mirror. OCR confirms visible text, not invisible whitespace bytes. A later paste
of `s` visibly produced `TapMesh Exact Focu s`; literal concatenation is not
passed, and its cause is not established. A following expected-target lookup
stopped without fill dispatch. `hide_keyboard` was delivered but reported
`unverifiable`; a later capture still showed the Arabic keyboard. It is not a
passed keyboard-hiding outcome.

Focused Select All/Delete was followed by an independent empty-field capture
before Unicode paste. Exact AX readbacks then confirm `TapMesh تجربة` and,
after Backspace, `TapMesh تجرب`. Arabic coordinate replacement is also confirmed
by exact AX Value and the named `Entered:` mirror. SwiftUI's FSI/PDI display
wrappers are retained and explicitly matched; they are not inserted into the
field. These identifier-bound reads report unknown roles and null bounds, so
they do not prove generic editable AX semantics. Variable Arabic OCR alone is
not treated as failed input. The observed keyboard remained Arabic; no current
US-layout declaration or US hardware-keyboard qualification is claimed.

In the final notarized Mac app, the new allowance checkbox was observed
unchecked, explicitly checked, and used with Clipboard and ordinary_app. Native
attempts for `TapMesh Native Nine` and `TapMesh Native Fresh` safely refused;
`TapMesh Native Quick` showed `Fill · success` and passed independent readback.
UI selection and unchanged compiled serialization are evidence of the option
path; the actual HTTP payload was not independently retained. Two CUA harness
setup errors (diff-prefix parsing, then an undeclared variable) occurred before
the third dispatch and sent no phone input. Only the harness was corrected.

## Retained refusals and investigations

- Reusing the original expired observation for `MUST_NOT_BE_TYPED` returned
  `stale_observation/not_sent`; no replay was sent.
- A separately controlled minute-boundary request returned
  `large_pixel_change/not_sent`. It remains a refusal alongside the later
  successful 2,942-pixel clock/status dispatch.
- Subsequent fresh references for `Reference Two` and `Reference Three` refused
  unexplained pixels and an unqualified caret. No automatic input retry or
  retargeting was introduced.
- Two setup operations stopped because the fixture marker could not be
  confirmed, and one stopped on an expected-target assertion. Marker absence
  does not prove which application was foreground. No fill was sent by these
  failed setups. A concurrent read-only OCR probe returned HTTP 504; subsequent
  device/OCR reads were serialized. Causation and a product fix are not claimed.
- A read-only 430-native-pixel caret comparison refused input eligibility. Its
  caret edge was about 4.57 logical points inside the OCR text edge, beyond the
  existing four-point allowance; no background-only frame established the
  pixel-derived fallback. A later comparison found 432 caret pixels plus 46
  scattered keyboard-region changes and also refused. Two subsequent 432-pixel
  caret-only comparisons passed `stable_text_target/blue_caret` using the OCR
  edge. **These are pure image comparisons, not HID dispatch evidence.** Fading
  caret phases stayed blue; no unseen-background exception was added.
- AX traversal can shift the viewport. A reverse-inspector-focus probe traversed
  73 elements and stepped Previous; it did not restore an identical frame or
  prove the fields returned. Its next sample stopped on a missing fixture
  marker. No production restoration patch was adopted.
- The optional replacement-field sequence stopped when its focus tap refused
  `frame_changed/not_sent`. Neither planned fill executed; neither is counted.

All historical build 7/8/9 failures and earlier distribution variants remain
retained. There is no evidence here for widening the pixel, context, confidence,
protected-state, authorization or expiry exceptions. Guard frame storage remains
RAM-only. Explicit investigator screenshots of the disposable fixture are kept
privately, separately from that guard; no automatic guard frame logging was added.

## Verification, cleanup and remaining gates

Fresh full automated verification passes **312 Python tests, 28 subtests,
37 Swift tests and 50 tool schemas**, including 2,560 native CoreGraphics color
samples. Four live simulator opt-ins were skipped and are not passes. The
final package's earlier ten runtime, ten helper and six native-launch checks
remain in [NOTARIZATION_BUILD9_QUALIFICATION.json](NOTARIZATION_BUILD9_QUALIFICATION.json).

Only the owned test Mac GUI and its isolated services were closed; stopped
listeners and clean supervisor exit were verified. Only transient fixture state
was reset, and a final independent capture confirmed empty `Enter name`, empty
`Entered:`, `Existing value`, and the fixture still foreground. The installed
Mac app, private TNAS environment, user apps and other devices/simulators were
not operated or reset.

Public binary publication remains blocked by repeated active-field/caret
dispatch qualification and exact-final-artifact clean supported-Mac installation
and native update. The AX viewport effect is unresolved. Development-Mac checks
and historical build 3 clean-host runs do not certify final build 9. No remote is
configured; no public upload, production appcast edit or immutable build 3 asset
change occurred. Signing, notarization and matching complete source are already
available and were preserved.

Machine-readable evidence and top-level raw-file SHA-256 bindings are in
[PHYSICAL_TEXT_BUILD9_QUALIFICATION.json](PHYSICAL_TEXT_BUILD9_QUALIFICATION.json).
The private evidence directory is
`<private-tapmesh>/qualification/Physical-20261114T102053Z`.
Its `Local` credentials/state are excluded from committed evidence. Operator UI
observations are identified separately from captured HTTP bodies; overwritten
temporary previews are not presented as immutable image artifacts.
