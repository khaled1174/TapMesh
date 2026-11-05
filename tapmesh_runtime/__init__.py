"""Owned, per-user local services for the standalone Mac application.

No SSH, launch agent, package manager, global configuration, or network download
is needed at first launch. Only this supervisor's child services are stopped.
"""
import fcntl
import json
import math
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import stat
import plistlib
import subprocess
import sys
import time


class RuntimeErrorCode(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def _private_directory(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeErrorCode('unsafe_profile', 'The local profile must be a directory owned by this user.')
    path.chmod(0o700)


def _read_json(path):
    try:
        with open(path, 'rb') as source:
            data = source.read(16385)
        if len(data) > 16384:
            return None
        value = json.loads(data)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


class Profile:
    tokens = ('server.token', 'agent.token', 'approvals.token')

    def __init__(self, root):
        self.root = Path(root).expanduser().absolute()
        self.lock = None

    def prepare(self):
        _private_directory(self.root)
        for name in ('agent-state', 'server-state', 'Exports'):
            _private_directory(self.root / name)
        for name in self.tokens:
            path = self.root / name
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            except FileExistsError:
                fd = None
            if fd is not None:
                with os.fdopen(fd, 'w') as destination:
                    destination.write(secrets.token_hex(32) + '\n')
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(fd, 'rb') as source:
                    info = os.fstat(source.fileno())
                    value = source.read(257).strip()
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or not re.fullmatch(rb'[0-9a-f]{64}', value):
                    raise ValueError()
                path.chmod(0o600)
            except (OSError, ValueError) as exc:
                raise RuntimeErrorCode('unsafe_credential', 'A local credential is invalid; restore the profile rather than replacing its authorization.') from exc

    def acquire(self):
        fd = os.open(self.root / 'runtime.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            os.close(fd)
            raise RuntimeErrorCode('unsafe_profile', 'The local runtime lock is invalid.')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        self.lock = fd
        return True

    def release(self):
        if self.lock is not None:
            os.close(self.lock)
            self.lock = None

    def configure(self, agent_port, server_port, instance):
        configuration = {
            'api_url': f'http://127.0.0.1:{server_port}',
            'agent_url': f'http://127.0.0.1:{agent_port}',
            'server_token_file': str(self.root / 'server.token'),
            'agent_token_file': str(self.root / 'agent.token'),
            'approval_token_file': str(self.root / 'approvals.token'),
            'instance_id': instance, 'profile': 'local',
        }
        temporary = self.root / ('.connection-' + secrets.token_hex(8))
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, 'w') as destination:
                json.dump(configuration, destination)
                destination.write('\n')
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(temporary, self.root / 'connection.json')
        finally:
            temporary.unlink(missing_ok=True)
        return configuration

    def check_update_fence(self, resources, *, consume=False):
        path=self.root/'update-fence.json'
        if not path.exists() and not path.is_symlink(): return False
        if consume and self.lock is None:
            raise RuntimeErrorCode('unsafe_profile','Update recovery requires local runtime ownership.')
        fd=os.open(self.root/'update-fence.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077 or info.st_nlink!=1:
                raise ValueError()
            try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeErrorCode('update_in_progress','Another TapMesh instance is preparing an update. Retry shortly.') from exc
            try: source_fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            except FileNotFoundError: return False
            with os.fdopen(source_fd,'rb') as source:
                info=os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077 or info.st_nlink!=1:
                    raise ValueError()
                raw=source.read(4097)
            value=json.loads(raw)
            if len(raw)>4096 or not isinstance(value,dict) or set(value)!={'schema','lease_id','build_number','expires_at'} or type(value['schema']) is not int or value['schema']!=1:
                raise ValueError()
            lease=value['lease_id']; build=value['build_number']; expiry=value['expires_at']
            if not isinstance(lease,str) or not 1<=len(lease)<=128 or any(not 33<=ord(c)<=126 for c in lease): raise ValueError()
            if not isinstance(build,str) or not re.fullmatch('[1-9][0-9]{0,19}',build) or int(build)>2**64-1: raise ValueError()
            if isinstance(expiry,bool) or not isinstance(expiry,(int,float)) or not math.isfinite(expiry): raise ValueError()
            with (resources.parent/'Info.plist').open('rb') as source: installed=plistlib.load(source).get('CFBundleVersion')
            if not isinstance(installed,str) or not re.fullmatch('[1-9][0-9]{0,19}',installed) or int(installed)>2**64-1: raise ValueError()
            active=expiry>time.time()
            if active and int(installed)<=int(build):
                raise RuntimeErrorCode('update_in_progress','TapMesh is installing an update. Wait for it to relaunch, or retry after the preparation lease expires.')
            if consume:
                # This lock also protects Core's atomic publisher. Re-reading
                # and unlinking under it cannot clear another instance's lease.
                path.unlink()
                directory=os.open(self.root,os.O_RDONLY)
                try: os.fsync(directory)
                finally: os.close(directory)
            return active
        except (ValueError,TypeError,KeyError,OSError) as exc:
            raise RuntimeErrorCode('invalid_update_fence','The private update preparation receipt is invalid. Restore it before restarting local services.') from exc
        finally: os.close(fd)


def service_environment(profile, resources, configuration):
    # Never inherit private development endpoints, shell Python injection,
    # proxy credentials, loader overrides, or a different user's helpers.
    env = {key: os.environ[key] for key in ('HOME', 'TMPDIR', 'LANG', 'DEVELOPER_DIR') if key in os.environ}
    env.update(PATH='/usr/bin:/bin:/usr/sbin:/sbin', PYTHONUNBUFFERED='1')
    env.update({
        'TAPMESH_AGENT_TOKEN_FILE': configuration['agent_token_file'],
        'TAPMESH_SERVER_TOKEN_FILE': configuration['server_token_file'],
        'TAPMESH_APPROVAL_TOKEN_FILE': configuration['approval_token_file'],
        'TAPMESH_INSTANCE_ID': configuration['instance_id'],
        'TAPMESH_AGENT_URL': configuration['agent_url'],
        'TAPMESH_AGENT_PORT': configuration['agent_url'].rsplit(':', 1)[1],
        'TAPMESH_SERVER_PORT': configuration['api_url'].rsplit(':', 1)[1],
        'TAPMESH_OCR_URL': configuration['api_url'],
        'TAPMESH_OCR_ENGINE': 'vision',
        'TAPMESH_NATIVE_TOOLS': str(resources / 'TapMeshTools'),
        'TAPMESH_BUILD_ROOTS': os.pathsep.join(build_roots(profile)),
        'TAPMESH_BUNDLED_RUNTIME': '1',
        'TAPMESH_EXPORT_ROOTS': str(profile.root / 'Exports'),
    })
    companion = resources / 'idb_companion/idb_companion'
    if companion.is_file():
        env['TAPMESH_IDB_COMPANION'] = str(companion)
    agent, server = dict(env), dict(env)
    agent['TAPMESH_STATE_DIR'] = str(profile.root / 'agent-state')
    server['TAPMESH_STATE_DIR'] = str(profile.root / 'server-state')
    return agent, server


def build_roots(profile):
    document = _read_json(profile.root / 'permissions.json')
    if document is None:
        return []
    roots = document.get('build_roots')
    if set(document) != {'build_roots'} or not isinstance(roots, list) or len(roots) > 32:
        raise RuntimeErrorCode('invalid_permissions', 'Review the selected build folders in TapMesh settings.')
    result = []
    for root in roots:
        if not isinstance(root, str) or not Path(root).is_absolute() or os.pathsep in root or '\x00' in root:
            raise RuntimeErrorCode('invalid_permissions', 'A selected build folder is invalid.')
        path = Path(root).resolve()
        # A disconnected volume or moved project revokes effective access. It
        # must not prevent the user opening Settings to remove the old grant.
        if path.is_dir():
            result.append(str(path))
    return list(dict.fromkeys(result))


def listeners():
    """Keep kernel ownership through child startup; no reserve/close/bind race."""
    result = []
    try:
        for _ in range(2):
            listener = socket.socket()
            result.append(listener)
            listener.bind(('127.0.0.1', 0))
            listener.listen(128)
        return result
    except BaseException:
        for listener in result: listener.close()
        raise


def _ready(configuration):
    import httpx
    try:
        instance = configuration['instance_id']
        urls = (configuration['agent_url'], configuration['api_url'])
        if not all(re.fullmatch(r'http://127\.0\.0\.1:[0-9]{1,5}', url) for url in urls):
            return False
        with httpx.Client(timeout=.5, trust_env=False, follow_redirects=False) as client:
            for url in urls:
                response = client.get(url + '/health')
                if response.status_code != 200 or response.json().get('instance_id') != instance:
                    return False
            token = Path(configuration['server_token_file']).read_text().strip()
            return client.get(urls[1] + '/api/tools', headers={'Authorization': 'Bearer ' + token}).status_code == 200
    except (OSError, ValueError, KeyError, TypeError, httpx.HTTPError):
        return False


def _emit(value):
    print(json.dumps(value, separators=(',', ':')), flush=True)


def _stop(process, seconds):
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        # Only the fresh child group owned by this Popen may be escalated.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def supervise(profile, resources, parent_pid):
    if parent_pid != os.getppid():
        raise RuntimeErrorCode('invalid_parent', 'The local runtime must be launched directly by its owner application.')
    _private_directory(profile.root)
    profile.check_update_fence(resources)
    stopped = False

    def stop(signum, frame):
        nonlocal stopped
        stopped = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stop)
    owned=profile.acquire()
    if not owned:
        deadline = time.monotonic() + 45
        while not stopped and os.getppid() == parent_pid and time.monotonic() < deadline:
            fenced=profile.check_update_fence(resources)
            if profile.acquire():
                owned=True
                break
            configuration = _read_json(profile.root / 'connection.json')
            if not fenced and configuration and _ready(configuration):
                _emit({'status': 'ready', 'config_path': str(profile.root / 'connection.json'), 'instance_id': configuration['instance_id'], 'owner': False})
                return
            time.sleep(.2)
        if not owned:
            raise RuntimeErrorCode('profile_in_use', 'Another TapMesh process owns this profile but its services are unavailable.')
    children = []
    try:
        profile.check_update_fence(resources,consume=True)
        profile.prepare()
        sockets = listeners()
        try:
            configuration = profile.configure(*(listener.getsockname()[1] for listener in sockets), secrets.token_hex(16))
            environments = service_environment(profile, resources, configuration)
            for module, env, listener in zip(('agent.app', 'server.app'), environments, sockets):
                env['TAPMESH_LISTEN_FD'] = str(listener.fileno())
                children.append(subprocess.Popen(
                    [sys.executable, '-I', '-B', '-m', module], env=env, cwd=str(profile.root),
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True, pass_fds=(listener.fileno(),),
                ))
        finally:
            for listener in sockets: listener.close()
        deadline = time.monotonic() + 45
        while not stopped and os.getppid() == parent_pid:
            if any(child.poll() is not None for child in children):
                raise RuntimeErrorCode('service_exited', 'A bundled local service stopped. Reconnect to start it again.')
            if _ready(configuration):
                break
            if time.monotonic() >= deadline:
                raise RuntimeErrorCode('startup_timeout', 'The bundled local services did not become ready in time.')
            time.sleep(.2)
        if stopped or os.getppid() != parent_pid:
            return
        _emit({'status': 'ready', 'config_path': str(profile.root / 'connection.json'), 'instance_id': configuration['instance_id'], 'owner': True})
        while not stopped and os.getppid() == parent_pid:
            if any(child.poll() is not None for child in children):
                raise RuntimeErrorCode('service_exited', 'A bundled local service stopped. Reconnect to start it again.')
            time.sleep(.2)
    finally:
        # Stop public intake before the device agent. ASGI graceful shutdown
        # cancels in-flight requests and lets their owned process cleanup finish.
        for index, child in enumerate(reversed(children)):
            _stop(child, 5 if index == 0 else 40)
        profile.release()


def stdio_bridge(profile, app_bundle):
    """Resolve dynamic ports privately and cold-start the owning native app."""
    configuration = _read_json(profile.root / 'connection.json')
    if not configuration or not _ready(configuration):
        if not (app_bundle / 'Contents/MacOS/TapMesh').is_file():
            raise RuntimeErrorCode('installation_incomplete', 'Install the complete TapMesh app before connecting an MCP client.')
        launch_environment = {key: os.environ[key] for key in ('HOME', 'TMPDIR', 'LANG') if key in os.environ}
        launch_environment['PATH'] = '/usr/bin:/bin:/usr/sbin:/sbin'
        subprocess.run(['/usr/bin/open', '-g', '-n', '--env', 'TAPMESH_PROFILE_DIR='+str(profile.root), str(app_bundle)], check=True, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, env=launch_environment)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            configuration = _read_json(profile.root / 'connection.json')
            if configuration and _ready(configuration):
                break
            time.sleep(.2)
        else:
            raise RuntimeErrorCode('startup_timeout', 'Open TapMesh and check its local runtime before reconnecting the MCP client.')
    os.environ['TAPMESH_SERVER_TOKEN_FILE'] = str(profile.root / 'server.token')
    os.environ['TAPMESH_SERVER_URL'] = configuration['api_url']
    from server.stdio import main as run_stdio
    run_stdio()


def main():
    import argparse
    parser = argparse.ArgumentParser(description='TapMesh bundled local runtime')
    subparsers = parser.add_subparsers(dest='command', required=True)
    supervisor = subparsers.add_parser('supervise')
    supervisor.add_argument('--state-dir', type=Path, required=True)
    supervisor.add_argument('--resources', type=Path, required=True)
    supervisor.add_argument('--parent-pid', type=int, required=True)
    bridge = subparsers.add_parser('stdio')
    bridge.add_argument('--app-bundle', type=Path, required=True)
    bridge.add_argument('--state-dir', type=Path, default=Path.home() / 'Library/Application Support/TapMesh/Local')
    args = parser.parse_args()
    try:
        if args.command == 'stdio':
            stdio_bridge(Profile(args.state_dir), args.app_bundle.absolute())
        else:
            supervise(Profile(args.state_dir), args.resources.absolute(), args.parent_pid)
    except RuntimeErrorCode as exc:
        if args.command == 'stdio':
            print(exc.message, file=sys.stderr)
        else:
            _emit({'status': 'error', 'code': exc.code, 'message': exc.message})
        raise SystemExit(1)
    except (OSError, ValueError):
        message = 'TapMesh could not prepare its private local runtime. Check available storage and profile permissions.'
        if args.command == 'stdio': print(message, file=sys.stderr)
        else: _emit({'status': 'error', 'code': 'local_runtime_unavailable', 'message': message})
        raise SystemExit(1)
