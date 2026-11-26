# TapMesh

[العربية](README.ar.md)

TapMesh is a Mac workspace for your iPhone, iPad, and iOS simulators. See the device screen, control it with your mouse and keyboard, and inspect or build apps. You can also connect an MCP client to the same devices from your AI app.

## Get started

You need an Apple Silicon Mac running macOS 15 or later. Install Xcode, open it once, and accept its license. For simulators, install the iOS runtime you want in Xcode. For an iPhone or iPad, connect by cable, unlock it, tap **Trust**, and turn on Developer Mode in **Settings › Privacy & Security** on the device.

1. Get the Developer Preview ZIP from [TapMesh releases](https://github.com/khaled1174/TapMesh/releases) when the preview is available. Unzip it, move **TapMesh.app** to **Applications**, and open it from Finder. See [release status](docs/RELEASE_STATUS.md) for the current qualification.
2. Select a device in the sidebar. If the device needs setup, follow the steps shown in TapMesh and choose **Check Again**.
3. Start a simulator if needed. A wired iPhone or iPad can show live video. Simulators and Wi-Fi devices use refreshed screenshots.

The app bundles its services. Close the window to keep them available to clients. Quit TapMesh to stop them. To build an app from Xcode sources, add its project folder in **Settings › Build Folders**.

## Connect an AI agent

Read the [MCP and project connection guide](docs/CONNECTING_AGENTS.md) for manual configuration, project setup and screen previews.

Open **Settings › AI Agents** or **Help › Connect an AI Agent…**.

1. Copy the configuration.
2. Add it to your AI app. Paste the JSON into your app's MCP settings, or run the Claude Code command in Terminal.
3. Ask the agent to list devices. Then ask it to select the device you want to use.

Both options include the actual TapMesh install path. No key is needed. An agent can't approve its own requests. On an iPhone or iPad you decide once whether agents may control it. Each client keeps its own device selection.

## Control and approvals

With fast control on, TapMesh can tap, swipe, type, press buttons, and open apps immediately. AI clients can do the same on simulators and on physical devices you allow. The first request for ordinary control on a new phone or tablet appears in **Approvals**. You can remove access in **Settings › General**. When an agent acts on the selected device, **Stop** pauses it and **Resume** lets it continue. Turning off fast control requires approval for each ordinary control action.

Sensitive actions such as reading the clipboard, pasting through it, saving recordings, and simulating location require a separate approval (**S**). Uninstalling or replacing an app requires destructive confirmation (**D**). Running a build requires host-execution approval (**H**) in a folder you've allowed. Protected steps such as unlocking the device need your direct input (**U**). [Tool contracts](docs/TOOL_CONTRACTS.md) describe all 50 tools.

## Privacy and files

The app, device services, screen processing, and storage run on your Mac. TapMesh keeps the approval credential in memory. **Saved Files** lists screenshots, recordings, and logs produced during the current workspace session and shows usage for stored copies. When a service starts or saves an artifact, it removes artifacts older than 30 days and keeps at most 5 GiB in its store. The device service clears derived build data older than seven days at startup. Use **Delete All Saved Files…** to clear both stores, including copies from earlier sessions. Copies you exported to another folder stay there.

To uninstall, quit TapMesh and delete **TapMesh.app**. To remove the default local profiles, saved files, and caches, also delete `~/Library/Application Support/TapMesh` and `~/Library/Caches/TapMesh`. This removes your saved files and device permissions from those profiles.

## Known limits

- Live video uses USB and one active USB tunnel at a time.
- Physical screen input is limited by device orientation and capabilities. Use portrait for general input and landscape-left where the capability lists support.
- iOS updates can change the undocumented device interfaces TapMesh uses. The app shows available capabilities for the selected device.

TapMesh is licensed under GPL-3.0-or-later. Build the app from the source repository or from the source included with the release. Public binary releases require a matching corresponding-source archive. [License](LICENSE) · [Notices](NOTICE) · [Build instructions](docs/SOURCE_BUILD.md) · [Dependency sources](docs/DEPENDENCY_SOURCES.md)
