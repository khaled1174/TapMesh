#!/usr/bin/env python3
"""Notarize a concrete signed app archive using a private T3 credential file.

Credential JSON: key_id, issuer_id, private_key (App Store Connect .p8 PEM).
Private key bytes stay out of output, arguments, source and release metadata.
"""
import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import stat
import subprocess
import tempfile
import uuid

from bundle_integrity import file_digest, require_same_bundle


DEVELOPER_ID_REQUIREMENT = ('anchor apple generic and certificate leaf[field.1.2.840.113635.100.6.1.13] exists '
                            'and identifier "io.tapmesh.mac"')


def verify_app(app: Path):
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict',
                    '-R='+DEVELOPER_ID_REQUIREMENT, str(app)], check=True)


def release_inputs(app, archive, manifest_path):
    try:
        manifest=json.loads(manifest_path.read_text())
        info=plistlib.loads((app/'Contents/Info.plist').read_bytes())
        if (app.name!='TapMesh.app' or info['CFBundleIdentifier']!='io.tapmesh.mac'
                or manifest['product']!='TapMesh' or manifest['archive']!=archive.name
                or manifest['signing']!='developer-id'
                or str(manifest['build_number'])!=info['CFBundleVersion'] or manifest['version']!=info['CFBundleShortVersionString']
                or file_digest(archive)!=manifest['sha256']):
            raise ValueError()
        return manifest
    except (OSError, ValueError, KeyError, TypeError, plistlib.InvalidFileException):
        raise SystemExit('The app, ZIP, and release manifest must describe the same signed TapMesh build.') from None


def preflight(app, archive, manifest_path, workspace):
    snapshot=workspace/archive.name
    shutil.copyfile(archive,snapshot)
    manifest=release_inputs(app,snapshot,manifest_path)
    destination=workspace/'unpacked'
    destination.mkdir()
    subprocess.run(['/usr/bin/ditto','-x','-k',str(snapshot),str(destination)],check=True)
    archived_app=destination/app.name
    if set(destination.iterdir())!={archived_app}:
        raise SystemExit('The ZIP must contain only the signed TapMesh application.')
    try:
        require_same_bundle(app, archived_app)
    except (OSError, ValueError):
        raise SystemExit('The signed app and the supplied ZIP contain different application bundles.') from None
    verify_app(archived_app)
    verify_app(app)
    return snapshot, archived_app, manifest


def read_credentials(path):
    descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(descriptor,'rb') as source:
        details=os.fstat(source.fileno())
        if not stat.S_ISREG(details.st_mode) or details.st_uid!=os.getuid() or details.st_mode&0o077 or details.st_nlink!=1:
            raise SystemExit('The credential file must be private and owned by this user.')
        raw=source.read(32769)
    try:
        credentials=json.loads(raw)
        if (not isinstance(credentials,dict) or set(credentials)!={'key_id','issuer_id','private_key'}
                or any(not isinstance(value,str) for value in credentials.values())
                or not re.fullmatch('[A-Z0-9]{10}',credentials['key_id'])
                or str(uuid.UUID(credentials['issuer_id']))!=credentials['issuer_id'].lower()
                or not credentials['private_key'].startswith('-----BEGIN PRIVATE KEY-----') or len(raw)>32768):
            raise ValueError()
    except (ValueError,KeyError,TypeError,AssertionError):
        raise SystemExit('The private notarization credential file has an invalid format.')
    return credentials


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app',type=Path,required=True)
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--credentials-file',type=Path,required=True)
    parser.add_argument('--manifest',type=Path,required=True)
    args=parser.parse_args()
    replacement=args.archive.with_name(args.archive.name+'.notarized.zip')
    if replacement.exists() or replacement.is_symlink():
        raise SystemExit('Review the existing notarized replacement archive before retrying.')
    with tempfile.TemporaryDirectory(prefix='tapmesh-notarization-') as temporary:
        snapshot, archived_app, manifest=preflight(args.app,args.archive,args.manifest,Path(temporary))
        credentials=read_credentials(args.credentials_file)
        key=Path(temporary)/'notary.p8'
        fd=os.open(key,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as output: output.write(credentials['private_key'])
        result=subprocess.run(['/usr/bin/xcrun','notarytool','submit',str(snapshot),
            '--key',str(key),'--key-id',credentials['key_id'],'--issuer',credentials['issuer_id'],
            '--wait','--timeout','15m','--output-format','json'],capture_output=True)
        try: submission=json.loads(result.stdout)
        except ValueError: submission={}
        if not isinstance(submission,dict): submission={}
        identifier=submission.get('id')
        if identifier:
            try: identifier=str(uuid.UUID(identifier))
            except (ValueError,TypeError): identifier=None
        if result.returncode or submission.get('status')!='Accepted':
            print(json.dumps({'notarization':'not_accepted','submission_id':identifier,'exit_code':result.returncode}))
            raise SystemExit(1)
        current_manifest=release_inputs(args.app,args.archive,args.manifest)
        if current_manifest['sha256']!=manifest['sha256']:
            raise SystemExit('The signed ZIP changed during notarization. Review the submitted build before continuing.')
        try:
            require_same_bundle(args.app, archived_app)
        except (OSError, ValueError):
            raise SystemExit('The signed application bundle changed during notarization. Review the submitted build before continuing.') from None
        manifest=current_manifest
        subprocess.run(['/usr/bin/xcrun','stapler','staple',str(args.app)],check=True)
        subprocess.run(['/usr/bin/xcrun','stapler','validate',str(args.app)],check=True)
        subprocess.run(['/usr/sbin/spctl','--assess','--type','execute',str(args.app)],check=True)
        subprocess.run(['/usr/bin/ditto','-c','-k','--sequesterRsrc','--keepParent',str(args.app),str(replacement)],check=True)
        os.replace(replacement,args.archive)
        manifest.update(notarized=True,notarization_submission_id=identifier,sha256=file_digest(args.archive))
        args.manifest.write_text(json.dumps(manifest,indent=2)+'\n')
        print(json.dumps({'notarization':'accepted_and_stapled','submission_id':identifier,'archive_sha256':manifest['sha256']}))


if __name__=='__main__': main()
