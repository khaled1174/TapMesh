#!/usr/bin/env python3
"""Prepare disposable, signed Sparkle update fixtures and serve them on loopback.

The destination must be empty. This command never launches or installs either
app. After its ready JSON line, change control.json's mode from "tampered" to
"interrupted" or "valid" while exercising the installed copy manually. Stop the
owned fixture server with SIGINT/SIGTERM. Its request log contains bounded mode,
route and outcome metadata only. Publisher signing uses the macOS Keychain;
--ephemeral-test-key uses a fresh in-memory key for disposable fixtures only.
"""
from __future__ import annotations

import argparse
import base64
from collections import deque
from datetime import datetime, timezone
from email.utils import format_datetime
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import signal
import socket
import stat
import subprocess
import threading
import xml.etree.ElementTree as ET


MAX_ARCHIVE_BYTES = 192 * 1024 * 1024
INTERRUPTED_BYTES = 1024 * 1024
SIGN_UPDATE_SHA256 = '43c249771bafc3aa581228abae00731a012d324691b8292860896635050be76b'
SPARKLE_NS = 'http://www.andymatuschak.org/xml-namespaces/sparkle'
MODES = frozenset({'tampered', 'interrupted', 'valid'})


class QualificationError(Exception):
    """A bounded error that does not include signing-tool output."""


