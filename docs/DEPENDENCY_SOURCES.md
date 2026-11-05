# Offline dependency sources

TapMesh's GPL-3.0-or-later release workflow pairs the app with its exact source,
build instructions, and an offline dependency source pack. Dependencies retain
their own licenses. The source pack can accompany the installer as a separate
release archive; its retained files do not depend on upstream websites remaining
available.

`scripts/collect_sources.py` inspects the **actual bundled** distributions without
importing or executing them. It matches each name and version to `uv.lock`,
verifies the exact sdist SHA-256, and retains the complete archive. It also copies
license texts, including complete licenses embedded in README or metadata files.
Two workers bound download and archive work. HTTPS certificate checks remain
enabled, and verified downloads are cached under
`~/Library/Caches/TapMesh/source-archives`.

Run with Python 3.12 or newer after runtime embedding and before final packaging:

```sh
python3 scripts/collect_sources.py \
  --resources "/path/to/TapMesh.app/Contents/Resources" \
  --destination "/path/to/source-pack" \
  --native-evidence "/path/to/handoff/native-dependency-review.json"
```

The native handoff is produced by the separate audit documented in
[NATIVE_DEPENDENCY_SOURCE_AUDIT.md](NATIVE_DEPENDENCY_SOURCE_AUDIT.md). The collector
rechecks every inventoried source, license, recipe and evidence file before
copying it into the source pack. Component versions must match the actual runtime.

The output includes `packages/`, `rust-crates/`, `upstream/`, `upstream-native/`,
licenses, installed `METADATA.txt`, runtime inventory, exact lockfile, tag evidence,
and `source-manifest.json`. Reported paths are relative to the source pack. The
application packager separately creates `Source/TapMesh-source.tar.gz` with the
TapMesh source and build scripts.

Use `--offline` to require the existing checksum-verified cache without network
requests. Exit `0` means all source-availability gates in the report passed; exit
`2` means a useful pack was written with explicit gaps; exit `1` means collection
or inventory validation failed. An existing nonempty destination must carry a
collector receipt. A rerun removes obsolete package directories identified by
that receipt, including distributions deliberately omitted from a later runtime.

## Native source evidence

Mach-O files alone do not cause a source failure. Each affected Python package
gets `native-source-evidence.json`, recording preferred source members, build
recipe paths, installed binary hashes, and actual `LC_LOAD_*` library commands.
`LC_ID_DYLIB` is excluded because it identifies the file itself. Assessed C,
Cython and Python layouts close their source gate when their source/build inputs
are retained and their loads resolve to Apple platform libraries.

Rust extensions retain every registry crate named by their exact `Cargo.lock`,
verify each crate checksum, and retain licenses. Nonregistry sources and missing
license texts remain explicit failures. Where an upstream package declares an
Apache-2.0 alternative but omits the text, the pack retains the pinned canonical
[Apache license](https://www.apache.org/licenses/LICENSE-2.0.txt) and records the
selected alternative. Existing source attribution stays in the original archive.
The same approach retains canonical GNU GPL3 text for an explicit GPLv3-or-later
distribution declaration; it does not invent MIT copyright notices.

Pillow 13.0.0 source evidence includes its complete release build recipes, the
pinned multibuild submodule, and the exact archives used for the bundled native
libraries. These include libavif's nested AOM 3.14.1, DAV1D 1.5.3 and pinned libyuv
source. Runtime version qualification matched the release dependency recipe.
`PILLOW_SOURCE_BUILD.txt` explains the retained archive cache and local codec
source layout. Sources from the official Pillow mirror are pinned to an immutable
mirror commit and SHA-256, rather than its moving `main` branch.

The cryptography 50.0.2 wheel's embedded OpenSSL version string identifies 4.0.3.
Its exact [OpenSSL release](https://github.com/openssl/openssl/releases/tag/openssl-4.0.3)
source, upstream checksum evidence, licenses and build instructions are retained
separately from its Cargo dependency graph.

## Pinned upstream snapshots

Official GitHub tag evidence is retained and checked against these commits.
Annotated tags retain both response objects. Archive SHA-256 pins additionally
bind the retained source bytes.

| Component | Version | Source commit |
| --- | --- | --- |
| [idb companion](https://github.com/meta/idb/tree/v1.7.8) | 1.7.8 | `c793afad2a31092628f7013cf1aa96f266c85024` |
| [python-build-standalone recipes](https://github.com/astral-sh/python-build-standalone/tree/20260510) | 20260510 | `f0ae464f527498d6c4b13f19977447d603cd5edd` |
| [CPython](https://github.com/python/cpython/tree/v3.12.13) | 3.12.13 | `3bb231a6a5dc02b95658877318bf61501a7209e9` |
| [Sparkle](https://github.com/sparkle-project/Sparkle/tree/2.10.1) | 2.10.1 | `ab7d71291238df6e0492143b120c9b696a35f603` |

Sparkle is included when its framework is actually embedded. Its source snapshot
retains Objective-C/C sources, the Xcode project and configurations, modified
Ed25519 sources, bsdiff sources and licenses. Actual framework load commands are
audited for unsupported external dependencies.

## Scope of a passing gate

`corresponding_source_verified` records assessed availability of preferred
sources, dependency sources, licenses and build recipes. It does **not** claim
an independently reproduced binary or verified upstream build attestation. Those
provenance results remain separate fields in the native evidence. The idb audit
also records upstream's unfrozen general build-tool versions.

Missing exact sources, missing license texts, unassessed native libraries,
unsupported component versions, mismatched inventories and failed checksums
remain explicit failures. Public release readiness also requires the separate
application, signing, notarization, update and platform qualification gates.

The collector never runs source build hooks or extracts archive paths into the
project. License extraction reads bounded regular-file members into names chosen
by TapMesh; complete archives remain intact for offline use.
