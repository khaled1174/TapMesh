# TapMesh public product

Approved product decisions (2026-11-05): a standalone local Mac application,
open source under GPL-3.0-or-later. Each user's Mac runs the native interface,
device agent, authenticated HTTP/MCP coordinator, OCR and artifact storage.
There are no required cloud accounts or public backend services.

## Installation and first launch

The app bundles its Python interpreter, pinned packages, idb companion and
system-framework OCR/media helper. A user installs TapMesh.app and opens it.
First launch creates a private profile at
`~/Library/Application Support/TapMesh/Local`; credentials are generated locally,
never packaged. Services bind only to loopback on dynamically allocated ports.
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

## Owner's private TNAS environment

The owner's existing root `TapMesh/connection.json`, credentials, services and
TNAS deployment remain a separate advanced development connection. The default
public app does not read or overwrite it. The public binary contains no owner
credentials, NAS endpoint, SSH identity or preconfigured user devices.
`scripts/manage.py` remains the private deployment utility, not public onboarding.

## Distribution gates

An embedded local runtime alone is not a public release. A release additionally
needs fresh relocated-bundle and native first-launch/restart/quit evidence,
license notices and complete corresponding source with build instructions,
recursive Developer ID signing, notarization, supported-OS/device qualification,
and the planned installation/update/soak checks. Release manifests must retain
false/unverified status for gates that have not actually passed.

The package requires macOS 15 or later, matching the embedded companion's actual
deployment target. The initially qualified binary architecture is Apple Silicon. Intel support is
not claimed by this package. Public publishing needs an explicit release location
and a matching source download; private qualification files are not source
release inputs.
