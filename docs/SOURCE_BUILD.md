# Building and modifying TapMesh

TapMesh is licensed under GPL-3.0-or-later; see LICENSE and NOTICE. Its source
archive contains the native app, helpers, Python services, contracts, tests,
dependency locks and packaging scripts. It excludes private profiles,
credentials, device captures, runtime databases and original investigation data.

## Build on Apple Silicon

Install Xcode with command-line tools, Python 3.12 and uv. These are developer
build prerequisites, not requirements to install the resulting app.

```sh
uv venv --python 3.12 .venv
uv pip sync --python .venv/bin/python --require-hashes requirements-mac.lock
.venv/bin/python scripts/setup_companion.py
.venv/bin/python scripts/package_app.py --configuration release --standalone
```

The packager downloads a checksum-pinned Python build-standalone archive,
installs the exact hashed runtime packages from requirements-product.lock, and
embeds the companion from its verified release archive. It builds the app and
TapMeshTools with SwiftPM. Dependency acquisition occurs only on the build
machine. No signing secrets are needed for an ad-hoc build of a modified app. Before signing, the packager removes group/other write and special mode bits from code and resources, including files copied from network shares; the relocated-runtime qualifier independently checks those permissions.

For Developer ID distribution add `--identity '<Developer ID identity>'`.
Notarization is a separate step and must not be claimed by an unsigned or merely
Developer ID signed package. Native builds and codesigning use local APFS caches;
the finished archive can be copied to network storage.

## Verify

```sh
.venv/bin/python scripts/check_contracts.py
.venv/bin/python -m pytest -q
swift test --package-path macos --scratch-path /tmp/tapmesh-swift-build --jobs 2
```

Live physical-device tests are opt-in and require the user's own trusted,
unlocked device. Ordinary automated tests do not grant permission to interact
with personal apps or data. Runtime token generation is independent for every
user and does not use distributor keys.

## Corresponding source status

The app includes its own source and third-party notices. The binary release
manifest separately tracks verification of complete corresponding source for
all redistributed dependencies. This gate stays false until the dependency
source archive, native dependency provenance and notices have been audited.
Publish matching complete source next to any public binary; a link to upstream
alone is not a substitute for making the required corresponding source available.
