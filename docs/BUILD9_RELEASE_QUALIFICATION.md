# TapMesh 0.1.0 build 9 qualification

The exact Developer ID ZIP has SHA-256
`c3eed4e33528dd253487e1e541ad26033e090a64c6d0938e7542318a57878a33`.
The matching complete source TAR has SHA-256
`5a60a84b52b652c6ddec860c150d29e06be9ffa53b75585a72d288886a9fb85a`.
Fresh checks on the extracted ZIP passed strict code-signature, stapled-ticket
and Gatekeeper validation. The embedded production update key verifies the
signature over those ZIP bytes.

On November 20, fresh local checks passed 312 Python tests, 28 subtests,
37 Swift tests and 50 tool contracts. Four explicit live simulator opt-ins
were skipped. These automated checks do not certify physical input.

The complete source includes the exact embedded application archive, 112
lock-hashed Python source distributions and reviewed native dependency sources,
licenses and build materials. An offline archived application build reused
a retained Sparkle SDK and Python test dependencies. Selected native executable
sections matched. Full dependency rebuilding and byte-for-byte distribution
reproduction remain unverified. The first offline build hit a nested sandbox
restriction. The added test sandbox prevented the Python process observer and
affected native OCR. The affected cases passed in targeted reruns after the
relevant test restriction was removed.

The exact ZIP passed 26 install and native update checks in total across two
clean Apple silicon hosts running macOS 15.8.2 and 26.7.2. The update started
from a build-number-8 fixture made from build 9 code. The automated launch
does not establish Finder's first-open confirmation.

Prepared portrait fixture trials recorded two non-exact blue-caret fills
with matching field and entered-text readbacks. Other trials recorded safe
refusals and lower-confidence OCR. Native Arabic replacement attempts
encountered frame refusals before input was sent. Focused English and Arabic
entry successes exercise a separate path. Generic active-field repeatability
and clean-host Finder first-open confirmation remain open in this snapshot.

The later coordinated round confirmed focused English insertion, Backspace
and clearing independently. Four ordinary English/Arabic replacements, one
native Mac replacement and one aged-observation negative all stopped before
input. The aged negative passed its safety criterion; the ordinary replacement
gate remains partial. Intermittent Vision deadlines were retained without an
unproved language-specific cause. No product code or safety limit changed.

See [source build instructions](SOURCE_BUILD.md) and the
[clean-Mac install and update report](CLEAN_MAC_BUILD9_REPORT.md).
