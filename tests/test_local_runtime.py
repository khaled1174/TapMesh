"""Per-user bootstrap boundaries; no device or private NAS service is touched."""
import json
import os
from pathlib import Path
import signal
import socket
import stat
from types import SimpleNamespace

import pytest

import tapmesh_runtime as runtime
from tapmesh_runtime import Profile, RuntimeErrorCode, service_environment, build_roots, listeners


def test_first_launch_creates_private_profile_and_preserves_credentials(tmp_path):
    legacy = tmp_path / 'connection.json'
    legacy.write_text('{"private_nas":"preserve"}')
    profile = Profile(tmp_path / 'Local')
    profile.prepare()
    original = {name: (profile.root / name).read_bytes() for name in profile.tokens}
    profile.prepare()
    assert original == {name: (profile.root / name).read_bytes() for name in profile.tokens}
    assert len(set(original.values())) == 3
    assert stat.S_IMODE(profile.root.stat().st_mode) == 0o700
    assert all(stat.S_IMODE((profile.root / name).stat().st_mode) == 0o600 for name in profile.tokens)
    assert json.loads(legacy.read_text()) == {'private_nas': 'preserve'}


def test_profile_rejects_symlinks_and_corrupt_existing_credentials(tmp_path):
    target = tmp_path / 'untouched'
    target.write_text('keep me')
    root = tmp_path / 'profile'
    root.mkdir()
    (root / 'server.token').symlink_to(target)
    with pytest.raises(RuntimeErrorCode):
        Profile(root).prepare()
    assert target.read_text() == 'keep me'
    (root / 'server.token').unlink()
    (root / 'server.token').write_text('invalid token')
    with pytest.raises(RuntimeErrorCode):
        Profile(root).prepare()


def test_profile_lock_has_exclusive_ownership(tmp_path):
    first = Profile(tmp_path / 'profile')
    second = Profile(first.root)
    first.prepare()
    assert first.acquire()
    assert not second.acquire()
    first.release()
    assert second.acquire()
    second.release()


def test_local_environment_excludes_private_nas_and_inherited_runtime_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv('TAPMESH_IDB_COMPANION', '/private/owner/companion')
    monkeypatch.setenv('PYTHONPATH', '/private/owner/source')
    monkeypatch.setenv('TAPMESH_AGENT_URL', 'http://nas.invalid')
    profile = Profile(tmp_path / 'Local')
    profile.prepare()
    configuration = profile.configure(50101, 50102, 'instance')
    agent, server = service_environment(profile, tmp_path / 'Resources', configuration)
    assert 'PYTHONPATH' not in agent
    assert agent['PYTHONDONTWRITEBYTECODE']=='1'
    assert agent['TAPMESH_BUILD_ROOTS'] == ''
    assert agent['TAPMESH_EXPORT_ROOTS'] == str(profile.root / 'Exports')
    assert agent.get('TAPMESH_IDB_COMPANION') != '/private/owner/companion'
    assert server['TAPMESH_AGENT_URL'] == 'http://127.0.0.1:50101'
    assert agent['TAPMESH_OCR_URL'] == 'http://127.0.0.1:50102'
    assert configuration['api_url'] == 'http://127.0.0.1:50102'
    assert stat.S_IMODE((profile.root / 'connection.json').stat().st_mode) == 0o600


def test_bundled_command_environment_does_not_depend_on_homebrew_or_write_signed_resources(monkeypatch):
    from agent.process import minimal_environment
    monkeypatch.setenv('TAPMESH_BUNDLED_RUNTIME','1')
    monkeypatch.setenv('PATH','/private/developer/bin')
    monkeypatch.setenv('PYTHONPATH','/private/developer/source')
    env=minimal_environment()
    assert env['PATH']=='/usr/bin:/bin:/usr/sbin:/sbin'
    assert env['PYTHONDONTWRITEBYTECODE']=='1'
    assert 'PYTHONPATH' not in env


def test_moved_build_folder_revokes_access_without_blocking_settings(tmp_path):
    profile = Profile(tmp_path / 'Local')
    profile.prepare()
    existing = tmp_path / 'selected-project'
    existing.mkdir()
    permissions = profile.root / 'permissions.json'
    permissions.write_text(json.dumps({'build_roots': [str(existing), str(tmp_path / 'disconnected-volume')]}))
    assert build_roots(profile) == [str(existing)]
    existing.rmdir()
    assert build_roots(profile) == []
    assert len(json.loads(permissions.read_text())['build_roots']) == 2


