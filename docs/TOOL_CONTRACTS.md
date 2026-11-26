# TapMesh independently specified tool contracts

These are project requirements and new schemas. They are not a recovered vendor API. The owner authorized implementation on 2026-11-05.

## Shared rules

- Explicit insertion extension: `type_text` and `fill` accept `input_method: "keyboard"|"clipboard"` (default `keyboard`). Clipboard insertion is selected explicitly, requires sensitive policy S, reports clipboard replacement and possible Universal Clipboard propagation, and never runs as an implicit fallback. Backend capability limits still apply. Protected paste prompts require human handoff.
- Physical keyboard mode can declare the owner-confirmed current `keyboard_layout: "us"`. Use the clipboard method for Unicode on a physical device and obtain that action's approval. TapMesh checks the lock state before physical input and again between typing chunks or during long gestures. The Developer Preview retires the old physical text preflight and pixel comparison. Observe the target before sending input and check the result afterward.

- Unless a row says otherwise, a device-bound tool accepts `device_id?: string`. Omission resolves only to that caller's selected device; otherwise return `device_not_selected`. Never choose a device solely because it is the only connected one.
- Device IDs are opaque and stable within their platform namespace. Simulator tools require simulator IDs; physical tools must not silently select a simulator.
- A common optional `timeout_ms` is bounded by the operation class. Default ordinary deadline: 30,000 ms. The service may return an immediate prerequisite/error before the deadline. A timeout does not prove that a sent mutation failed.
- Treat all objects as closed schemas unless an explicitly versioned extension field is provided. Validate enum values, numeric ranges, strings, output paths, and mutually exclusive fields before any side effect.
- Tool definitions stay stable within a version. Device capability output distinguishes `supported`, `unsupported`, `requires_user_action`, `degraded`, and `unknown`, with reasons and operation-specific limits. Missing prerequisites do not necessarily mean permanent lack of support.
- A response includes `request_id`, `status`, `device_id`/`connection_epoch` when applicable, and result data. Observation results include `observed_at`, source and freshness. Artifact results include metadata and a local reference, not unbounded file contents.
- Successful mutation means the declared completion condition was met; it does not imply that the application's business transaction succeeded. For example, a delivered tap is not evidence that a purchase or login completed.
- MCP protocol errors and execution failures remain distinct. Tool execution errors use `isError: true` plus structured error fields; batch results can report `partial` with accurate per-child outcomes.
- Changing control settings and deciding approvals require the local owner credential. Exact-action approvals bind the canonical action, session, device, connection epoch, and expiry.

### Reusable input/output shapes

| Shape | Proposed contract |
|---|---|
| `Target` | Exactly one of `{ref, snapshot_id}` or `{x, y, coordinate_space: "device_points", observation_id}`; observation ID comes from a screenshot/element response and anchors geometry |
| `TextMatch` | `{text, match: "exact"\|"contains", case_sensitive?, occurrence?}`; default exact, case-sensitive; ambiguity without occurrence is an error |
| `ArtifactDestination` | `{destination?: approved_directory, filename?, overwrite?: false}`; omission uses the app-owned artifact directory; overwrite needs policy approval |
| `Observation` | `{observation_id, snapshot_id?, device_id, connection_epoch, observed_at, source, orientation, logical_size, image_size?, image_to_device_transform?, freshness}` |
| `ArtifactResult` | `{artifact_id, kind, state: "complete"\|"partial", local_path, size_bytes, sha256, created_at, device_id, connection_epoch}`; hash only after finalization |
| `ActionResult` | `{delivery: "not_sent"\|"sent"\|"confirmed"\|"unknown", completion_condition, verified?, observation_id?}`; do not infer app success from input delivery |
| `ErrorResult` | `{code, message, retryable, side_effect_state, suggested_action?, details?}` with sensitive values redacted |

Targets are bound to the issuing client/session. Reconnection, orientation/geometry mismatch, or stale observations invalidate them. A fresh coordinate-only manual UI event uses the UI's current local observation context; an agent must provide its observation ID.

Policy **O** covers observations. **C** covers ordinary control, **S** sensitive access or persistent export, **D** destructive confirmation, **H** host execution, and **U** human handoff. Arguments can raise a tool's policy. For example, clipboard text, non-HTTP URLs, and launch arguments raise C to S. Replacing an installed app raises S to D. A batch takes its strictest child policy.

