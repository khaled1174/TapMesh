# Public source update, 2026-11-14

The standalone application source includes the build 8 glyph-edge correction and
build 9 caret-color safety correction, their negative dispatch regressions and
the native CoreGraphics calibration. Application source corresponds to local
commit `782b79f`. The accompanying qualification documents record later testing;
they do not change the frozen candidate's program bytes.

Final build 9 clean arm64 macOS 15.8.2/26.7.2 native installation and update pass
26 checks. Physical fixture input has eight independently confirmed fill
outcomes and twelve safe refusals. General active-field/caret repeatability
remains partial. See [release status](RELEASE_STATUS.md),
[clean-Mac evidence](CLEAN_MAC_BUILD9_REPORT.md) and
[physical evidence](PHYSICAL_TEXT_BUILD9_REPORT.md).

The public tree contains product sources, build scripts, dependency locks,
tests and selected standalone qualification reports. Private deployment state,
credentials, raw device images/logs, binary candidates and unrelated historical
TNAS reports are excluded. Existing generic advanced-connection utilities remain
source-only tools; they do not include the owner's configured environment.

Public qualification copies replace private machine paths and hardware device
identifiers. The original reports and raw evidence remain unchanged in private
storage. Recorded raw-file checksums refer to those original private artifacts;
public privacy views have their own Git blob identities. The legacy private
deployment appendix is omitted from the public final-qualification view.

This update publishes source and qualification summaries. It does not publish a
binary release or change the production appcast. Draft build 3 and its matching
source remain immutable. Final build 9 ZIP SHA-256 is
`c3eed4e33528dd253487e1e541ad26033e090a64c6d0938e7542318a57878a33`;
its retained complete-source archive SHA-256 is
`5a60a84b52b652c6ddec860c150d29e06be9ffa53b75585a72d288886a9fb85a`.
