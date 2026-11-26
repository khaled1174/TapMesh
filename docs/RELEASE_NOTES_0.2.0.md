# TapMesh 0.2.0 Developer Preview

The Developer Preview still needs the full attended device trial, Apple notarization, and clean-Mac checks before publication. [Candidate checks and release status](RELEASE_STATUS.md)

Use TapMesh to view and control iPhone, iPad, and iOS simulators on an Apple Silicon Mac with macOS 15 or later. Install Xcode before selecting a device. A wired phone or tablet supports live video. Wi-Fi devices and simulators use screenshots.

The sidebar groups devices by readiness and shows their OS versions. When setup is needed, TapMesh shows what to do on the device or in Xcode. In **Settings › AI Agents**, copy the MCP configuration or the Claude Code command for your installation.

## Control of physical devices

With fast control on, an AI client needs a per-device grant before it can tap, type, or open apps on a new phone or tablet. Allow the device in **Approvals**. TapMesh remembers that choice across restarts. Use **Stop** above the preview to cancel work from other clients and pause new writes. **Resume** releases the device. Sensitive, destructive, host-execution, and human-handoff actions still require the relevant approval or human action.

This preview removes the old screenshot comparison before physical text input, including the clock, status icon, and caret checks. It also removes the earlier text preflight that tried to classify the active field. Use the current device preview to confirm the destination before sending text. Enter credentials and passcodes directly on the device. If the phone locks while TapMesh is typing, the lock check stops further input. Inspect the field after an interruption before trying again.

## Services and saved files

The local supervisor restarts bundled services after a crash. The preview can switch to screenshots and return to live video after reconnecting. Approval credentials for the bundled services stay in memory.

Open **Saved Files** to view usage or delete TapMesh's stored copies. Each local service prunes its artifact store at startup and when it saves an artifact. It removes files older than 30 days and keeps at most 5 GiB per store. The device service removes derived build data older than seven days at startup. Copies you export stay in their chosen folder.

See the [installation guide](../README.md), [current release status](RELEASE_STATUS.md), and [build instructions](SOURCE_BUILD.md). The release ZIP and its matching source archive belong together.