With fast control on, C runs immediately for the authenticated TapMesh app, for clients on simulators, and for clients on iPhones or iPads the owner has allowed. The first C action on an unapproved physical device returns `approval_required` with policy **A** and scope `device`. The owner can allow that device once. With fast control off, each C action uses exact-action approval. S, D, H, and U always require the owner. A held device returns `device_held` to external clients attempting a write or handoff. Wait for the owner to resume before trying again.

The table uses **L** for the simulator backend and **P** for physical devices. **Both** is subject to the capabilities reported by each device.

## Complete catalog and routing

`?` marks an optional field. Shared `device_id` and `timeout_ms` are omitted from rows unless their behavior differs. Capability keys are proposed internal names.

| # | Tool | Proposed specific input | Result / completion condition | Route and required capability | Policy | Intended backend / phase |
|---|---|---|---|---|---|---|
| 1 | `list_devices` | `include_all_simulators?: false` | Devices, IDs, endpoint types, readiness reasons, selected ID for this client, capabilities; no selection side effect | DeviceService / `device.discover` | O | Both / P2–P3 |
| 2 | `start_simulator` | Exactly one of `simulator_id` or `{device_type_id, runtime_id, name?}`; `select?: true` | Create if explicitly requested, boot, await ready; report actual ID and whether created; selection affects caller only | DeviceService / `simulator.boot` | C | L / P2 |
| 3 | `shut_down_simulator` | `device_id?` | Request shutdown, confirm not booted, invalidate live observations; retain simulator definition | DeviceService / `simulator.shutdown` | C; active-job warning | L / P2 |
| 4 | `select_device` | `device_id` required | Update caller selection after identity validation; return readiness; no auto-boot, reconnect or substitution | SessionService / `device.select` | O | Both / P1 |
| 5 | `get_device_info` | `refresh?: false` | OS/model, readiness, connection epoch, capability/limit matrix, screen geometry if available; redact unnecessary identifiers | DeviceService / `device.info` | O | Both / P2–P3 |
| 6 | `take_screenshot` | `format?: "jpeg"`, `max_width?: 1280`, `quality?: 0.8`, `require_fresh?: true` | MCP image plus observation metadata and transform; no persistent file by default | ScreenService / `screen.capture` | O | Both / P5 |
| 7 | `save_screenshot` | `format?: "png"`, `full_resolution?: true`, `destination?`, `filename?`, `overwrite?: false` | Finalized image artifact and dimensions; never silently overwrite | ScreenService / `screen.capture` + artifact storage | S | Both / P6 |
| 8 | `start_recording` | `destination?`, `filename?`, `max_duration_seconds?: 600`, `resume_after_reconnect?: false` | Recording ID and state after writer starts; one active recording per device; video only initially | ScreenService / `screen.record` | S | Both / P6 |
| 9 | `stop_recording` | `recording_id` | Finalize all segments; return segment artifacts, gap manifest, actual durations and partial errors; repeat returns prior final result | ScreenService / `screen.record` | S, scoped to recording | Both / P6 |
| 10 | `get_foreground_app` | No extra fields | Bundle identifier/name where exposed; `unknown` if not observable; do not guess from OCR | AppLifecycleService / `apps.foreground` | O | Both / P5 |
| 11 | `list_elements` | `source?: "auto"\|"accessibility"\|"ocr"`, `max_elements?: 200`, `include_invisible?: false` | Snapshot/observation IDs, element refs, bounds, source, truncation; OCR lacks fabricated roles/IDs | ElementService / `ui.inspect` or `ui.ocr` | O | Both / P5 |
| 12 | `tap` | `target: Target` | Delivered touch action with bound geometry; no business-success assertion | InteractionService / `input.tap` | C | Both / P5 |
| 13 | `tap_text` | `match: TextMatch`, `source?: "auto"` | Obtain a fresh snapshot, resolve unique match, tap; return chosen match/bounds/source | InteractionService + ElementService / `input.tap` + inspection | C | Both / P5 |
| 14 | `double_tap` | `target: Target`, `interval_ms?: 120` | Two taps as one scheduled operation; validate backend interval range | InteractionService / `input.double_tap` | C | Both / P5 |
| 15 | `long_press` | `target: Target`, `duration_ms?: 800` | Hold/release as one operation; release on cancellation when possible | InteractionService / `input.long_press` | C | Both / P5 |
| 16 | `swipe` | `start: Target`, `end: Target`, `duration_ms?: 300` | One continuous swipe in the same observation/geometry; return delivery state | InteractionService / `input.swipe` | C | Both / P5 |
| 17 | `scroll` | `direction`, `distance_points`, `container_ref?`, `snapshot_id?` | Perform bounded scroll in target viewport/container; return fresh observation when available | InteractionService / `input.scroll` | C | Both / P5 |
| 18 | `scroll_to` | `match: TextMatch` or explicit element selector; `direction`, `max_steps?: 8` | Inspect-scroll loop with deadline and no-progress detection; found target or exhausted result; never endless scrolling | InteractionService + ElementService / scroll + inspection | C | Both / P5 |
| 19 | `drag` | `start: Target`, `end: Target`, `hold_ms?: 300`, `duration_ms?: 700` | Press, move and release with continuous ownership; report uncertain delivery if interrupted | InteractionService / `input.drag` | C | Both / P5 |
| 20 | `pinch` | `center: Target`, `scale`, `duration_ms?: 500` | Multitouch scale gesture; reject single-touch emulation when it cannot honor the contract | InteractionService / `input.pinch` | C | Both / P5 |
| 21 | `type_text` | `text`, `target?: Target` | Insert Unicode text at focus, optionally focus explicit target first; no clearing or submit by default; no implicit clipboard fallback | InteractionService / `input.text` | C; sensitive state → U | Both / P5 |
| 22 | `fill` | `target: Target`, `text`, `submit?: false` | Focus, select/clear existing value, enter text, optionally submit; return verification status; fail if safe replace is unsupported | InteractionService / `input.fill` | C; sensitive state → U | Both / P5 |
| 23 | `press_key` | `key`, `modifiers?: []` | Semantic key/chord from capability enum; reject unsupported keys; no host-global keyboard injection | InteractionService / `input.key` | C | Both / P5 |
| 24 | `hide_keyboard` | No extra fields | Use a supported dismissal route; report already hidden, confirmed hidden, or unverifiable; do not tap an arbitrary screen point | InteractionService / `input.keyboard_dismiss` | C | Both / P5 |
| 25 | `press_button` | `button` from device capability enum | Supported device hardware/system-button action; unavailable buttons return unsupported | DeviceSettingsService / `system.button` | C | Both / P5 |
| 26 | `system_gesture` | `gesture` from capability enum, e.g. `home`, `app_switcher`, `control_center`, `notification_center` | Perform named supported system gesture; protected state can require handoff | DeviceSettingsService / `system.gesture` | C or U | Both / P5 |
| 27 | `go_back` | `strategy?: "auto"\|"navigation_element"\|"edge_gesture"` | Use a qualified app navigation mechanism; return chosen strategy or no-supported-back-action; not an Android-style universal back promise | InteractionService / `navigation.back` | C | Both / P5 |
| 28 | `rotate` | `orientation: "portrait"\|"portrait_upside_down"\|"landscape_left"\|"landscape_right"` | Request supported orientation, observe result or explain orientation lock; invalidate old geometry | DeviceSettingsService / `display.rotate` | C | Both / P5 |
| 29 | `set_appearance` | `appearance: "light"\|"dark"\|"system"` | Change supported appearance mode and report observed/effective state | DeviceSettingsService / `settings.appearance` | C | Both / P6 |
| 30 | `set_accessibility` | Explicit patch fields from capability list, e.g. `content_size_category?`, `reduce_motion?`, `bold_text?` | Apply only supported fields; preflight all fields before change; report each effective value; no arbitrary settings key | DeviceSettingsService / `settings.accessibility` | C | Both / P6 |
| 31 | `list_apps` | `include_system?: false`, `query?` | Bounded/paginated app metadata with supported actions; no hidden data extraction | AppLifecycleService / `apps.list` | O | Both / P2–P3 |
| 32 | `launch_app` | `bundle_id`, `arguments?: []`, approved `environment?` | Launch/request activation; report process/foreground confirmation if supported; do not treat request acceptance as verified foreground | AppLifecycleService / `apps.launch` | C; elevated args may require S | Both / P2–P3 |
| 33 | `terminate_app` | `bundle_id` | Stop named supported app; report already stopped or confirmed/requested result | AppLifecycleService / `apps.terminate` | C | Both / P2–P3 |
| 34 | `install_app` | `artifact_path` to approved local app/archive; `replace?: false` | Verify format/platform and install; report bundle ID/version; replacement warning/approval where data could be affected | AppLifecycleService / `apps.install` | S; replacement may require D | Both / P2–P3 |
| 35 | `uninstall_app` | `bundle_id` | Confirm app/data impact, uninstall supported target, verify absence; no wildcard removal | AppLifecycleService / `apps.uninstall` | D | Both / P6 |
| 36 | `open_settings` | `section?:` capability enum | Open supported settings destination or top-level settings; disclose fallback instead of claiming deep-link success | DeviceSettingsService / `system.open_settings` | C | Both / P6 |
| 37 | `open_url` | `url` | Validate scheme/length and route on selected device; unknown/deep-link schemes require explicit grant; no host file/shell URLs | AppLifecycleService / `apps.open_url` | C or S by scheme/context | Both / P6 |
| 38 | `build_and_run` | `project_path`, `scheme`, `configuration?: "Debug"`, `device_id?`, `launch?: true`, `timeout_ms?: 1800000` | Build approved workspace/project; resolve app; install; optionally launch; stage-by-stage results plus bounded log artifact | BuildService → AppLifecycleService / `build.xcode` + install/launch | H + S | Both / P6 |
| 39 | `set_location` | `latitude`, `longitude`, `altitude?` | Set a device-wide simulated location on a supported backend and report the outcome | DeviceSettingsService / `location.simulate` | S | Both / P6 |
| 40 | `clear_location` | No extra fields | Clear simulated override; report actual state if observable, otherwise requested result | DeviceSettingsService / `location.simulate` | C | Both / P6 |
| 41 | `get_clipboard` | `format?: "text"` | Read supported device clipboard, bounded and redacted from logs; warn/grant if backend could expose synchronized Mac content | ClipboardService / `clipboard.read` | S | Both / P6 |
| 42 | `set_clipboard` | `text` | Replace clipboard only after specific write grant; disclose possible Universal Clipboard propagation | ClipboardService / `clipboard.write` | S | Both / P6 |
| 43 | `list_crashes` | `bundle_id?`, `since?`, `limit?: 50`, `cursor?` | Paginated metadata and opaque report IDs, no bulk body dump | DiagnosticsService / `diagnostics.crash_list` | S | Both / P6 |
| 44 | `get_crash` | `crash_id`, `symbolicate?: false`, `symbols_path?`, `destination?` | Report artifact and bounded summary; symbolication only with matching approved symbols; return partial-symbolication status | DiagnosticsService / `diagnostics.crash_read` | S | Both / P6 |
| 45 | `download_app_container` | `bundle_id`, `destination?`, `filename?` | Export accessible development-app container, manifest, checksum and access limitations; fail clearly for inaccessible apps | DiagnosticsService / `apps.container_export` | S | Both / P6 |
| 46 | `request_human_action` | `reason`, `instructions`, `resume_condition?`, `timeout_ms?: 120000` | Present local handoff; release control; return completed/cancelled/expired and verification status; never accept secrets in arguments | WorkflowService / `workflow.handoff` | U | Both / P6 |
| 47 | `handle_alert` | `snapshot_id`, exactly one of `button_ref` or `button_label` | Inspect current alert and click the specified uniquely matched permitted button; protected/security/account prompts require handoff | WorkflowService + ElementService / `ui.alert` | C or U | Both / P6 |
| 48 | `wait_for` | Typed `condition`, `timeout_ms?: 10000`, `poll_interval_ms?: 250` | Observe until supported condition is met; return satisfying snapshot/time; bounded, cancellable, no mutation lease held | WorkflowService / condition-specific observation | O | Both / P6 |
| 49 | `wait` | `duration_ms` from 0 to 120000 | Cancellable monotonic delay; report actual elapsed time; no device required, no lease held | WorkflowService / `workflow.delay` | O | Both / P6 |
| 50 | `batch_commands` | `commands: [{tool, arguments}]`, `stop_on_error?: true`, `device_id?`, `timeout_ms?: 120000` | Preflight up to 25 allowed children, one bound device/workflow lease; ordered per-step results, partial state, no rollback, no nesting | WorkflowService → same CommandRouter / child capabilities | Maximum of child policies | Both / P6 |

