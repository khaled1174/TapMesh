# TapMesh public product

Approved product decisions (2026-11-05): a standalone local Mac application,
open source under GPL-3.0-or-later. Each user's Mac runs the native interface,
device agent, authenticated HTTP/MCP coordinator, OCR and artifact storage.
There are no required cloud accounts or public backend services.

## Installation and first launch

The app bundles its Python interpreter, pinned packages, idb companion and
system-framework OCR/media helper. A user installs TapMesh.app and opens it.
First launch creates a private profile at
`~/Library/Application Support/TapMesh/Local`. Services bind only to loopback
on dynamically allocated ports. The app keeps its owner approval credential
in memory and passes it to its services through inherited pipes.
Closing the window keeps the app available to MCP clients. Quitting stops its
owned local services. A crashed app's supervisor detects the lost parent and
stops its children. No launch agent, SSH, Docker, uv or Python installation is
required for normal use.

Apple development tools remain an explicit prerequisite: the user installs
Xcode and suitable simulator runtimes, or connects and trusts a physical device,
enables its Developer Mode and unlocks it when required. TapMesh advertises the
actual capabilities of each device; it does not promise unsupported Apple
operations. Local Vision language support is queried at runtime. Build folders
are granted explicitly in Settings; TapMesh does not grant the whole home folder.

## Optional advanced connection

The default app runs local services and does not require a NAS, SSH tunnel or
remote backend. Advanced users can select an existing local connection file in
Settings. Custom services remain separately managed. No configured endpoint,
credential, SSH identity or device list is included in the public source.
`scripts/manage.py` is an optional development utility, not public onboarding.

## Distribution gates

The 0.2.0 Developer Preview requires recursive Developer ID signing,
notarization, third-party notices, and complete corresponding source with build
instructions. Qualify the exact build on clean supported Macs for Finder launch,
installation, update, restart, and quit. Release manifests record each gate when
the corresponding artifact has passed it.

The owner-attended live-control trial covers tap and swipe latency, portrait and
landscape video, locking during typing, cable reconnect, Wi-Fi fallback,
selection across two devices, sleep and wake, per-device consent, and Stop and
Resume. For frame-rate measurement, play moving content on the phone and close
other live previews. Keep measurements and results under `docs/` and resolve
failed steps before distribution.

The package requires macOS 15 or later, matching the embedded companion's actual
deployment target. The initially qualified binary architecture is Apple Silicon. Intel support is
not claimed by this package. Public publishing needs an explicit release location
and a matching source download; private qualification files are not source
release inputs.
