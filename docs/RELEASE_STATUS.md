# TapMesh release qualification

Updated 2026-11-05. The public repository contains the standalone local product
source. No notarized public binary release is available yet. The candidate is
Apple Silicon only, with macOS 15 as its minimum deployment target.

| Area | Evidence and current limit |
| --- | --- |
| Portable services | Linux automated contracts and regressions pass in public CI. |
| Supported Mac builds | Public CI passes on macOS 15 and 26, including standalone packaging and relocated local HTTP/MCP runtime execution. |
| Local automated tests | 208 Python tests, 28 subtests and 36 Swift tests pass; four physical opt-in tests are skipped. |
| Runtime independence | Bundled interpreter, packages, companion and native OCR/media helper run with system-only PATH; profile isolation, authentication, restart, shutdown and preserved bundle signatures pass. |
| Native interface | Local first launch, MCP cold start, settings restart and owned service shutdown have been observed on the development Mac. |
| iPhone/iPad simulators | Fresh standalone HTTP/MCP, input, media and hour-long capture qualification are in progress. Explicit accessibility remains limited where idb returns unusable trees. |
| Physical devices | Earlier development slices exist; final standalone physical qualification is deferred until the owner reconnects the devices. |
| Updates | Signature unit and integration checks pass. Disposable native installation, modified-download rejection and interrupted-download qualification are in progress with an ephemeral test key. Production signing-key authorization remains pending. |
| Signing and notarization | Developer ID hardened signatures are verified locally. Apple notarization credentials and successful notarization/stapling are pending. |
| Corresponding source | Dependency source closure and notices have been collected. The final release must ship its matching complete source archive and checksums. Independent bit-for-bit rebuilding has not been performed. |

Simulator evidence does not certify physical-device behavior. A native update on
the development Mac does not certify installation or Gatekeeper acceptance on a
clean Mac. Intel support, every Apple OS/device combination and unsupported
backend operations are not claimed.

The production update feed is
`https://raw.githubusercontent.com/khaled1174/TapMesh/main/appcast.xml`. It remains
empty until a qualified public binary and matching complete source are ready.
Internal staging copies alone may use an explicit literal-loopback HTTP feed
and their own disposable signing key. They do not change the production key or
release feed.