## Operation-specific limits and semantics

These limits are starting design values; benchmark and finalize them at G0/G5. Expose them through `get_device_info` or catalog documentation.

| Input or resource | Proposed validation |
|---|---|
| Screenshot | JPEG/PNG only; max width 320–4096; quality 0.1–1 for JPEG; cap encoded response at 8 MB and report requested/effective size |
| Elements | Default 200, maximum 1,000 returned; report truncation; no silent loss of a referenced element |
| Touch durations | Validate backend-specific minima/maxima; general hard bound 10 seconds per gesture; disallow NaN/infinite coordinates |
| Pinch | Initial scale range 0.25–4.0; two-contact capability required |
| Text | Maximum 64 KiB UTF-8 per call; do not split grapheme clusters; preserve explicit newline behavior; never log content |
| Recording | Default maximum 10 minutes, configurable up to 60 minutes initially; storage preflight and visible stop control |
| Location | Latitude −90…90; longitude −180…180; altitude range defined by qualified backend |
| Accessibility patch | Apply only enumerated fields; unsupported fields fail preflight before any supported field is changed |
| Waiting | Maximum 120 seconds; polling no faster than 100 ms; backend events preferred when available |
| Batch | 1–25 children, one device, no nested batches, no build, no handoff, no device-selection/lifecycle changes, no recording start/stop; preapprove sensitive children |
| Export | No relative traversal or symlink escape; use approved directories; conflict defaults to error |