def command(arguments, *, timeout=180, required=True, private_input=None):
    try:
        options = {'stdin': subprocess.DEVNULL} if private_input is None else {'input': private_input}
        result = subprocess.run([str(item) for item in arguments], **options,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise QualificationError('The '+Path(str(arguments[0])).name+' step did not finish within its deadline.') from exc
    if required and result.returncode:
        raise QualificationError('The '+Path(str(arguments[0])).name+' step failed with exit code '+str(result.returncode)+'.')
    return result


def private_json(path: Path, value):
    temporary = path.with_name('.'+path.name+'.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, 'w') as destination:
            json.dump(value, destination, indent=2)
            destination.write('\n')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_destination(destination: Path):
    if destination.is_symlink():
        raise QualificationError('The qualification destination must not be a symlink.')
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise QualificationError('Use a new, empty qualification destination.')
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = destination.stat()
    if info.st_uid != os.getuid():
        raise QualificationError('The qualification destination must belong to this user.')
    destination.chmod(0o700)


def public_key(path: Path):
    with path.open('rb') as source:
        encoded = source.read(257).strip()
    try:
        decoded = base64.b64decode(encoded, validate=True)
        if len(decoded) != 32 or base64.b64encode(decoded) != encoded:
            raise ValueError()
        return encoded.decode('ascii'), decoded
    except (ValueError, UnicodeError) as exc:
        raise QualificationError('Use the canonical base64 Sparkle Ed25519 public-key file.') from exc


def signing_team(bundle: Path):
    result = command(['/usr/bin/codesign', '--display', '--verbose=4', bundle])
    match = re.search(rb'^TeamIdentifier=([A-Z0-9]+)$', result.stderr, re.MULTILINE)
    if not match or match.group(1) == b'NOTSET':
        raise QualificationError('The base app must have a Developer ID team signature.')
    return match.group(1)


def stage_app(source: Path, destination: Path, info: dict, build: int, feed: str, key: str,
              identity: str, expected_team: bytes):
    shutil.copytree(source, destination, symlinks=True)
    staged = dict(info)
    staged.update(CFBundleVersion=str(build), TapMeshUpdateStagingMode=True,
                  SUFeedURL=feed, SUPublicEDKey=key, SUEnableAutomaticChecks=False,
                  SUSendProfileInfo=False)
    (destination/'Contents/Info.plist').write_bytes(plistlib.dumps(staged))
    # Nested code is unchanged. Re-seal only the outer app with the same team.
    command(['/usr/bin/codesign', '--force', '--sign', identity, '--options', 'runtime', '--timestamp', destination])
    command(['/usr/bin/codesign', '--verify', '--deep', '--strict', destination])
    if signing_team(destination) != expected_team:
        raise QualificationError('Staging copies must use the base application signing team.')


def sign_and_verify(archive: Path, tampered: Path, tool: Path, account: str, key: bytes, *, test_seed=None):
    if hashlib.sha256(tool.read_bytes()).hexdigest() != SIGN_UPDATE_SHA256:
        raise QualificationError('Use the checksum-qualified Sparkle 2.10.1 sign_update tool.')
    signing_arguments = ['--account', account] if test_seed is None else ['--ed-key-file', '-']
    private_input = None if test_seed is None else base64.b64encode(test_seed)+b'\n'
    signed = command([tool, *signing_arguments, '-p', archive], private_input=private_input)
    try:
        signature = signed.stdout.decode('ascii').strip()
        raw_signature = base64.b64decode(signature, validate=True)
        if len(raw_signature) != 64 or base64.b64encode(raw_signature).decode() != signature:
            raise ValueError()
    except (ValueError, UnicodeError) as exc:
        raise QualificationError('The signing tool returned an invalid public signature.') from exc
    shutil.copyfile(archive, tampered)
    with tampered.open('r+b') as output:
        output.seek(archive.stat().st_size//2)
        original = output.read(1)
        output.seek(-1, os.SEEK_CUR)
        output.write(bytes([original[0] ^ 1]))
    assert archive.stat().st_size == tampered.stat().st_size
    command([tool, *signing_arguments, '--verify', archive, signature], private_input=private_input)
    rejected = command([tool, *signing_arguments, '--verify', tampered, signature], required=False, private_input=private_input)
    if rejected.returncode == 0:
        raise QualificationError('The signing tool accepted a modified archive.')
    # Check against the public key actually embedded in both apps, independently
    # of the Keychain account used by the official signer/verifier.
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    verifier = Ed25519PublicKey.from_public_bytes(key)
    try:
        verifier.verify(raw_signature, archive.read_bytes())
    except InvalidSignature as exc:
        raise QualificationError('The supplied public key does not match the signed update.') from exc
    try:
        verifier.verify(raw_signature, tampered.read_bytes())
    except InvalidSignature:
        return signature
    raise QualificationError('Independent public-key verification accepted the modified archive.')


def appcast(build: int, version: str, minimum_macos: str, feed_root: str, size: int, signature: str):
    ET.register_namespace('sparkle', SPARKLE_NS)
    rss = ET.Element('rss', {'version': '2.0'})
    channel = ET.SubElement(rss, 'channel')
    ET.SubElement(channel, 'title').text = 'TapMesh local update qualification'
    item = ET.SubElement(channel, 'item')
    ET.SubElement(item, 'title').text = f'TapMesh qualification build {build}'
    ET.SubElement(item, 'pubDate').text = format_datetime(datetime.now(timezone.utc))
    ET.SubElement(item, f'{{{SPARKLE_NS}}}version').text = str(build)
    ET.SubElement(item, f'{{{SPARKLE_NS}}}shortVersionString').text = version
    ET.SubElement(item, f'{{{SPARKLE_NS}}}minimumSystemVersion').text = minimum_macos
    ET.SubElement(item, 'description').text = 'Disposable local signed update test.'
    ET.SubElement(item, 'enclosure', {
        'url': feed_root+'/update.zip', 'length': str(size), 'type': 'application/octet-stream',
        f'{{{SPARKLE_NS}}}edSignature': signature,
        f'{{{SPARKLE_NS}}}version': str(build), f'{{{SPARKLE_NS}}}shortVersionString': version,
    })
    return ET.tostring(rss, encoding='utf-8', xml_declaration=True)


class FixtureState:
    def __init__(self, destination: Path):
        self.destination = destination
        self.control = destination/'control.json'
        self.events = deque(maxlen=100)
        self.lock = threading.Lock()

    def mode(self):
        try:
            descriptor = os.open(self.control, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, 'rb') as source:
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    return 'invalid'
                encoded = source.read(1025)
                if len(encoded) > 1024:
                    return 'invalid'
                value = json.loads(encoded)
            return value['mode'] if isinstance(value, dict) and set(value) == {'mode'} and value['mode'] in MODES else 'invalid'
        except (OSError, ValueError, KeyError, TypeError):
            return 'invalid'

    def event(self, route, mode, outcome):
        with self.lock:
            self.events.append({'route': route, 'mode': mode, 'outcome': outcome})
            try:
                private_json(self.destination/'requests.json', {'requests': list(self.events)})
            except OSError:
                pass  # A logging/storage failure must not dump request context.


class FixtureServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    request_queue_size = 8

    def __init__(self, address, state):
        self.state = state
        self.slots = threading.BoundedSemaphore(4)
        super().__init__(address, FixtureHandler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(timeout=2):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        self.state.event('request', 'invalid', 'failed')


class FixtureHandler(BaseHTTPRequestHandler):
    server_version = 'TapMeshQualification'
    sys_version = ''

    def log_message(self, format, *arguments):
        pass  # Never log caller-controlled URLs, headers, bodies or addresses.

    def do_HEAD(self):
        self.serve(body=False)

    def do_GET(self):
        self.serve(body=True)

    def serve(self, *, body):
        self.connection.settimeout(10)
        state = self.server.state
        mode = state.mode()
        route = 'appcast' if self.path == '/appcast.xml' else 'update' if self.path == '/update.zip' else 'denied'
        if route == 'denied' or mode == 'invalid':
            self.send_response(404 if route == 'denied' else 503)
            self.send_header('Content-Length', '0')
            self.end_headers()
            state.event(route, mode, 'rejected')
            return
        path = state.destination/('appcast.xml' if route == 'appcast' else 'update-tampered.zip' if mode == 'tampered' else 'update.zip')
        self.send_response(200)
        self.send_header('Content-Type', 'application/rss+xml' if route == 'appcast' else 'application/octet-stream')
        self.send_header('Content-Length', str(path.stat().st_size))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        if not body:
            state.event(route, mode, 'headers')
            return
        remaining = INTERRUPTED_BYTES if route == 'update' and mode == 'interrupted' else None
        try:
            with path.open('rb') as source:
                while chunk := source.read(min(256*1024, remaining) if remaining is not None else 256*1024):
                    self.wfile.write(chunk)
                    if remaining is not None:
                        remaining -= len(chunk)
                        if remaining == 0:
                            self.wfile.flush()
                            self.connection.shutdown(socket.SHUT_RDWR)
                            state.event(route, mode, 'interrupted')
                            return
            state.event(route, mode, 'complete')
        except (BrokenPipeError, ConnectionError, TimeoutError, OSError):
            state.event(route, mode, 'client_closed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', required=True, type=Path)
    parser.add_argument('--destination', required=True, type=Path)
    parser.add_argument('--identity', required=True)
    key_mode = parser.add_mutually_exclusive_group(required=True)
    key_mode.add_argument('--public-key-file', type=Path)
    key_mode.add_argument('--ephemeral-test-key', action='store_true',
                          help='Generate a new in-memory signing key for these disposable copies; does not qualify the publisher key')
    parser.add_argument('--sparkle-tools', required=True, type=Path)
    parser.add_argument('--keychain-account', default='io.tapmesh.mac')
    args = parser.parse_args()
    destination = args.destination.expanduser().absolute()
    bundle = args.bundle.expanduser().resolve()
    server = None
    thread = None
    stopped = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda signum, frame: stopped.set())
    try:
        if not args.identity.startswith('Developer ID Application:') or not re.fullmatch(r'[A-Za-z0-9._-]{1,128}', args.keychain_account):
            raise QualificationError('Use an existing Developer ID Application identity and a valid Keychain account name.')
        info = plistlib.loads((bundle/'Contents/Info.plist').read_bytes())
        if info.get('CFBundleIdentifier') != 'io.tapmesh.mac' or not (bundle/'Contents/MacOS/TapMesh').is_file():
            raise QualificationError('Use the signed TapMesh application bundle.')
        build = int(info['CFBundleVersion'])
        if not 0 < build < 2**64-1:
            raise QualificationError('The base build must be a positive incrementable integer.')
        test_seed = None
        if args.ephemeral_test_key:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption
            test_key = Ed25519PrivateKey.generate()
            test_seed = test_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
            key_bytes = test_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
            key_text = base64.b64encode(key_bytes).decode('ascii')
        else:
            key_text, key_bytes = public_key(args.public_key_file.expanduser())
        command(['/usr/bin/codesign', '--verify', '--deep', '--strict', bundle])
        team = signing_team(bundle)
        prepare_destination(destination)
        state = FixtureState(destination)
        server = FixtureServer(('127.0.0.1', 0), state)
        port = server.server_address[1]
        feed_root = f'http://127.0.0.1:{port}'
        installed = destination/'Installed/TapMesh.app'
        candidate = destination/'Candidate/TapMesh.app'
        stage_app(bundle, installed, info, build, feed_root+'/appcast.xml', key_text, args.identity, team)
        stage_app(bundle, candidate, info, build+1, feed_root+'/appcast.xml', key_text, args.identity, team)
        archive = destination/'update.zip'
        command(['/usr/bin/ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', candidate, archive])
        if not INTERRUPTED_BYTES < archive.stat().st_size <= MAX_ARCHIVE_BYTES:
            raise QualificationError('The qualification update must be between 1 MiB and 192 MiB.')
        signature = sign_and_verify(archive, destination/'update-tampered.zip', args.sparkle_tools.expanduser()/'bin/sign_update', args.keychain_account, key_bytes, test_seed=test_seed)
        test_seed = None
        if args.ephemeral_test_key:
            test_key = None
        (destination/'appcast.xml').write_bytes(appcast(build+1, str(info['CFBundleShortVersionString']),
            str(info.get('LSMinimumSystemVersion', '15.0')), feed_root, archive.stat().st_size, signature))
        private_json(state.control, {'mode': 'tampered'})
        private_json(destination/'qualification.json', {'installed_build': build, 'candidate_build': build+1,
            'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
            'public_signature_checks': 'passed', 'signing_key': 'ephemeral-test-only' if args.ephemeral_test_key else 'publisher-keychain',
            'installed_app': str(installed), 'port': port, 'server_pid': os.getpid()})
        thread = threading.Thread(target=server.serve_forever, name='TapMesh update fixture', daemon=True)
        thread.start()
        print(json.dumps({'installed_app': str(installed), 'port': port, 'control_file': str(state.control),
                          'server_pid': os.getpid(), 'public_signature_checks': 'passed'}, separators=(',', ':')), flush=True)
        stopped.wait()
    except QualificationError as exc:
        print('TapMesh update qualification: '+str(exc), file=__import__('sys').stderr)
        raise SystemExit(1)
    except (OSError, ValueError, KeyError, ImportError, subprocess.SubprocessError) as exc:
        import traceback
        frame=traceback.extract_tb(exc.__traceback__)[-1]
        print('TapMesh update qualification preparation failed: '+type(exc).__name__+' at '+frame.name+':'+str(frame.lineno)+'. Check the signed base app, empty local destination, public key and existing Keychain signing account.', file=__import__('sys').stderr)
        raise SystemExit(1)
    finally:
        if server:
            if thread: server.shutdown()
            server.server_close()
        if thread: thread.join(timeout=5)


if __name__ == '__main__':
    main()
