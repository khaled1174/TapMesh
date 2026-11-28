# Building and modifying TapMesh

TapMesh is licensed under GPL-3.0-or-later. See LICENSE and NOTICE. Its source
archive contains the native app, helpers, Python services, contracts, tests,
dependency locks and packaging scripts. It excludes private profiles,
credentials, device captures, runtime databases, internal localization working
notes, historical qualification reports with private evidence, and original
investigation data. It includes the built `.lproj` strings.

## Build on Apple Silicon

Install Xcode with command-line tools, Python 3.12 and uv. These are developer
build prerequisites, not requirements to install the resulting app.

```sh
uv venv --python 3.12 .venv
uv pip sync --python .venv/bin/python --require-hashes requirements-mac.lock
.venv/bin/python scripts/setup_companion.py
.venv/bin/python scripts/package_app.py --configuration release --standalone --build-number 25
```

The packager downloads a checksum-pinned Python build-standalone archive,
installs the exact hashed runtime packages from `requirements-product.lock`, and
embeds the companion from its verified release archive. It uses SwiftPM to
compile TapMesh and TapMeshTools. Dependency acquisition occurs on the build
machine. An ad-hoc build of a modified app needs no signing secrets. Before
signing, the packager sets directories and executables to 0755 and data to 0644.
The relocated-runtime qualifier checks those permissions.

For Developer ID distribution add `--identity '<Developer ID identity>'`.
For updates, also pass the production HTTPS appcast with `--update-feed` and
its Sparkle Ed25519 public verification key with `--update-public-key`. Submit
the signed package for notarization. Native builds and codesigning use local
APFS caches. You can copy the finished archive to network storage.

## Make a DMG

After signing and, for distribution, notarizing and stapling the app, create a
signed drag-to-Applications image from the exact bundle:

```sh
.venv/bin/python scripts/package_dmg.py \
  --app "$HOME/Library/Caches/TapMesh/packages/TapMesh.app" \
  --output dist/TapMesh-0.2.0-build25-macos-arm64.dmg \
  --identity '<Developer ID Application identity>'
```

The DMG packager verifies the source and copied app signatures in a local cache, preserves the input
bundle, adds an Applications shortcut and installation instructions, signs the
image and verifies its integrity. It refuses to overwrite an existing output.
Submit the DMG itself for notarization and staple the accepted ticket before
publication. Packaging alone does not satisfy the device or clean-Mac release
gates. [Release status](RELEASE_STATUS.md)

Attach the notarized DMG to each GitHub release for manual installation.
Attach the Sparkle-signed ZIP referenced by the appcast for in-app updates.

## Verify

```sh
.venv/bin/python scripts/check_contracts.py
.venv/bin/python -m pytest -q
swift test --package-path macos --scratch-path /tmp/tapmesh-swift-build --jobs 2
```

Live physical-device tests are opt-in and require the user's own trusted,
unlocked device. Run `scripts/qualify_live_control.py --device <device_id>` for
read-only live measurements. Play moving content on the phone and close other
live previews so the viewer count starts at zero. Add `--attended` only with
the device owner present for the control steps. TapMesh generates runtime
credentials separately for each profile. Check [release status](RELEASE_STATUS.md)
for verification of the release build.

## Corresponding source

Historical artifact checks do not qualify the current preview. Collect and verify
the corresponding source for the exact candidate you intend to distribute.

The app includes its own source and third-party notices. The binary release
manifest separately tracks verification of complete corresponding source for
all redistributed dependencies. Collect and verify the matching source for the
exact release candidate. Publish that source with any public binary.
[Dependency source collection](DEPENDENCY_SOURCES.md)