Supported `wait_for` conditions should begin with `text_present`, `text_absent`, `element_present`, `foreground_app`, and `device_ready`. Each has its own validated fields. Do not accept arbitrary code, scripts, predicates, or selectors that invoke host execution. A condition should describe whether it uses AX, OCR, or device state.

`batch_commands` must not allow children to override the bound device. If a child supplies an incompatible device ID, fail preflight before running any child. Resolve fresh text/element targets at the relevant step; a snapshot from before a preceding mutation may be stale. A child timeout, unknown delivery, cancellation, or manual takeover must stop the batch even when `stop_on_error` is false. Continue-on-error is only for errors whose lack of side effects is known and whose continuation is safe.

## Stable execution errors

| Code | Meaning and caller recovery |
|---|---|
| `invalid_arguments` | Schema, bound, enum, or mutually exclusive field error; fix input |
| `device_not_selected` / `device_not_found` | Select/list devices; never substitute a different target |
| `device_ambiguous` | Only applicable to UI discovery or a named legacy alias; choose an opaque ID |
| `device_disconnected` / `connection_changed` | Reconnect, inspect, and reauthorize as required |
| `prerequisite_required` | Trust, unlock, Developer Mode, runtime, toolchain, pairing, or runner setup needed |
| `unsupported_capability` | Tool exists but this backend/profile lacks the required operation |
| `stale_observation` / `stale_element_reference` | Obtain a new frame or element snapshot |
| `ambiguous_text_match` | Inspect candidates and choose a match explicitly |
| `approval_required` / `approval_denied` / `approval_expired` | Complete or cancel the local authorization flow; no agent override |
| `protected_action_requires_human` | User must complete the protected device step |
| `device_held` | The owner paused AI control. Wait for Resume before attempting another write |
| `ownership_changed` | The owner took control. Obtain a fresh observation before sending further input |
| `cancelled` / `deadline_exceeded` | Inspect side-effect state before retrying |
| `delivery_outcome_unknown` | Side effect may have happened; observe before deciding what to do next |
| `artifact_conflict` / `insufficient_storage` / `unsafe_path` | Choose another approved destination or resolve storage/conflict |
| `build_failed` / `provisioning_failed` / `install_failed` | Review stage results and bounded diagnostic output |
| `backend_failure` | Include a redacted diagnostic reference; never return fabricated success |

## Contract completion checklist

For each production tool, require a schema, a documented completion condition, explicit capability requirements, policy classification, cancellation/deadline behavior, one successful integration case, and meaningful failure cases. Maintain one shared catalog so the MCP registration, UI diagnostics, examples, documentation and compatibility table derive from the same source.
