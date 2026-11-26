#!/usr/bin/env python3
"""Create a signed drag-to-Applications DMG from an existing signed TapMesh app.

Does not install, launch, notarize, publish, or modify the input application.
"""
import argparse
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile

from bundle_integrity import require_same_bundle


def publish_image(staged, output):
    """Expose the final name only after its copied bytes pass verification."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.tapmesh-image-', suffix='.dmg',
                                         dir=output.parent, delete=False) as destination:
            temporary = Path(destination.name)
            with staged.open('rb') as source:
                shutil.copyfileobj(source, destination)
            destination.flush()
            os.fsync(destination.fileno())
        temporary.chmod(0o644)
        subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(temporary)], check=True)
        subprocess.run(['/usr/bin/hdiutil', 'verify', str(temporary)], check=True)
        # Both names are on the output volume. Linking is atomic, never
        # overwrites an existing file, and safely refuses unsupported volumes.
        output.hardlink_to(temporary)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--identity', required=True, help='Developer ID Application signing identity')
    args = parser.parse_args()
    app = args.app.resolve(strict=True)
    output = args.output.absolute()
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    if app.name != 'TapMesh.app' or info.get('CFBundleIdentifier') != 'io.tapmesh.mac':
        parser.error('Use the complete TapMesh.app bundle.')
    if output.suffix != '.dmg' or output.exists() or output.is_symlink():
        parser.error('Use a new .dmg output path; existing artifacts are never overwritten.')
    output.parent.mkdir(parents=True, exist_ok=True)
    requirement = ('anchor apple generic and certificate leaf[field.1.2.840.113635.100.6.1.13] exists '
                   'and identifier "io.tapmesh.mac"')
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', '-R=' + requirement, str(app)], check=True)
    # App copies and signing stay in the same local cache as app packaging.
    # Network output folders can attach metadata that invalidates app seals.
    cache = Path.home() / 'Library/Caches/TapMesh/dmg-build'
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='tapmesh-dmg-', dir=cache) as work:
        payload = Path(work) / 'payload'
        payload.mkdir()
        copied = payload / app.name
        subprocess.run(['/usr/bin/ditto', str(app), str(copied)], check=True)
        require_same_bundle(app, copied)
        subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', '-R=' + requirement, str(copied)], check=True)
        (payload / 'Applications').symlink_to('/Applications', target_is_directory=True)
        (payload / 'Install TapMesh.txt').write_text(
            'TapMesh ' + info['CFBundleShortVersionString'] + ' Developer Preview\n\n'
            '1. Drag TapMesh.app to Applications.\n'
            '2. Eject this disk image.\n'
            '3. Open TapMesh from Applications in Finder.\n\n'
            'Requires Apple Silicon, macOS 15 or later, and initialized Xcode for device work.\n'
            'Open Settings > AI Agents to copy the MCP connection for your installation.\n'
            'Preview qualification and source: https://github.com/khaled1174/TapMesh\n', encoding='utf-8')
        staged = Path(work) / output.name
        subprocess.run(['/usr/bin/hdiutil', 'create', '-volname', 'TapMesh Developer Preview',
                        '-srcfolder', str(payload), '-format', 'UDZO', '-ov', str(staged)], check=True)
        subprocess.run(['/usr/bin/codesign', '--sign', args.identity, '--timestamp', str(staged)], check=True)
        subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(staged)], check=True)
        subprocess.run(['/usr/bin/hdiutil', 'verify', str(staged)], check=True)
        require_same_bundle(app, copied)
        publish_image(staged, output)
    print(output)


if __name__ == '__main__':
    main()