def test_listeners_keep_loopback_port_ownership_until_handed_to_children():
    import socket
    owned = listeners()
    try:
        addresses = [item.getsockname() for item in owned]
        assert len(set(addresses)) == 2
        assert all(address[0] == '127.0.0.1' for address in addresses)
        for address in addresses:
            with socket.socket() as competitor, pytest.raises(OSError):
                competitor.bind(address)
    finally:
        for item in owned: item.close()


def test_child_logs_are_written_and_bounded(tmp_path):
    from tapmesh_runtime import _open_service_log

    profile = Profile(tmp_path / 'Local')
    profile.prepare()
    logs = profile.root / 'logs'
    logs.mkdir(mode=0o755)
    agent_log = logs / 'agent.log'
    tail = b'b' * (256 * 1024)
    agent_log.write_bytes(b'a' * (768 * 1024) + tail)
    agent_log.chmod(0o644)

    with _open_service_log(profile, 'agent') as output:
        assert agent_log.read_bytes() == tail
        os.write(output.fileno(), b'child stderr\n')

    assert agent_log.read_bytes() == tail + b'child stderr\n'
    assert stat.S_IMODE(agent_log.stat().st_mode) == 0o600
    assert stat.S_IMODE(logs.stat().st_mode) == 0o700


@pytest.mark.parametrize('log_path_kind', ['directory_symlink', 'file_symlink', 'file_hardlink'])
def test_service_log_rejects_links_without_changing_the_target(tmp_path, log_path_kind):
    from tapmesh_runtime import _open_service_log

    profile = Profile(tmp_path / 'Local')
    profile.prepare()
    outside = tmp_path / 'outside'
    outside.mkdir(mode=0o755)
    target = outside / 'agent.log'
    target.write_bytes(b'private contents')
    target.chmod(0o644)
    logs = profile.root / 'logs'
    if log_path_kind == 'directory_symlink':
        logs.symlink_to(outside, target_is_directory=True)
    else:
        logs.mkdir()
        if log_path_kind == 'file_symlink':
            (logs / 'agent.log').symlink_to(target)
        else:
            (logs / 'agent.log').hardlink_to(target)

    with pytest.raises((OSError, RuntimeErrorCode)):
        with _open_service_log(profile, 'agent'):
            pytest.fail('Linked logs must not be opened')

    assert target.read_bytes() == b'private contents'
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    assert stat.S_IMODE(outside.stat().st_mode) == 0o755


class FakeService:
    def __init__(self, name, environment, address, started_at):
        self.name = name
        self.environment = environment
        self.address = address
        self.started_at = started_at
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -signal.SIGTERM

    def wait(self, timeout):
        assert self.returncode is not None
        return self.returncode


@pytest.fixture
def fake_supervisor(tmp_path, monkeypatch):
    profile = Profile(tmp_path / 'Local')
    fake = SimpleNamespace(profile=profile, now=0., children={}, launches=[], events=[], signals={},
                           inherited_secrets=[],
                           on_launch=lambda child: None, on_event=lambda event: None, on_tick=lambda: None)

    def popen(command, *, env, pass_fds, stderr, **kwargs):
        service = command[-1].split('.')[0]
        fd = int(env['TAPMESH_LISTEN_FD'])
        if 'TAPMESH_APPROVAL_TOKEN_FD' in env:
            approval_fd = int(env['TAPMESH_APPROVAL_TOKEN_FD'])
            assert pass_fds == (fd, approval_fd) and fd != approval_fd
            assert 'TAPMESH_APPROVAL_TOKEN_FILE' not in env
            with os.fdopen(os.dup(approval_fd), 'rb') as source:
                fake.inherited_secrets.append((service, approval_fd, source.read()))
        else:
            assert pass_fds == (fd,)
        with socket.socket(fileno=os.dup(fd)) as listener:
            child = FakeService(service, dict(env), listener.getsockname(), fake.now)
        os.write(stderr.fileno(), (service + ' started\n').encode())
        fake.children[service] = child
        fake.launches.append(child)
        fake.on_launch(child)
        return child

    def sleep(seconds):
        fake.now += seconds
        assert fake.now < 120, 'Supervisor did not finish within the fake deadline'
        fake.on_tick()

    def emit(event):
        fake.events.append(event)
        fake.on_event(event)

    def ready(configuration):
        return all(fake.children[service].poll() is None and fake.now >= fake.children[service].started_at + .5
                   for service in ('agent', 'server'))

    fake.run = lambda **options: runtime.supervise(profile, tmp_path / 'Resources', os.getppid(), **options)
    fake.stop = lambda: fake.signals[signal.SIGTERM](signal.SIGTERM, None)
    monkeypatch.setattr(runtime, 'time', SimpleNamespace(monotonic=lambda: fake.now, sleep=sleep))
    monkeypatch.setattr(runtime.subprocess, 'Popen', popen)
    monkeypatch.setattr(runtime.signal, 'signal', fake.signals.__setitem__)
    monkeypatch.setattr(runtime, '_emit', emit)
    monkeypatch.setattr(runtime, '_ready', ready)
    return fake


