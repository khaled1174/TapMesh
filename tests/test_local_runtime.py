"""Per-user bootstrap boundaries; no device or private NAS service is touched."""
import json
import os
from pathlib import Path
import stat

import pytest

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
