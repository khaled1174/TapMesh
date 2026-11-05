#!/usr/bin/env python3
"""Notarize a concrete signed app archive using a private T3 credential file.

Credential JSON: key_id, issuer_id, private_key (App Store Connect .p8 PEM).
Values never appear in output, command arguments, source or release metadata.
"""
import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import uuid


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app',type=Path,required=True)
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--credentials-file',type=Path,required=True)
    parser.add_argument('--manifest',type=Path,required=True)
    args=parser.parse_args()
    descriptor=os.open(args.credentials_file,os.O_RDONLY|os.O_NOFOLLOW)
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
    subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(args.app)],check=True)
    private=Path.home()/'Library/Caches/TapMesh/notarization'
    private.mkdir(parents=True,exist_ok=True,mode=0o700)
    with tempfile.TemporaryDirectory(prefix='submission-',dir=private) as temporary:
        key=Path(temporary)/'notary.p8'
        fd=os.open(key,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as output: output.write(credentials['private_key'])
        result=subprocess.run(['/usr/bin/xcrun','notarytool','submit',str(args.archive),
            '--key',str(key),'--key-id',credentials['key_id'],'--issuer',credentials['issuer_id'],
            '--wait','--timeout','15m','--output-format','json'],capture_output=True)
        try: submission=json.loads(result.stdout)
        except ValueError: submission={}
        identifier=submission.get('id')
        if identifier:
            try: identifier=str(uuid.UUID(identifier))
            except (ValueError,TypeError): identifier=None
        if result.returncode or submission.get('status')!='Accepted':
            print(json.dumps({'notarization':'not_accepted','submission_id':identifier,'exit_code':result.returncode}))
            raise SystemExit(1)
        subprocess.run(['/usr/bin/xcrun','stapler','staple',str(args.app)],check=True)
        subprocess.run(['/usr/bin/xcrun','stapler','validate',str(args.app)],check=True)
        subprocess.run(['/usr/sbin/spctl','--assess','--type','execute',str(args.app)],check=True)
        replacement=args.archive.with_name(args.archive.name+'.notarized.zip')
        if replacement.exists(): raise SystemExit('Review the existing notarized replacement archive before retrying.')
        subprocess.run(['/usr/bin/ditto','-c','-k','--sequesterRsrc','--keepParent',str(args.app),str(replacement)],check=True)
        os.replace(replacement,args.archive)
        import hashlib
        manifest=json.loads(args.manifest.read_text())
        manifest.update(notarized=True,notarization_submission_id=identifier,sha256=hashlib.sha256(args.archive.read_bytes()).hexdigest())
        args.manifest.write_text(json.dumps(manifest,indent=2)+'\n')
        print(json.dumps({'notarization':'accepted_and_stapled','submission_id':identifier,'archive_sha256':manifest['sha256']}))


if __name__=='__main__': main()
