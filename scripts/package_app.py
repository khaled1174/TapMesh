#!/usr/bin/env python3
"""Build and stage the native TapMesh app using a bounded local build cache."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import platform
import re
import base64
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path.home() / "Library/Caches/TapMesh/swift-build"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configuration", choices=("debug", "release"), default="debug")
    parser.add_argument("--identity", default="-", help="codesign identity, '-' for local ad-hoc build")
    parser.add_argument("--install", action="store_true", help="Copy to the current user's Applications folder")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--standalone", dest="standalone", action="store_true", default=True, help="Embed the qualified local runtime and helpers (default)")
    mode.add_argument("--external-services", dest="standalone", action="store_false", help="Private development UI only; not a standalone user app")
    parser.add_argument("--python-runtime", type=Path, help="Unverified developer runtime override; default uses the checksum-pinned archive")
    parser.add_argument('--build-number',type=int,default=2)
    parser.add_argument('--version',default='0.1.0')
    parser.add_argument('--update-feed')
    parser.add_argument('--update-public-key')
    parser.add_argument('--staging-updates',action='store_true',help='Disposable localhost update qualification only; never a public release')
    args = parser.parse_args()
    if platform.machine() != 'arm64':
        raise SystemExit('This package currently qualifies Apple Silicon only.')
    if not 0<args.build_number<2**64 or not re.fullmatch(r'[0-9]+\.[0-9]+(?:\.[0-9]+)?',args.version):
        parser.error('Use a positive UInt64 build number and a numeric release version.')
    if bool(args.update_feed)!=bool(args.update_public_key): parser.error('The update feed and public key must be configured together.')
    if args.update_feed:
        parsed=urlsplit(args.update_feed)
        staging=args.staging_updates and parsed.scheme=='http' and parsed.hostname in {'127.0.0.1','::1'} and parsed.port is not None and 0<parsed.port<65536
        if (parsed.scheme!='https' and not staging) or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            parser.error('Use an HTTPS feed, or an explicit loopback staging feed.')
        try:
            decoded=base64.b64decode(args.update_public_key,validate=True)
            if len(decoded)!=32 or base64.b64encode(decoded).decode()!=args.update_public_key: raise ValueError()
        except ValueError: parser.error('The update verification key must be a canonical base64 Ed25519 public key.')
    command = ["swift", "build", "--package-path", str(ROOT / "macos"),
               "--scratch-path", str(CACHE), "--jobs", "2", "-c", args.configuration]
    subprocess.run(command, check=True)
    binary_dir = subprocess.check_output(command + ["--show-bin-path"], text=True).strip()
    distribution = ROOT / "dist"
    distribution.mkdir(exist_ok=True)
    # Signing must happen on local APFS. SMB creates Finder/resource-fork
    # metadata which codesign correctly rejects; only the finished ZIP goes NAS.
    local_packages = CACHE.parent / "packages"
    local_packages.mkdir(parents=True, exist_ok=True)
    bundle = local_packages / "TapMesh.app"
    staging = local_packages / "TapMesh.staging.app"
    if staging.exists():
        shutil.rmtree(staging)
    executable = staging / "Contents/MacOS/TapMesh"
    executable.parent.mkdir(parents=True)
    (staging / "Contents/Resources").mkdir()
    shutil.copyfile(Path(binary_dir) / "TapMesh", executable)
    executable.chmod(0o755)
    info = {
        "CFBundleExecutable": "TapMesh", "CFBundleIdentifier": "io.tapmesh.mac",
        "CFBundleName": "TapMesh", "CFBundleDisplayName": "TapMesh",
        "CFBundlePackageType": "APPL", "CFBundleShortVersionString": args.version,
        "CFBundleVersion": str(args.build_number), "LSMinimumSystemVersion": "15.0",
        "NSPrincipalClass": "NSApplication", "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.developer-tools",
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        "NSLocalNetworkUsageDescription": "TapMesh connects to the Apple devices you select for local development and control.",
    }
    if args.update_feed:
        info.update(SUFeedURL=args.update_feed,SUPublicEDKey=args.update_public_key,SUEnableAutomaticChecks=False,SUSendProfileInfo=False)
    if args.staging_updates: info['TapMeshUpdateStagingMode']=True
    (staging / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
    from bundle_runtime import clean_developer_rpaths, sign_embedded, run_codesign, normalize_bundle_permissions
    frameworks=staging/'Contents/Frameworks';frameworks.mkdir()
    candidates=list((CACHE/'artifacts').glob('*/Sparkle/Sparkle.xcframework/macos-arm64_x86_64/Sparkle.framework'))
    if len(candidates)!=1: raise SystemExit('The checksum-pinned Sparkle framework artifact is missing or ambiguous.')
    shutil.copytree(candidates[0],frameworks/'Sparkle.framework',symlinks=True)
    clean_developer_rpaths(executable)
    subprocess.run(['/usr/bin/xcrun','swift-stdlib-tool','--copy','--scan-executable',str(executable),'--platform','macosx','--destination',str(frameworks)],check=True,capture_output=True)
    framework_count=sign_embedded(frameworks,args.identity)
    runtime_manifest = None
    if args.standalone:
        from bundle_runtime import embed, sign_embedded
        resources = staging / 'Contents/Resources'
        shutil.copyfile(Path(binary_dir)/'TapMeshTools', resources/'TapMeshTools')
        (resources/'TapMeshTools').chmod(0o755)
        launcher = staging/'Contents/MacOS/tapmesh-mcp'
        subprocess.run(['/usr/bin/xcrun', 'clang', '-Os', '-mmacosx-version-min=15.0', '-Wall', '-Werror', str(ROOT/'macos/MCPLauncher/main.c'), '-o', str(launcher)], check=True)
        runtime_manifest = embed(resources, args.python_runtime)
        runtime_manifest['updater']={'version':'2.10.1','archive_sha256':'4c2e48a3e50ebcc2c88a58a4c7264a1a99c3c31857ce754357afb9cdb323022a','configured':bool(args.update_feed)}
        runtime_manifest['signed_mach_o_count'] = sign_embedded(resources, args.identity)+framework_count
        (resources/'runtime-manifest.json').write_text(json.dumps(runtime_manifest, indent=2)+'\n')
    normalize_bundle_permissions(staging)
    sign = ["codesign", "--force", "--sign", args.identity]
    if args.identity != "-":
        sign += ["--options", "runtime", "--timestamp"]
    if args.standalone:
        run_codesign(sign + [str(staging/'Contents/MacOS/tapmesh-mcp')])
    run_codesign(sign + [str(staging)])
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(staging)], check=True)
    if bundle.exists():
        shutil.rmtree(bundle)
    os.replace(staging, bundle)
    suffix='-staging' if args.staging_updates else ''
    package = distribution / (f"TapMesh-{args.version}-standalone-macos-arm64{suffix}.zip" if args.standalone else f"TapMesh-{args.version}-macos-arm64{suffix}.zip")
    if package.exists():
        package.unlink()
    subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(bundle), str(package)], check=True)
    manifest = {
        "product": "TapMesh", "version": args.version, "build_number":args.build_number, "configuration": args.configuration,
        "minimum_macos":"15.0","architecture":"arm64","staging":args.staging_updates,"updater_configured":bool(args.update_feed),
        "signing": "local-ad-hoc" if args.identity == "-" else "developer-id",
        "notarized": False, "sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
        "archive": package.name,
        "runtime": "embedded-local" if args.standalone else "external-private-development",
        "public_release_ready": False,
        "source_compliance_verified": False,
    }
    (distribution / "release-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    if args.install:
        destination = Path.home() / "Applications/TapMesh.app"
        destination.parent.mkdir(exist_ok=True)
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(bundle, destination, symlinks=True)
        from manage import record_installed_app
        record_installed_app(destination)
        print("Installed:", destination)
    print("App:", bundle)
    print("Package:", package)
    print("Notarization is a separate release gate; see dist/release-manifest.json.")


if __name__ == "__main__":
    main()