@pytest.mark.parametrize('service', ['agent', 'server'])
def test_supervisor_restarts_a_dead_service_on_the_same_port(fake_supervisor, service):
    fake = fake_supervisor
    crash_at = None

    def on_event(event):
        nonlocal crash_at
        if event['status'] == 'ready':
            crash_at = fake.now
            fake.children[service].returncode = 1
        else:
            assert fake.now >= fake.children[service].started_at + .5
            fake.stop()

    def on_tick():
        if crash_at is not None:
            os.fstat(int(fake.children[service].environment['TAPMESH_LISTEN_FD']))

    fake.on_event = on_event
    fake.on_tick = on_tick
    fake.run()

    assert [event['status'] for event in fake.events] == ['ready', 'restarted']
    assert fake.events[1] == {'status': 'restarted', 'service': service}
    assert len(fake.launches) == 3
    first, replacement = [child for child in fake.launches if child.name == service]
    assert replacement.address == first.address
    assert replacement.environment == first.environment
    assert replacement.started_at - crash_at == pytest.approx(1, abs=.21)
    assert all(child.poll() is not None for child in fake.launches)
    for child in fake.children.values():
        with pytest.raises(OSError):
            os.fstat(int(child.environment['TAPMESH_LISTEN_FD']))
    assert fake.profile.lock is None
    assert (fake.profile.root / 'logs' / f'{service}.log').read_text() == f'{service} started\n' * 2


def test_supervisor_gives_up_after_five_quick_crashes(fake_supervisor):
    fake = fake_supervisor

    def crash_agent(child):
        if child.name == 'agent':
            child.returncode = 1

    fake.on_launch = crash_agent
    with pytest.raises(RuntimeErrorCode) as raised:
        fake.run()

    assert raised.value.code == 'service_exited'
    moments = [child.started_at for child in fake.launches if child.name == 'agent']
    assert len(moments) == 5
    assert [end - start for start, end in zip(moments, moments[1:])] == pytest.approx([1, 2, 5, 5], abs=.21)
    assert fake.events == []
    assert fake.children['server'].poll() == -signal.SIGTERM
    assert fake.profile.lock is None


def test_supervisor_does_not_spawn_while_stopping_during_backoff(fake_supervisor):
    fake = fake_supervisor

    def crash_agent(event):
        fake.children['agent'].returncode = 1
        stop_at = fake.now + .39

        def stop_in_backoff():
            if fake.now >= stop_at:
                fake.stop()

        fake.on_tick = stop_in_backoff

    fake.on_event = crash_agent
    fake.run()

    assert [child.name for child in fake.launches] == ['agent', 'server']
    assert [event['status'] for event in fake.events] == ['ready']
    assert fake.children['server'].poll() == -signal.SIGTERM
    assert fake.profile.lock is None


def test_pipe_mode_rotates_off_disk_and_reseeds_restarted_children(fake_supervisor):
    fake = fake_supervisor
    fake.profile.prepare()
    old_approval = fake.profile.root / 'approvals.token'
    assert old_approval.is_file()
    secret = b'a' * 64

    def on_event(event):
        if event['status'] == 'ready':
            fake.children['agent'].returncode = 1
        else:
            fake.stop()

    fake.on_event = on_event
    fake.run(approval_secret=secret)

    assert not old_approval.exists()
    configuration = (fake.profile.root / 'connection.json').read_bytes()
    assert 'approval_token_file' not in json.loads(configuration)
    assert secret not in configuration and secret.decode() not in json.dumps(fake.events)
    assert [service for service, _, _ in fake.inherited_secrets] == ['agent', 'server', 'agent']
    for service, fd, data in fake.inherited_secrets:
        assert data == secret + b'\n'
        assert secret not in (fake.profile.root / 'logs' / f'{service}.log').read_bytes()
        with pytest.raises(OSError):
            os.fstat(fd)


