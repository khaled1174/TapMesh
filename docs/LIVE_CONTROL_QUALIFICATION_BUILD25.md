# Build 25 wired iPhone qualification

The read-only trial passed on 2026-11-26 using TapMesh 0.2.0 Developer Preview
Build 25 from application source `b66f8b46cd772dec00ce1559a91f6daeb56371e4`.
The iPhone was connected over USB and played moving content during the
10-second live sample.

| Check | Measured result | Pass criterion |
| --- | --- | --- |
| First live frame | 1,405.9 ms | At most 2,000 ms |
| Live frame rate | 60.5 fps, 605 frames in 10 seconds | At least 50 fps |
| Viewer cleanup | 0 → 1 → 0 viewers | Return to zero when the test viewer disconnects |
| Screenshot latency | 20 screenshots, 108.4 ms median, 275.8 ms p90, 344.6 ms maximum | Median at most 250 ms |

All measurements came from the read-only qualification script. The owner
deferred the five Home Screen swipes, and the attended physical-control matrix
remains open for this candidate.

[Raw measurements](LIVE_CONTROL_QUALIFICATION_BUILD25.json) ·
[Signed and notarized candidate](BUILD25_PACKAGING_20261126.md) ·
[Release status](RELEASE_STATUS.md)
