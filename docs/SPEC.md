# TapMesh distributed implementation specification

Owner-authorized implementation, 2026-11-05. Implement from this specification and public documentation only. Do not read investigation directories, raw binary evidence, disassembly, strings, vendor assets, or original internal architecture. No claim of strict binary compatibility or completed independent-provenance audit.

## Outcome
Native Mac application to discover, view, inspect, and control iPhone, iPad, and iOS simulators. Manual and MCP calls share tool schemas and device routing. The local owner grants or revokes control and decides approvals. Android is deferred.

The Developer Preview runs the app, agent, and coordinator on the user's Mac.
The TNAS setup below remains available to developers as a custom connection.

## Distribution
- Product root: my-version/devicebridge. Existing reports are read-only.
- TNAS Linux x86_64: authenticated official MCP SDK Streamable HTTP service, artifact storage, metadata/history and portable processing. Pin dependencies. Docker CPU 2 and memory 1GB maximum initially; increase only with evidence.
- Mac: Python device agent using argv subprocess calls to qualified Apple tools and an installed idb_companion for simulator UI operations. Native SwiftUI/AppKit user app talks to the server. Use original UI/branding.
- Physical adapters may use public idevice/pymobiledevice3/WDA. Do not use proprietary apps. A required but unimplemented operation must be visibly unsupported; it never counts as complete.
- NAS service binds 127.0.0.1:48793. Mac agent binds 127.0.0.1:48792. SSH local forward Mac 48793 to NAS 48793 and reverse NAS 48792 to Mac 48792. Docker host network permits loopback agent access. No unauthenticated LAN listener.
- Setup generates separate 256-bit bearer credentials. The Developer Preview keeps the owner approval credential in memory and passes it to bundled services through inherited pipes. The service bearers use private profile files. Development setups can explicitly keep the approval credential in a private file. Verify the SSH host identity for a tunneled connection.

## Interfaces (freeze before parallel integration)
Mac agent HTTP:
GET /health -> {status,version,host,backend_health,instance_id,approval_secret}, where `approval_secret` reports only the mode (`pipe` or `file`).
POST /v1/call, Authorization Bearer agent credential; JSON {session_id,tool,arguments}. Returns a typed tool envelope {request_id,status,device_id?,connection_epoch?,data?,error?}. error has {code,message,retryable,side_effect_state}. HTTP transport status distinct from device errors.
GET /v1/approvals; POST /v1/approvals/{id} with {decision:allow|deny}; local app authorization only. Exact-action approval binds the session, device, connection epoch, and arguments and expires. Policy A grants apply to the device. Protected actions require human handoff.
GET /v1/settings returns `{fast_control, agent_devices}`. POST accepts any nonempty subset of `{fast_control, agent_devices, owner_session}`. Both require the owner credential. The service persists fast control and the list of allowed devices. Effective policy C runs immediately for the authenticated native owner, on simulators, and on allowed iPhones or iPads when fast control is on. Other physical devices require a per-device grant (policy A). `TAPMESH_FAST_CONTROL=0` forces fast control off.
GET /v1/activity reports active or recent external control and holds. POST /v1/hold requires the owner credential and takes `{device_id, held}`. A hold cancels external work and blocks further writes from external clients. It survives a supervised agent restart, including the period before the app registers its owner session again. Resume, a different owner, or a new supervisor instance clears the hold. Read-only actions and live video remain available.
GET /v1/live/{device_id}?session_id=<id>, agent Bearer credential; read-only live video stream (see "Live video stream").
NAS HTTP app:
GET /health (no secret data)
GET /api/devices and GET /api/tools, authenticated server credential
POST /api/call {session_id,tool,arguments}, same envelope; UI and MCP call same route.
GET /api/approvals and POST /api/approvals/{id} relay to the agent with owner authorization. GET/POST /api/settings and POST /api/hold use the same owner authorization. GET /api/activity uses the service credential. Native tool calls present both credentials to authenticate the registered owner session.
GET /api/artifacts/{artifact_id} authenticated, validated opaque IDs, no arbitrary paths.
GET /api/storage returns `{count, bytes}` for coordinator artifacts. POST /api/storage/clear requires the owner credential and clears the device agent's and coordinator's artifact stores. The response reports deleted coordinator files and bytes freed. Copies explicitly exported to a user folder stay in that folder.
GET /api/live/{device_id}?session_id=<id>, authenticated server credential; unbuffered relay of agent GET /v1/live/{device_id}?session_id=<id> (agent credential). See "Live video stream".
MCP at /mcp with official SDK and bearer auth; stable schemas from catalog; per-client selected device; initialized sessions; progress/cancellation, no stdout logs in stdio bridge.