def test_attach_does_not_replace_a_pipe_mode_owner(fake_supervisor, monkeypatch):
    fake = fake_supervisor
    owner = Profile(fake.profile.root)
    owner.prepare(approval_file=False)
    configuration = owner.configure(50101, 50102, 'owning-instance', approval_file=False)
    monkeypatch.setattr(runtime, '_ready', lambda candidate: candidate == configuration)
    assert owner.acquire()
    try:
        fake.run(approval_secret=b'b' * 64)
    finally:
        owner.release()

    assert fake.events == [{'status': 'ready', 'config_path': str(owner.root / 'connection.json'),
                            'instance_id': 'owning-instance', 'owner': False}]
    assert fake.launches == [] and fake.inherited_secrets == []
    assert json.loads((owner.root / 'connection.json').read_bytes()) == configuration
    assert not (owner.root / 'approvals.token').exists()


def test_profile_update_fence_blocks_old_restart_and_allows_only_newer_build_under_lock(tmp_path):
    import plistlib
    import time
    profile=Profile(tmp_path/'Local'); profile.prepare()
    resources=tmp_path/'TapMesh.app/Contents/Resources'; resources.mkdir(parents=True)
    info=resources.parent/'Info.plist'
    info.write_bytes(plistlib.dumps({'CFBundleVersion':'2'}))
    fence=profile.root/'update-fence.json'
    fence.write_text(json.dumps({'schema':1,'lease_id':'owner-one','build_number':'2','expires_at':time.time()+300}))
    fence.chmod(0o600)
    with pytest.raises(RuntimeErrorCode,match='installing an update'): profile.check_update_fence(resources)
    assert fence.exists()
    info.write_bytes(plistlib.dumps({'CFBundleVersion':'3'}))
    assert profile.check_update_fence(resources) is True
    with pytest.raises(RuntimeErrorCode): profile.check_update_fence(resources,consume=True)
    assert fence.exists()
    assert profile.acquire()
    assert profile.check_update_fence(resources,consume=True) is True
    assert not fence.exists()
    profile.release()


def test_update_fence_cannot_be_cleared_during_another_atomic_publisher(tmp_path):
    import fcntl
    import plistlib
    import time
    profile=Profile(tmp_path/'Local'); profile.prepare(); assert profile.acquire()
    resources=tmp_path/'TapMesh.app/Contents/Resources'; resources.mkdir(parents=True)
    (resources.parent/'Info.plist').write_bytes(plistlib.dumps({'CFBundleVersion':'3'}))
    fence=profile.root/'update-fence.json'
    fence.write_text(json.dumps({'schema':1,'lease_id':'owner-two','build_number':'2','expires_at':time.time()+300}));fence.chmod(0o600)
    fd=os.open(profile.root/'update-fence.lock',os.O_RDWR|os.O_CREAT,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(RuntimeErrorCode): profile.check_update_fence(resources,consume=True)
        assert json.loads(fence.read_text())['lease_id']=='owner-two'
    finally: os.close(fd); profile.release()


@pytest.mark.asyncio
async def test_shutdown_reaps_multiple_owned_recording_groups_within_one_grace_period(tmp_path):
    import asyncio
    import sys
    import time
    from agent.backends import MacBackend
    from agent.process import Runner
    runner = Runner()
    backend = MacBackend(tmp_path / 'agent', runner=runner)
    children = []
    try:
        for index in range(4):
            process = await asyncio.create_subprocess_exec(sys.executable, '-c',
                'import signal,time; signal.signal(signal.SIGINT,signal.SIG_IGN); signal.signal(signal.SIGTERM,signal.SIG_IGN); print("ready",flush=True); time.sleep(60)',
                start_new_session=True, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            runner.own(process)
            children.append(process)
            assert await process.stdout.readline() == b'ready\n'
            backend.recordings[str(index)] = {'process': process, 'path': tmp_path / f'absent-{index}.mp4',
                'lock': asyncio.Lock(), 'started': time.monotonic(), 'errors': [], 'recording_id': str(index)}
        started = time.monotonic()
        await backend.close()
        assert time.monotonic() - started < 8
        assert all(process.returncode is not None for process in children)
        assert not runner._groups
        assert not backend.socket_root.exists()
    finally:
        await runner.close()
