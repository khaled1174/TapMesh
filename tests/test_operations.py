"""Operations regressions run against fake Docker and private temporary state only."""
import importlib.util
import json
from pathlib import Path
import plistlib
import sqlite3
import subprocess
import sys
import tarfile

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(f"tapmesh_{name}", ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def ops(tmp_path, monkeypatch):
    module = load_script("manage")
    state = tmp_path / "private-state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr(module, "STATE", state)
    monkeypatch.setattr(module, "PLIST", tmp_path / "LaunchAgents/io.tapmesh.supervisor.plist")
    module.private_json(state / "connection.json", {
        "owner_id": "c" * 32, "ssh_alias": "test-nas", "nas_hostname": "test.invalid",
        "host_key_alias": "test-key", "remote_project_root": "/test/project",
        "python": sys.executable, "agent_url": "http://test.invalid:48792",
        "api_url": "http://test.invalid:48793",
    })
    for name in ("server.token", "agent.token", "approvals.token"):
        (state / name).write_text("fixture-secret-never-log\n")
        (state / name).chmod(0o600)
    return module


class Docker:
    def __init__(self, *, owned=True, failure=None, healthy=True):
        self.old = "a" * 64
        self.new = "b" * 64
        self.failure, self.healthy = failure, healthy
        self.calls = []
        self.containers = {self.old: {
            "Id": self.old, "Name": "/tapmesh-server", "Image": "sha256:" + "d" * 64,
            "Config": {"Labels": {"io.tapmesh.project": "tapmesh", "io.tapmesh.owner": "c" * 32} if owned else {},
                       "Cmd": ["python", "-m", "server.app"]},
            "HostConfig": {"NetworkMode": "host"},
            "Mounts": [{"Type": "bind", "Source": "/Volume1/.tapmesh/secrets", "Destination": "/run/devicebridge", "RW": False},
                       {"Type": "bind", "Source": "/Volume1/.tapmesh/server-state", "Destination": "/var/lib/devicebridge", "RW": True}],
            "State": {"Running": True, "Health": {"Status": "healthy"}},
        }}

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        output = b""
        if argv[0] != "docker":
            return subprocess.CompletedProcess(argv, 0, output, b"")
        action = argv[1]
        if action == "ps":
            if "name=^/tapmesh-server$" in argv:
                matches = [key for key, value in self.containers.items() if value["Name"] == "/tapmesh-server"]
            else:
                matches = [key for key, value in self.containers.items() if value.get("Config", {}).get("Labels", {}).get("io.tapmesh.owner") == "c" * 32]
            output = "\n".join(matches).encode()
        elif action in {"inspect", "container"}:
            key = argv[-1]
            if key not in self.containers:
                key = next((key for key, value in self.containers.items() if value["Name"] == "/" + argv[-1]), None)
            if key is None:
                raise subprocess.CalledProcessError(1, argv, stderr=b"No such container")
            output = json.dumps([self.containers[key]]).encode()
        elif action == "run":
            if self.failure == "run":
                raise subprocess.CalledProcessError(1, argv, stderr=b"fixture failed start")
            labels = dict(argv[index + 1].split("=", 1) for index, argument in enumerate(argv) if argument == "--label")
            self.containers[self.new] = {
                "Id": self.new, "Name": "/tapmesh-server", "Image": "sha256:" + "e" * 64,
                "Config": {"Labels": labels},
                "State": {"Running": True, "Health": {"Status": "healthy" if self.healthy else "unhealthy"}},
            }
            output = (self.new + "\n").encode()
        elif action in {"stop", "start", "rm"}:
            key = argv[-1]
            if key not in self.containers:
                key = next(key for key, value in self.containers.items() if value["Name"] == "/" + argv[-1])
            if action == "rm":
                del self.containers[key]
            else:
                self.containers[key]["State"]["Running"] = action == "start"
        elif action == "rename":
            self.containers[argv[-2]]["Name"] = "/" + argv[-1]
        return subprocess.CompletedProcess(argv, 0, output, b"")


def test_deploy_refuses_foreign_container_before_mutation(ops, monkeypatch):
    docker = Docker(owned=False)
    monkeypatch.setattr(ops, "remote_command", docker)
    with pytest.raises((SystemExit, RuntimeError), match="(?i)(foreign|owner|owned)"):
        ops.deploy()
    assert all(call[1] not in {"stop", "rm", "rename", "build", "run"} for call in docker.calls if call[0] == "docker")


@pytest.mark.parametrize("failure,healthy", [("run", True), (None, False)])
def test_failed_deploy_restores_previous_release(ops, monkeypatch, capsys, failure, healthy):
    docker = Docker(failure=failure, healthy=healthy)
    monkeypatch.setattr(ops, "remote_command", docker)
    with pytest.raises((SystemExit, RuntimeError, subprocess.CalledProcessError)):
        ops.deploy()
    previous = docker.containers[docker.old]
    assert previous["Name"] == "/tapmesh-server"
    assert previous["State"]["Running"] is True
    assert docker.new not in docker.containers
    assert "Deployed" not in capsys.readouterr().out


@pytest.mark.parametrize("failed_action", ["run", "rename"])
def test_deploy_restores_even_when_ssh_response_is_lost_after_mutation(ops, monkeypatch, failed_action):
    docker = Docker()
    lost_response = False

    def remote(argv, **kwargs):
        nonlocal lost_response
        result = docker(argv, **kwargs)
        if argv[:2] == ["docker", failed_action] and not lost_response:
            lost_response = True
            raise subprocess.TimeoutExpired(argv, 1)
        return result

    monkeypatch.setattr(ops, "remote_command", remote)
    with pytest.raises(RuntimeError, match="previous release restored"):
        ops.deploy()
    assert docker.containers[docker.old]["Name"] == "/tapmesh-server"
    assert docker.containers[docker.old]["State"]["Running"] is True
    assert docker.new not in docker.containers


def test_missing_candidate_health_has_a_deadline(ops, monkeypatch):
    docker = Docker()
    docker.containers[docker.old]["State"]["Health"]["Status"] = "starting"
    monkeypatch.setattr(ops, "remote_command", docker)
    with pytest.raises(RuntimeError, match="deadline"):
        ops.wait_container_healthy(docker.old, timeout=0)


def test_successful_deploy_retains_old_release_and_labels_new(ops, monkeypatch):
    docker = Docker()
    monkeypatch.setattr(ops, "remote_command", docker)
    ops.deploy()
    assert docker.containers[docker.old]["Name"].startswith("/tapmesh-server-previous-")
    assert docker.containers[docker.old]["State"]["Running"] is False
    run = next(call for call in docker.calls if call[:2] == ["docker", "run"])
    assert "io.tapmesh.owner=" + "c" * 32 in run
    assert "io.tapmesh.project=tapmesh" in run


def test_active_log_rotates_and_never_records_child_bodies(ops):
    sink = ops.BoundedLogSink(ops.STATE / "child.log", max_bytes=128)
    child = subprocess.Popen([sys.executable, "-c", "import sys;sys.stdout.write('secret-body' * 20000)"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    reader = ops.drain_child_output(child.stdout, sink, "fixture")
    assert child.wait(timeout=10) == 0
    reader.join(timeout=10)
    assert not reader.is_alive()
    sink.close()
    current, previous = ops.STATE / "child.log", ops.STATE / "child.log.1"
    assert current.stat().st_size <= 128
    assert previous.exists() and previous.stat().st_size <= 128
    assert b"secret-body" not in current.read_bytes() + previous.read_bytes()


def test_diagnostics_preserve_reference_and_stack_without_arbitrary_bodies(ops):
    value = {'event': 'unexpected_backend_exception', 'diagnostic_id': 'a' * 32,
             'exception_type': 'PermissionError', 'stack': [{'file': 'process.py', 'line': 41, 'function': '_group_exists'}]}
    assert json.loads(ops.diagnostic_metadata(json.dumps(value).encode())) == value
    assert json.loads(ops.diagnostic_metadata(b'WARNING:tapmesh.diagnostics:' + json.dumps(value).encode())) == value
    for unsafe in [value | {'message': 'secret'}, value | {'stack': [{'file': '/private/secret', 'line': 1, 'function': 'run'}]}, value | {'exception_type': 'secret body'}, ['secret'], None]:
        assert ops.diagnostic_metadata(json.dumps(unsafe).encode()) is None


def test_existing_oversized_log_is_bounded_without_restart(ops):
    path = ops.STATE / "supervisor.log"
    path.write_bytes(b"x" * 1000)
    path.chmod(0o600)
    previous = path.with_name(path.name + ".1")
    previous.write_bytes(b"y" * 1000)
    previous.chmod(0o600)
    sink = ops.BoundedLogSink(path, max_bytes=128)
    assert path.stat().st_size <= 128
    assert previous.stat().st_size <= 128
    for _ in range(20):
        sink.write(b"safe lifecycle\n")
    sink.close()
    assert path.stat().st_size <= 128
    assert path.with_name(path.name + ".1").stat().st_size <= 128


def test_stop_nas_refuses_foreign_and_uses_verified_full_id(ops, monkeypatch):
    foreign = Docker(owned=False)
    monkeypatch.setattr(ops, "remote_command", foreign)
    with pytest.raises(RuntimeError, match="(?i)own"):
        ops.stop_nas()
    assert not any(call[:2] == ["docker", "stop"] for call in foreign.calls)
    owned = Docker()
    monkeypatch.setattr(ops, "remote_command", owned)
    ops.stop_nas()
    assert ["docker", "stop", "--time", "20", owned.old] in owned.calls
    with pytest.raises(RuntimeError, match="full container"):
        ops.owned_action("stop", owned.old[:12])


@pytest.mark.parametrize("change", ["short_id", "mount", "network", "command", "name", "foreign_label"])
def test_adoption_rejects_unsafe_initial_container(ops, monkeypatch, change):
    docker = Docker(owned=False)
    identity = docker.old
    container = docker.containers[identity]
    if change == "short_id":
        identity = identity[:12]
    elif change == "mount":
        container["Mounts"][0]["Source"] = "/foreign/secrets"
    elif change == "network":
        container["HostConfig"]["NetworkMode"] = "bridge"
    elif change == "command":
        container["Config"]["Cmd"] = ["python", "-m", "other.app"]
    elif change == "name":
        container["Name"] = "/foreign-app"
    else:
        container["Config"]["Labels"] = {"io.tapmesh.owner": "other-owner"}
    monkeypatch.setattr(ops, "remote_command", docker)
    with pytest.raises(RuntimeError):
        ops.adopt_container(identity)
    assert not (ops.STATE / "ownership.json").exists()
    assert all(call[:2] == ["docker", "container"] for call in docker.calls)


def test_explicit_adoption_trusts_only_recorded_full_id(ops, monkeypatch):
    docker = Docker(owned=False)
    monkeypatch.setattr(ops, "remote_command", docker)
    ops.adopt_container(docker.old)
    manifest = ops.STATE / "ownership.json"
    assert manifest.stat().st_mode & 0o077 == 0
    assert json.loads(manifest.read_text())["containers"] == [docker.old]
    assert ops.current_container()["Id"] == docker.old
    foreign = docker.containers.pop(docker.old)
    foreign["Id"] = "f" * 64
    docker.containers[foreign["Id"]] = foreign
    with pytest.raises(RuntimeError, match="(?i)owned"):
        ops.stop_nas()


def test_configure_preserves_owner_and_credentials(ops):
    import argparse
    token = (ops.STATE / "server.token").read_bytes()
    args = argparse.Namespace(ssh_alias="test-nas", nas_hostname="changed.invalid", host_key_alias="test-key", remote_project_root="/new/project")
    ops.configure(args)
    assert ops.config()["owner_id"] == "c" * 32
    assert ops.config()["remote_project_root"] == "/new/project"
    assert (ops.STATE / "server.token").read_bytes() == token
    assert ops.agent_env()["TAPMESH_OCR_URL"] == "http://127.0.0.1:48793"


def test_backup_snapshots_sqlite_wal_and_excludes_credentials(ops):
    state = ops.STATE / "agent-state"
    state.mkdir(mode=0o700)
    database = sqlite3.connect(state / "metadata.sqlite3")
    database.execute("PRAGMA journal_mode=WAL")
    database.execute("CREATE TABLE records (value TEXT)")
    database.execute("INSERT INTO records VALUES ('committed-in-wal')")
    database.commit()
    folder = ops.backup(local_only=True)
    assert folder.parent == ops.STATE / "backups"
    assert folder.stat().st_mode & 0o077 == 0
    with tarfile.open(folder / "mac-state.tar") as archive:
        assert "server.token" not in archive.getnames()
        assert not any(name.endswith(("-wal", "-shm")) for name in archive.getnames())
        snapshot = folder / "snapshot.sqlite3"
        snapshot.write_bytes(archive.extractfile("agent-state/metadata.sqlite3").read())
    with sqlite3.connect(snapshot) as copy:
        assert copy.execute("SELECT value FROM records").fetchall() == [("committed-in-wal",)]
    database.close()
    assert all(path.stat().st_mode & 0o077 == 0 for path in (folder / "mac-state.tar", folder / "backup.json"))


def test_backup_credentials_require_explicit_option_and_stay_private(ops):
    folder = ops.backup(local_only=True, include_credentials=True)
    with tarfile.open(folder / "mac-state.tar") as archive:
        assert archive.extractfile("server.token").read() == b"fixture-secret-never-log\n"
        assert archive.getmember("server.token").mode == 0o600


def test_backup_rejects_symlinks_and_is_bounded(ops, tmp_path):
    state = ops.STATE / "agent-state"
    state.mkdir(mode=0o700)
    (state / "escape").symlink_to(tmp_path)
    with pytest.raises(RuntimeError, match="symlink"):
        ops.backup(local_only=True)
    assert not list((ops.STATE / "backups").iterdir())
    (state / "escape").unlink()
    (state / "large").write_bytes(b"x" * 101)
    with pytest.raises(RuntimeError, match="size"):
        ops.snapshot_tar(ops.STATE, ["agent-state"], tmp_path / "bounded.tar", max_bytes=100)


def test_start_readiness_reports_degraded_without_live_ports(ops, monkeypatch):
    monkeypatch.setattr(ops.urllib.request, "urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline fixture")))
    with pytest.raises(RuntimeError, match="degraded.*agent, server"):
        ops.wait_local_ready(ops.config(), timeout=0)


def test_management_lock_prevents_competing_cli_mutations(ops):
    with ops.operation_lock():
        with pytest.raises(RuntimeError, match="in progress"):
            with ops.operation_lock():
                pytest.fail("Concurrent management lock was accepted")


def test_remote_backup_script_compiles_and_excludes_nas_credentials(ops, tmp_path, monkeypatch):
    nas = tmp_path / "private-nas"
    (nas / "server-state").mkdir(mode=0o700, parents=True)
    nas.chmod(0o700)
    (nas / "server-state/fixture.json").write_text('{"test":true}')
    archive = tmp_path / "remote.tar"
    ops.snapshot_tar(nas, ["server-state"], archive)
    docker = Docker()

    def remote(argv, **kwargs):
        if argv[0] == "python3":
            compile(argv[2], "remote-backup", "exec")
            assert "['server-state']" in argv[2]
            return subprocess.CompletedProcess(argv, 0, archive.read_bytes(), b"")
        return docker(argv, **kwargs)

    monkeypatch.setattr(ops, "remote_command", remote)
    folder = ops.backup()
    with tarfile.open(folder / "nas-state.tar") as contents:
        assert contents.getnames() == ["server-state/fixture.json"]


def test_uninstall_also_removes_explicitly_adopted_retained_release(ops, monkeypatch):
    docker = Docker(owned=False)
    monkeypatch.setattr(ops, "remote_command", docker)
    monkeypatch.setattr(ops, "verified_installed_app", lambda: None)
    ops.adopt_container(docker.old)
    ops.deploy()
    ops.uninstall()
    assert not docker.containers


def test_uninstall_refuses_foreign_container_before_local_changes(ops, monkeypatch):
    monkeypatch.setattr(ops, "verified_installed_app", lambda: None)
    docker = Docker(owned=False)
    monkeypatch.setattr(ops, "remote_command", docker)
    with pytest.raises(RuntimeError, match="(?i)own"):
        ops.uninstall()
    assert (ops.STATE / "server.token").exists()
    assert not any(call[1] in {"stop", "rm"} for call in docker.calls if call[0] == "docker")


def test_uninstall_removes_verified_services_retains_data_and_other_containers(ops, monkeypatch):
    docker = Docker()
    foreign = json.loads(json.dumps(docker.containers[docker.old]))
    foreign["Id"], foreign["Name"], foreign["Config"]["Labels"] = "f" * 64, "/unrelated-database", {}
    docker.containers[foreign["Id"]] = foreign
    monkeypatch.setattr(ops, "verified_installed_app", lambda: None)
    monkeypatch.setattr(ops, "remote_command", docker)
    ops.uninstall()
    assert set(docker.containers) == {foreign["Id"]}
    assert (ops.STATE / "server.token").exists()
    assert (ops.STATE / "connection.json").exists()


def test_uninstall_refuses_unrecorded_or_changed_app(ops, tmp_path, monkeypatch):
    monkeypatch.setattr(ops.Path, "home", lambda: tmp_path)
    app = tmp_path / "Applications/TapMesh.app"
    binary = app / "Contents/MacOS/TapMesh"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"our-fixture-app")
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "io.tapmesh.mac"}))
    with pytest.raises(RuntimeError, match="receipt"):
        ops.uninstall(local_only=True)
    ops.record_installed_app(app)
    binary.write_bytes(b"replacement-app")
    with pytest.raises(RuntimeError, match="differs"):
        ops.uninstall(local_only=True)
    assert app.exists()