## Live video stream
`GET /api/live/{device_id}?session_id=<id>` (server Bearer) is relayed without buffering to `GET /v1/live/{device_id}?session_id=<id>` (agent Bearer). The relay has a normal connect timeout and no read timeout; a client disconnect closes the upstream request promptly, and the end of the upstream stream ends the client response.

Authorization is the same as `take_screenshot`: a valid session id, a discovered device, its `screen.capture` capability and that tool's policy (O). Viewing needs no control, takeover, approval or ownership lock, records no history entry, never opens HID and never sends input to a device.

Before streaming, HTTP 401 means the credential is missing or invalid, 400 means the `session_id` is missing or invalid, and 404 means the device is unknown. The server returns HTTP 409 with `live_unavailable` and a more specific `details.reason` when live video cannot start. This includes an update in progress, a device without live support, and an attempt to open a ninth viewer (`too_many_viewers`). Clients fall back to screenshots on any non-200 response.

Success: HTTP 200, `Content-Type: application/vnd.tapmesh.live`, `Cache-Control: no-store`. The body is a sequence of messages `[type: UInt8][length: UInt32 BE][payload]`:
- `0x01` FORMAT: UTF-8 JSON `{"version":1,"codec":"hevc","coded_width":W,"coded_height":H,"crop":{"x","y","width","height"}}`. `coded_*` is the SPS picture size (the decoded buffer size, padding included, e.g. 1328x2896); `crop` is the display content box in coded pixels, the same box screenshots are cropped to. Sent first and again when the coded size or crop changes (rotation).
- `0x02` PARAMS: VPS, SPS and PPS NAL units without start codes, each prefixed by a 4-byte BE length, in that order. Sent after FORMAT, whenever any of them changes, and always before a viewer's first FRAME.
- `0x03` FRAME: `[flags: UInt8, bit0 = keyframe][pts_us: UInt64 BE, agent monotonic clock][AVCC: 4-byte BE length-prefixed NAL units]`, one complete access unit; never contains VPS/SPS/PPS. Access units damaged by packet loss are never sent; after loss nothing is sent until the next keyframe (requested with RTCP FIR).
- `0x04` HEARTBEAT: empty; sent at least once per second while no FRAME was sent in the last second (a static screen produces no frames). Clients treat more than 3 s of silence as a dead stream.
- `0x05` END: UTF-8 JSON `{"reason":...}` just before the agent closes the stream: `device_disconnected`, `stream_ended`, `stream_closed`, `session_closed`, `replaced` (another device took the single USB tunnel), `shutdown`, `update`, `stalled`.

Only wired USB devices have the stream. The same DisplayService stream provides the latest decoded frame for screenshots and supports HID touch. A viewer's first FRAME is a keyframe preceded by FORMAT and PARAMS. The coordinator relays up to eight viewers. Each has a queue of up to 30 messages or 4 MiB. A viewer that falls behind drops its queued messages and resumes at the next keyframe with FORMAT and PARAMS again. The stream counts as in use while a viewer is connected. The 60 s idle rule applies after the last viewer leaves. A viewer that reads nothing for 15 s ends with `stalled`. The service ends a stream that receives no RTP packets for five seconds with `stream_ended`.

## Core invariants
See TOOL_CONTRACTS.md for all 50 names and proposed inputs. Do not infer device selection from having only one connected device. Session A selection never changes B. Bind connection epoch and observation geometry; stale or cross-session refs fail. Explicit per-device mutation ownership across awaits. Cancel work and release touches/owned processes. A delivered input is not business success; post-send disconnect is unknown and never automatically retried. OCR boxes are labeled OCR and do not invent AX roles. Batch preflight one device; bounded, no nesting/build/handoff/lifecycle/recording changes, ordered partial results, no rollback claim. Restrict file exports/build paths via configured grants; reject traversal/symlinks. Distinguish unsupported from test pass.

## Product UI
Native sidebar (devices), device canvas, inspector and apps/builds panes, connection/server status, recording/artifacts view, activity, pending approvals and handoff controls, settings/diagnostics. Live preview polling is bounded and stops when hidden/disconnected. Mouse taps use explicit image-to-point geometry. Keyboard and accessibility navigation. Light/dark/reduced motion. Clear prerequisite explanations.

## Qualification and delivery
Use a dedicated disposable project-owned simulator/test app, never reset the user's simulators or remove their apps. Discover physical devices read-only before operations. Public release requires signing/notarization and clean-Mac update tests; do not pretend debug/ad-hoc packaging meets that. Every tool needs a real route on at least one applicable backend and success/negative evidence before v1 complete. No stubs marketed as finished. Maintain compatibility/evidence ledger with confirmed/partial/unverified/failed.

Keep diagnostic logs bounded and redact private contents. Maintain the README in English and Arabic and technical documentation in English. See the [distribution gates](PRODUCT_SCOPE.md) for the exact build and integration evidence required for release.
