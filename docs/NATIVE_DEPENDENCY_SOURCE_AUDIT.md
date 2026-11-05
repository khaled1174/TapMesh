# Native dependency source audit

Audit date: 2026-11-05. Scope: the Apple Silicon idb-companion 1.7.8 release and CPython 3.12.13 from python-build-standalone 20260510. This is a technical inventory of preferred source, recipes, notices, and release provenance. The Python package/wheel/Rust review is maintained separately.

## Result and retained evidence

All **44 assessed source archives** were downloaded and retained; their SHA-256 values were freshly rechecked after collection. They contain **331,352,736 bytes** in total. No missing native source component remains in the assessed idb/Python target graph. The source pack must include these files before distribution; collecting them in a developer cache does not make them part of the delivered product.

The exclusive working cache is `~/Library/Caches/TapMesh/native-dependency-sources`. Its `native-source-manifest.json` records every official URL, exact version/commit, checksum, byte count, pin basis, and archive path. `audit-summary.json` separates source availability from binary provenance and integration. The cache also retains official release API responses, the matching Python `PYTHON.json`, load-command evidence, 62 dependency notice records, and 19 Python distribution notice files. The full Python binary archive is evidence for metadata, rather than corresponding source to copy into the source pack.

## idb-companion 1.7.8

The retained binary archive SHA-256 is `44369a2df0b62e3a1764de61bd09280999c7e9b27ea8f20bff1ea3a9db0302a2`. It matches the digest in the [official release](https://github.com/meta/idb/releases/tag/v1.7.8) API response, which associates the release with commit `c793afad2a31092628f7013cf1aa96f266c85024`. The already retained idb source archive SHA-256 is `c440cd8b4fa2b0adaf675990e5a7ec7b5d43b4a3fb574fd86d419c57aa295f41`.

The source includes Objective-C/Swift simulator/device code, protocol definitions, checked-in generated RPC code, project generation, build scripts, and release packaging. [Companion/project.yml](https://github.com/meta/idb/blob/c793afad2a31092628f7013cf1aa96f266c85024/Companion/project.yml) declares exact package versions; [Package.resolved](https://github.com/meta/idb/blob/c793afad2a31092628f7013cf1aa96f266c85024/Package.resolved) supplies all 23 revisions below. `build.sh` copies that lock into the generated Xcode workspace and invokes xcodebuild with `-onlyUsePackageVersionsFromResolvedFile` and `-disableAutomaticPackageResolution`. The release workflow builds `./build.sh build all`, or reuses a successful CI artifact for the same commit, then archives `Build/Distribution`.

| Dependency | Version | Retained commit | Archive SHA-256 |
| --- | --- | --- | --- |
| grpc-swift-2 | 2.4.3 | `ac33066eb6edb1a21a6ca172ea8184a9b06f3cc7` | `83cbd461586c8e21ea1dcd940bcb3baab8811b69cab3d57ee504a0c9ccb0a949` |
| grpc-swift-nio-transport | 2.9.2 | `eaad084d6c26ff1f2e96f9c2ab76ef84d7165ab6` | `0614ea732f41d84562ed924ca5213c07a8e649d57f65653a6901b36194b427a6` |
| grpc-swift-protobuf | 2.4.1 | `176c5a434fd76f6f479848d1a8f7d44967534168` | `2c5f41836179104937b87ea1ed55bec28a1eacfa6e12eaf00ec8e8cd2707a7a9` |
| swift-algorithms | 1.2.1 | `87e50f483c54e6efd60e885f7f5aa946cee68023` | `57f7faed9c572b7517bac082eed28f48653d49e0b86e1cdd239602a09112e8fe` |
| swift-argument-parser | 1.8.2 | `6a52f3251125d74daf04fcbd5e6f08a75d074382` | `23ab82e8be3b82b199a008e0044229ae327a8824734d917b78bcdde64ce81ec1` |
| swift-asn1 | 1.7.1 | `a9a5efd40eaf558a2bcd48d64b1d1646be686008` | `422c98d0aecf8cd7f1022167a0acaba1b3c4e9341e81ed16a7677427c3893650` |
| swift-async-algorithms | 1.1.5 | `3da39bbc4e687d4192af7c9cf4eab805745a0b9c` | `62d55abbf64233bc6d23e903c71bc9e6ae17fe0878cf396d35ab237d26e4ceac` |
| swift-atomics | 1.3.1 | `0442cb5a3f98ab802acb777929fdb446bda11a34` | `e2652977537003d50b8da1f330ef73d94e50e294b7f5a2fd0f2ddbb03fbf632f` |
| swift-certificates | 1.19.4 | `449dbbecd0f31e82b510ada227ca152caa8b5e98` | `93f22f59f70b40600bff2e1debadd3e8f96d8579305e4a7755161baa81043db7` |
| swift-collections | 1.6.0 | `a0cb0954ecb21e4e31b0070e6ed5674e8556685a` | `8e8714b566f9fafc28bf3de967e789f84d9e53466faa3a2240060ca7c1a7f2c7` |
| swift-crypto | 4.5.2 | `da9d28d69ebe3894b18376c8f2395c2f37b8448f` | `a99226ce0535133ac5b54dc528904c3db7eddfca520853b2c3495b72f4f2b9cb` |
| swift-http-structured-headers | 1.7.0 | `933538faa42c432d385f02e07df0ace7c5ecfc47` | `15a80f1ddeea17fb12473f380ae4b7339ea8bdaf1a388a0637e3a52afaf1d612` |
| swift-http-types | 1.8.0 | `bff4b6903cdc99dda49649dd52f46c11cfd3ed50` | `e45ae31885fffba5f3e01298a784ed26422ac9bae759e3c5716e09b15c083753` |
| swift-log | 1.14.0 | `a878e7f8f46cfc0e1125e565b5c08e7d5272dc9a` | `1f4e2205b10354bcba33a9f919488e1086bebb5cfb34db5262852cc5f9b3e633` |
| swift-nio | 2.101.3 | `0b18836bd8b0162e7e17a995a3fbee20ed8f3b2b` | `b7ffa2501fbbdac568999e96fa99e8eb392be42409540de797e7a087afa402ac` |
| swift-nio-extras | 1.34.3 | `88a51340f59cf181ebde888bd1b749296b3ec029` | `54e8a41c176aded2e589673d2864b32b7e0b0070bc8fde25b199da0eb6842462` |
| swift-nio-http2 | 1.45.0 | `45bdf670248be5f16ec0340e125dca285536f0fb` | `9a0ab148fcdbddbb1d64ea560bf1eb7c90963ea29329d1f889b825f3a0f0310e` |
| swift-nio-ssl | 2.37.2 | `d930168b86f46ca51a4bc09c5ca45c1833db8067` | `841f025ab418417d40e27e7db86cbe8fefd8806410601b90ba160ad603f89214` |
| swift-nio-transport-services | 1.28.0 | `67787bb645a5e67d2edcdfbe48a216cc549222d5` | `d0ec1d597f21008043bab37259df96139c4de053643af8ed0062a004bdf2ef5a` |
| swift-numerics | 1.1.1 | `0c0290ff6b24942dadb83a929ffaaa1481df04a2` | `d245f3fb06086ad0ea493b40d0a1058f17529344b5fd52ca4a7f4271240810c8` |
| swift-protobuf | 1.38.1 | `55d7a1cc5666b85c13464aea1c4b4a90feccb4c8` | `32f75fd3efc7f9ce70dd9803b2ce5f81033667a86cd1f044c56a2e445212fbbb` |
| swift-service-lifecycle | 2.12.0 | `7f9326b0326ff86e3646295ea6e891f68c471c5e` | `e139bb91804a450cf38e3fb8f61f8d39f2c6af43710f6e25a4b51d2d2c09dc29` |
| swift-system | 1.8.1 | `869129b7bf4ecc57b97d0193ad29690ca2134750` | `bd18faa740f5bd92856c21bc6928d91511e153965087abbc81639c99491400a9` |

The swift-crypto archive contains 531 BoringSSL C/assembly/header members and swift-nio-ssl contains 564, including their vendored licensing/build material. The only `.gitmodules` encountered in these 23 archives belongs to grpc-swift-2 and is empty; no unretained submodule was identified.

idb's `ZstdDecompress/README.md` identifies Zstandard 1.5.7 and the upstream single-file generator. The full Zstandard 1.5.7 source/generator is retained with SHA-256 `f24b52470d12f466e9fa4fcc94e6c530625ada51d7b36de7fdc6ed7e6f499c8e`. Its `lib/zstd.h` and `lib/zstd_errors.h` exactly match idb's copies after removing idb's first `@generated` line. The generated decoder, preferred multi-file source, generator, and notices are therefore available together.

## CPython and native libraries

The [official PBS release](https://github.com/astral-sh/python-build-standalone/releases/tag/20260510) associates recipe commit `f0ae464f527498d6c4b13f19977447d603cd5edd` with both:

- The distributed install-only stripped archive, SHA-256 `55bc1a5edbc8ac4da0081f4f5731ed2d1ed10c57cb37a820b2a0dbc7cad742e9`.
- The matching aarch64 macOS `pgo+lto` full archive, SHA-256 `0750a735a09181ce203fb357df7221eab78d8ff4372d5cb6d58a2053765befdb`.

Both digests were checked. The full archive's `PYTHON.json` identifies CPython 3.12.13, macOS SDK 15.5, deployment target 11.0, and the extension/library links. The [retained build recipes](https://github.com/astral-sh/python-build-standalone/tree/f0ae464f527498d6c4b13f19977447d603cd5edd/cpython-unix) and `pythonbuild/downloads.py` pin the following preferred sources. The official CPython release tar is included in addition to the previously collected CPython Git tag archive, preserving the exact release input selected by PBS.

| Source project | Recipe version | Retained archive SHA-256 |
| --- | --- | --- |
| cpython-3.12 | 3.12.13 | `c08bc65a81971c1dd5783182826503369466c7e67374d1646519adf05207b684` |
| bzip2 | 1.0.8 | `ab5a03176ee106d3f0fa90e381da478ddae405918153cca248e682cd0c4a2269` |
| expat | 2.6.3 | `274db254a6979bde5aad404763a704956940e465843f2a9bd9ed7af22e2c0efc` |
| libffi | 3.4.6 | `b0dea9df23c863a7a50e825440f3ebffabd65df1497108e5d437747843895a4e` |
| mpdecimal | 4.0.0 | `942445c3245b22730fd41a67a7c5c231d11cb1b9936b9c0f76334fb7d0b4468c` |
| openssl-3.5 | 3.5.6 | `deae7c80cba99c4b4f940ecadb3c3338b13cb77418409238e57d7f31f2a3b736` |
| sqlite | 3500400 | `a3db587a1b92ee5ddac2f66b3edb41b26f9c867275782d46c3a088977d6a5b18` |
| tcl | 9.0.3 | `2537ba0c86112c8c953f7c09d33f134dd45c0fb3a71f2d7f7691fd301d2c33a6` |
| tk | 9.0.3 | `bf344efadb618babb7933f69275620f72454d1c8220130da93e3f7feb0efbf9b` |
| xz | 5.8.1 | `507825b599356c10dca1cd720c9d0d0c9d5400b9de300af00e4d1ea150795543` |
| uuid | 1.0.3 | `46af3275291091009ad7f1b899de3d0cea0252737550e7919d17237997db5644` |
| llvm-project | 22.1.3 | `2488c33a959eafba1c44f253e5bbe7ac958eb53fa626298a3a5f4b87373767cd` |

The metadata maps `_bz2` to bzip2, `_ctypes` to libffi, `_decimal` to mpdecimal, XML modules to Expat, `_hashlib`/`_ssl` to OpenSSL, `_lzma` to liblzma, `_sqlite3` to SQLite, and `_uuid` to libuuid. Their static-library sources are retained. Tcl/Tk 9.0.3 shared libraries are included in the runtime; the Tcl source archive also contains preferred source and license files for the installed **Itcl 4.3.5** and **Thread 3.0.4** packages.

The runtime's load commands resolve ncurses, panel, libedit, zlib, libSystem, CoreFoundation, and SystemConfiguration from macOS. `_tkinter` resolves `@rpath/libtcl9.0.dylib` and `@rpath/libtcl9tk9.0.dylib`, both version 9.0.3, from bundled libraries. This was checked using files extracted from the checksum-pinned distribution archive. Recipe alternatives libedit/ncurses/zlib and Tcl/Tk 8.6.12 are retained as supplementary source; they are not evidence that those alternative libraries were bundled for this target. Zstandard 1.5.7 is also retained because the PBS target builds it and idb's generated decoder uses that version.

PBS's `src/release.rs` retains the full-to-install-only conversion and stripping recipes. Its `cpython-unix/targets.yml` pins the aarch64 macOS dependency set. `pythonbuild/downloads.py`, all patch/build scripts, and build requirements are present in the retained PBS source archive.

### Compiler runtime and build recipes

The full Python metadata includes `build/lib/libclang_rt.osx.a`. The PBS download pin selects the LLVM 22.1.3 toolchain from the [official toolchain bootstrap release](https://github.com/indygreg/toolchain-tools/releases/tag/toolchain-bootstrap%2F20260410). Its source commit `252f4ecbed5b62557ea283ec47e26102dae83c11` is retained, SHA-256 `5ae75761a6c61fd1dc1a43ab6f03dcc0228f70c974066b9d7b59ad2ab6c9eeab`.

That source's `toolchain-bootstrap/scripts/clang-macos.py` pins the complete LLVM 22.1.3 source archive to SHA-256 `2488c33a959eafba1c44f253e5bbe7ac958eb53fa626298a3a5f4b87373767cd`, matching the official LLVM release asset digest. The retained archive includes **3,975 compiler-rt C/C++/assembly/header members**, `compiler-rt/CMakeLists.txt`, and license material; the bootstrap archive retains its patches and compiler build recipes.

## Remaining integration and provenance limits

1. Copy all selected source archives, the idb/PBS recipe archives already retained by the main collector, this audit evidence, and applicable notices into the versioned corresponding-source pack. Recheck the final pack manifest and bundle references after integration. No source was uploaded or published by this audit.
2. Preserve compiler-rt/LLVM, vendored BoringSSL, idb/Zstandard, and Python/Tcl/embedded-package notices. `dependency-license-manifest.json` and `pbs-license-manifest.json` identify retained notice bytes and hashes for packaging.
3. Official release digests, tag/source association, exact dependency revisions, source layouts, and recipes are matched. An independent reproducible rebuild of either binary and signed build-attestation verification were not performed. idb's generated build date/time and unpinned Homebrew build-tool versions also prevent a claim that this audit reproduced its original build environment. These are provenance/reproducibility limits, separate from the source files retained above.
4. Apple SDK/macOS system frameworks and general-purpose build tools remain external prerequisites. The separate Python wheel/sdist/Rust review must be integrated to assess the entire application distribution.

## Verification

Fresh checks rehashed 44/44 source archives; compared recipe hashes for all 19 PBS source inputs and the LLVM source; inspected all 23 idb dependency archives; checked embedded Itcl/Thread sources and notices; compared idb/Zstandard header bytes; and inspected load commands from the pinned Python archive. No downloaded source build hook, companion service, device action, or private NAS service was executed.
