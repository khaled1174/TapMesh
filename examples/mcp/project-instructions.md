# TapMesh project workflow template

Copy the relevant instructions into the file your AI client reads, such as
`AGENTS.md`, and replace the placeholders. Adding this template alone does not
configure MCP or grant build/device access.

- Xcode workspace/project: `/path/MyApp/MyApp.xcworkspace`
- Shared scheme: `MyApp`
- Configuration: `Debug`
- Bundle ID: `com.example.myapp`
- Destination: the device explicitly authorized by the user; discover its current ID.
- Verification goal: the expected main screen is visible in the intended app.

Use the configured TapMesh MCP server. Start with `list_devices`, inspect readiness
and capabilities, then `select_device` in this session. Do not assume the device
selected in the Mac interface is selected for this client.

Build the approved Xcode project with `build_and_run` and `launch: true`. Stop for
required local approvals. Prepare framework-specific prerequisites with the
project's normal tools; do not assume TapMesh starts those services.

Verify every build/install/launch stage. Check `get_foreground_app` and
`take_screenshot` before interacting, and use `list_elements` when supported.
Operate only the project's intended app. Observe before input and verify the
result independently afterward. Never treat command delivery as verified success
or blindly replay an action whose outcome is uncertain.

The user grants build-folder access and device permission in TapMesh. Do not
approve your own requests, change permission settings, enter device passcodes,
or publish connection files, tokens or private device identifiers.
