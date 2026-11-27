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

All measurements came from the read-only qualification script.

On 2026-11-27, the swipe trial made two attempts to start. Both returned
`approval_required` with delivery state `not_sent`. The Build 25 test profile
had no saved permission for the physical phone. A separate test of the same
signed app saved permission via the device menu for a mock phone.

The owner later approved the physical phone from the Build 25 approval card
on the Mac. The test profile confirmed the persisted grant. The warm-stream
swipe trial initially timed out before observing its reference frame and
sent zero gestures.

After the owner returned the iPhone to the Home Screen, all five swipes were
accepted and changed the live image. The median time from request start to the
first changed decoded frame was 328.9 ms. The five measurements were
781.7, 328.9, 346.7, 326.1 and 313.2 ms. The latency criterion of a median
at or below 250 ms remains unmet. The attended control matrix remains open.
[Five-swipe trial measurements](LIVE_CONTROL_SWIPE_BUILD25.json).

[Raw measurements](LIVE_CONTROL_QUALIFICATION_BUILD25.json) ·
[Signed and notarized candidate](BUILD25_PACKAGING_20261126.md) ·
[Release status](RELEASE_STATUS.md)