def test_uninstall_removes_only_receipted_app_and_own_plist(ops, tmp_path, monkeypatch):
    monkeypatch.setattr(ops.Path, "home", lambda: tmp_path)
    app = tmp_path / "Applications/TapMesh.app"
    binary = app / "Contents/MacOS/TapMesh"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"our-fixture-app")
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "io.tapmesh.mac"}))
    ops.record_installed_app(app)
    ops.PLIST.parent.mkdir()
    ops.PLIST.write_bytes(plistlib.dumps({
        "Label": ops.LABEL, "WorkingDirectory": str(ops.STATE), "TapMeshOwnerID": "c" * 32,
        "ProgramArguments": [sys.executable, str(ops.STATE / "manage.py"), "supervise"],
    }))
    ops.PLIST.chmod(0o600)
    commands = []
    monkeypatch.setattr(ops.subprocess, "run", lambda argv, **kwargs: commands.append(argv) or subprocess.CompletedProcess(argv, 0))
    ops.uninstall(local_only=True)
    assert not app.exists()
    assert not ops.PLIST.exists()
    assert commands == [["launchctl", "bootout", f"gui/{ops.os.getuid()}/{ops.LABEL}"]]
    assert (ops.STATE / "server.token").exists()


def test_owned_bootstrap_waits_for_transient_registration_release(ops, monkeypatch):
    codes = iter([5, 5, 0]); calls = []; pauses = []
    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, next(codes))
    monkeypatch.setattr(ops.subprocess, 'run', run)
    monkeypatch.setattr(ops.time, 'sleep', pauses.append)
    ops.bootstrap_service('gui/fixture')
    assert calls == [['launchctl', 'bootstrap', 'gui/fixture', str(ops.PLIST)]] * 3
    assert pauses == [.5, .5]


def test_owned_bootstrap_never_retries_other_failures(ops, monkeypatch):
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 113)
    monkeypatch.setattr(ops.subprocess, 'run', run)
    with pytest.raises(subprocess.CalledProcessError):
        ops.bootstrap_service('gui/fixture')
    assert len(calls) == 1


def test_doctor_fails_when_xcode_tool_stubs_fail(tmp_path, monkeypatch, capsys):
    doctor = load_script("doctor")
    state = tmp_path / "Library/Application Support/TapMesh"
    state.mkdir(parents=True)
    for name in ("server.token", "agent.token", "approvals.token"):
        (state / name).write_text("fixture")
        (state / name).chmod(0o600)
    companion = tmp_path / "companion"
    companion.write_text("fixture")
    companion.chmod(0o700)
    (state / "connection.json").write_text(json.dumps({"idb_companion": str(companion)}))
    (state / "connection.json").chmod(0o600)
    monkeypatch.setattr(doctor.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/test/bin/" + name)
    monkeypatch.setattr(doctor.subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 72, b"", b"Xcode unavailable"))
    with pytest.raises(SystemExit) as result:
        doctor.main()
    assert result.value.code == 1
    assert "72" in capsys.readouterr().out
