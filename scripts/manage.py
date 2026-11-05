#!/usr/bin/env python3
"""Own only TapMesh services, credentials, and verified SSH forwards."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import inspect
import io
import json
import os
from pathlib import Path
import plistlib
import re
import secrets
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
STATE = Path.home() / "Library/Application Support/TapMesh"
VENV = Path.home() / "Library/Caches/TapMesh/venv"
REMOTE = "/Volume1/.tapmesh"
LABEL = "io.tapmesh.supervisor"
PLIST = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
CONTAINER = "tapmesh-server"
PROJECT_LABEL = "io.tapmesh.project"
OWNER_LABEL = "io.tapmesh.owner"
RELEASE_LABEL = "io.tapmesh.release"
HEALTH_TIMEOUT = 90
LOG_MAX_BYTES = 5_000_000


def reject_symlinks(path: Path) -> None:
    for component in (path, *path.parents):
        if component.is_symlink():
            raise RuntimeError(f"Refusing symlink at private path: {component}")


def require_private(path: Path, *, directory: bool = False) -> None:
    """Reject symlinks, foreign owners and group/world access at private boundaries."""
    reject_symlinks(path)
    details = path.stat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if (not expected(details.st_mode) or details.st_uid != os.getuid() or details.st_mode & 0o077 or
            (not directory and details.st_nlink != 1)):
        raise RuntimeError(f"Unsafe private ownership or permissions: {path}")


def private_json(path: Path, value: dict) -> None:
    require_private(path.parent, directory=True)
    if path.exists() or path.is_symlink():
        require_private(path)
    temp = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    try:
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as file:
            json.dump(value, file, indent=2)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


@contextmanager
def operation_lock():
    """Serialize CLI mutations from this installation; do not wait indefinitely."""
    reject_symlinks(STATE)
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    require_private(STATE, directory=True)
    path = STATE / "operation.lock"
    if path.exists() or path.is_symlink():
        require_private(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another TapMesh management operation is in progress.") from None
        yield
    finally:
        os.close(fd)


def config() -> dict:
    path = STATE / "connection.json"
    if not path.exists():
        raise SystemExit("Run scripts/manage.py configure first.")
    require_private(path)
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise RuntimeError("Invalid private connection configuration.")
    return value


def owner_id() -> str:
    value = config().get("owner_id", "")
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise RuntimeError("Missing private owner identity; run configure before managing services.")
    return value


def ownership_manifest() -> dict:
    path = STATE / "ownership.json"
    if not path.exists():
        return {"owner_id": owner_id(), "containers": []}
    require_private(path)
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or value.get("owner_id") != owner_id() or not isinstance(value.get("containers"), list):
        raise RuntimeError("Private ownership manifest does not match this owner.")
    return value


def inspect_container(identity: str) -> dict:
    result = remote_command(["docker", "container", "inspect", identity], capture=True, timeout=15)
    values = json.loads(result.stdout)
    if (not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict) or
            not isinstance(values[0].get("Id"), str) or not re.fullmatch(r"[0-9a-f]{64}", values[0]["Id"])):
        raise RuntimeError("Docker did not return one full container identity.")
    if re.fullmatch(r"[0-9a-f]{64}", identity) and values[0]["Id"] != identity:
        raise RuntimeError("Container identity changed; refusing operation.")
    return values[0]


def verify_owned(identity: str) -> dict:
    if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{64}", identity):
        raise RuntimeError("Ownership verification requires a full container identity.")
    container = inspect_container(identity)
    labels = container.get("Config", {}).get("Labels") or {}
    if labels.get(PROJECT_LABEL) == "tapmesh" and labels.get(OWNER_LABEL) == owner_id():
        return container
    # Explicit labels belonging to another owner can never be overridden by adoption.
    if labels.get(PROJECT_LABEL) or labels.get(OWNER_LABEL):
        raise RuntimeError("Foreign container ownership; refusing operation.")
    if container["Id"] in ownership_manifest()["containers"]:
        return container
    raise RuntimeError("Container is not owned by this TapMesh installation; explicit adoption required.")


def current_container() -> dict | None:
    result = remote_command(["docker", "ps", "-a", "--no-trunc", "--filter",
                             "name=^/tapmesh-server$", "--format", "{{.ID}}"], capture=True, timeout=15)
    identities = result.stdout.decode().split()
    if not identities:
        return None
    if len(identities) != 1 or not re.fullmatch(r"[0-9a-f]{64}", identities[0]):
        raise RuntimeError("Ambiguous container identity; refusing operation.")
    container = verify_owned(identities[0])
    if container.get("Name") != "/" + CONTAINER:
        raise RuntimeError("Container name changed; refusing operation.")
    return container


def adopt_container(identity: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", identity):
        raise RuntimeError("Adoption requires an explicit full 64-character container ID.")
    container = inspect_container(identity)
    labels = container.get("Config", {}).get("Labels") or {}
    if labels.get(PROJECT_LABEL) or labels.get(OWNER_LABEL):
        raise RuntimeError("Adoption is only for the unlabeled initial container.")
    expected_mounts = {
        (REMOTE + "/secrets", "/run/devicebridge", False),
        (REMOTE + "/server-state", "/var/lib/devicebridge", True),
    }
    mounts = container.get("Mounts", [])
    actual = {(entry.get("Source"), entry.get("Destination"), entry.get("RW")) for entry in mounts}
    if (container.get("Name") != "/" + CONTAINER or
            container.get("HostConfig", {}).get("NetworkMode") != "host" or
            container.get("Config", {}).get("Cmd") != ["python", "-m", "server.app"] or
            actual != expected_mounts or len(mounts) != 2 or
            any(entry.get("Type") != "bind" for entry in mounts)):
        raise RuntimeError("Container does not match the private TapMesh initial deployment.")
    manifest = ownership_manifest()
    if identity not in manifest["containers"]:
        manifest["containers"].append(identity)
    private_json(STATE / "ownership.json", manifest)
    print("Recorded explicit initial container ownership in private local state.")


def owned_action(action: str, identity: str, *arguments: str) -> None:
    verify_owned(identity)
    remote_command(["docker", action, *arguments, identity], capture=True, timeout=45)


def stop_nas() -> None:
    container = current_container()
    if container is None:
        print("No TapMesh NAS container present.")
        return
    owned_action("stop", container["Id"], "--time", "20")
    print("Stopped the verified project-owned NAS container; data retained.")


def ssh_args() -> list[str]:
    c = config()
    args = ["/usr/bin/ssh", "-T", "-o", "BatchMode=yes", "-o",
            "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=8", "-o",
            "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3"]
    if c.get("nas_hostname"):
        args += ["-o", "HostName=" + c["nas_hostname"]]
    if c.get("host_key_alias"):
        args += ["-o", "HostKeyAlias=" + c["host_key_alias"]]
    return args


def remote_command(argv: list[str], *, data: bytes | None = None,
                   capture: bool = False, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(ssh_args() + [config()["ssh_alias"], shlex.join(argv)],
                          input=data, capture_output=capture, timeout=timeout, check=True)


def configure(args: argparse.Namespace) -> None:
    reject_symlinks(STATE)
    STATE.mkdir(parents=True, exist_ok=True)
    STATE.chmod(0o700)
    require_private(STATE, directory=True)
    previous = config() if (STATE / "connection.json").exists() else {}
    ssh_alias = args.ssh_alias or previous.get("ssh_alias") or "tapmesh-nas"
    for name in ("logs", "run", "agent-state", "runtime-src"):
        reject_symlinks(STATE / name)
        (STATE / name).mkdir(exist_ok=True)
        (STATE / name).chmod(0o700)
    for name in ("server.token", "agent.token", "approvals.token"):
        path = STATE / name
        reject_symlinks(path)
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as file:
                file.write(secrets.token_urlsafe(32) + "\n")
        require_private(path)
    value = {
        "owner_id": previous.get("owner_id") or secrets.token_hex(16),
        "api_url": "http://127.0.0.1:48793", "agent_url": "http://127.0.0.1:48792",
        "server_token_file": str(STATE / "server.token"),
        "agent_token_file": str(STATE / "agent.token"),
        "approval_token_file": str(STATE / "approvals.token"),
        "ssh_alias": ssh_alias, "nas_hostname": args.nas_hostname or previous.get("nas_hostname") or "TNAS.local",
        "host_key_alias": args.host_key_alias or previous.get("host_key_alias") or ssh_alias, "project_root": str(ROOT),
        "remote_project_root": args.remote_project_root or previous.get("remote_project_root") or "/Volume1/public/tapmesh",
        "python": str(VENV / "bin/python"),
        "build_roots": [str(ROOT / "Fixtures"),
                        str(Path.home() / "Library/Caches/TapMesh/fixture-build/Build/Products")],
        "export_roots": [str(STATE / "agent-state/artifacts")],
        "idb_companion": str(Path.home() / "Library/Caches/TapMesh/idb-companion-1.7.8/idb_companion"),
    }
    private_json(STATE / "connection.json", value)
    print("Configured private credentials and connection; credential values withheld.")


def install_agent() -> None:
    c = config()
    destination = STATE / "runtime-src"
    for folder in ("agent", "server", "Contracts"):
        source = ROOT / folder
        if not source.exists():
            raise SystemExit(f"Missing implementation: {source}")
        shutil.copytree(source, destination / folder, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("pyproject.toml", "requirements.lock", "requirements-mac.lock"):
        source = ROOT / name
        if source.exists():
            shutil.copy2(source, destination / name)
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("uv is required to install the pinned Mac runtime.")
    if not Path(c["python"]).exists():
        subprocess.run([uv, "venv", "--python", "3.12", str(VENV)], check=True)
    lock = destination / "requirements-mac.lock"
    if not lock.exists():
        raise SystemExit("Missing hash-locked Mac runtime requirements.")
    subprocess.run([uv, "pip", "install", "--python", c["python"],
                    "--require-hashes", "-r", str(lock)], check=True)
    # This venv is private to this application. Remove the former working-name
    # distribution before replacing modules shared by the renamed package.
    subprocess.run([uv, "pip", "uninstall", "--python", c["python"],
                    "devicebridge", "tapmesh"], check=True)
    subprocess.run([uv, "pip", "install", "--python", c["python"],
                    "--no-deps", str(destination)], check=True)
    print("Mac runtime installed in local storage; source/build files stay on TNAS.")


def deploy() -> None:
    c = config()
    owner = owner_id()
    previous = current_container()  # Fail closed before provisioning or building.
    # stdin carries credentials over SSH; they never appear in argv or output.
    for name in ("server.token", "agent.token"):
        require_private(STATE / name)
    payload = json.dumps({name: (STATE / name).read_text().strip()
                          for name in ("server.token", "agent.token")}).encode()
    provision = r"""import json,os,pathlib,stat,sys
p=pathlib.Path('/Volume1/.tapmesh')
def check(path):
 for part in (path,*path.parents):
  if part.is_symlink():raise RuntimeError('private path is a symlink')
 if path.exists() and path.stat().st_uid!=os.getuid():raise RuntimeError('foreign private path owner')
check(p);p.mkdir(mode=0o700,exist_ok=True)
p.chmod(0o700)
for child in ['secrets','server-state']:
 check(p/child)
 (p/child).mkdir(mode=0o700,exist_ok=True)
 (p/child).chmod(0o700)
for name,value in json.load(sys.stdin).items():
 if name not in ('server.token','agent.token'):raise ValueError('unexpected credential')
 f=p/'secrets'/name
 check(f)
 if f.exists() and (not f.is_file() or f.stat().st_nlink!=1):raise RuntimeError('unsafe credential file')
 fd=os.open(f,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'w') as out:out.write(value+'\n')
 os.chmod(f,0o600)
print('NAS private state provisioned')
"""
    compile(provision, "nas-private-state-setup", "exec")
    remote_command(["python3", "-c", provision], data=payload)
    project = c["remote_project_root"]
    release = "tapmesh-server:release-" + secrets.token_hex(8)
    remote_command(["docker", "build", "--memory=1g", "--cpu-period=100000",
                    "--cpu-quota=200000", "-f", project + "/deploy/Dockerfile",
                    "-t", release, project])
    # The previous container pins its exact image and configuration for rollback.
    # It remains stopped after a successful release and is never removed here.
    try:
        if previous:
            owned_action("stop", previous["Id"], "--time", "20")
            verify_owned(previous["Id"])
            remote_command(["docker", "rename", previous["Id"],
                            CONTAINER + "-previous-" + previous["Id"][:12]], capture=True, timeout=20)
        result = remote_command([
            "docker", "run", "-d", "--name", "tapmesh-server", "--restart", "unless-stopped",
            "--label", PROJECT_LABEL + "=tapmesh", "--label", OWNER_LABEL + "=" + owner,
            "--label", RELEASE_LABEL + "=" + release,
            "--network", "host", "--cpus", "2", "--memory", "1g", "--memory-swap", "1g",
            "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--pids-limit", "128",
            "--log-opt", "max-size=5m", "--log-opt", "max-file=2",
            "-v", REMOTE + "/secrets:/run/devicebridge:ro",
            "-v", REMOTE + "/server-state:/var/lib/devicebridge:rw",
            "-e", "TAPMESH_SERVER_TOKEN_FILE=/run/devicebridge/server.token",
            "-e", "TAPMESH_AGENT_TOKEN_FILE=/run/devicebridge/agent.token",
            "-e", "TAPMESH_STATE_DIR=/var/lib/devicebridge",
            "-e", "TAPMESH_AGENT_URL=http://127.0.0.1:48792",
            release,
        ], capture=True, timeout=45)
        candidate = result.stdout.decode().strip()
        if not re.fullmatch(r"[0-9a-f]{64}", candidate):
            raise RuntimeError("New container did not return a full identity.")
        wait_container_healthy(candidate)
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        try:
            # A failed/timeout docker run may still have created a container.
            # Only a verified candidate from this owner may be removed.
            current = current_container()
            if current and (not previous or current["Id"] != previous["Id"]):
                if current.get("Config", {}).get("Labels", {}).get(RELEASE_LABEL) != release:
                    raise RuntimeError("Canonical container is not this deployment candidate; refusing removal.")
                owned_action("stop", current["Id"], "--time", "20")
                owned_action("rm", current["Id"])
            if previous:
                restored = verify_owned(previous["Id"])
                if restored.get("Name") != "/" + CONTAINER:
                    if restored.get("Name") != "/" + CONTAINER + "-previous-" + previous["Id"][:12]:
                        raise RuntimeError("Previous container name changed unexpectedly.")
                    remote_command(["docker", "rename", previous["Id"], CONTAINER], capture=True, timeout=20)
                if previous.get("State", {}).get("Running"):
                    owned_action("start", previous["Id"])
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as rollback_error:
            raise RuntimeError("Deployment failed and automatic restore failed; retained previous release requires operator recovery.") from rollback_error
        raise RuntimeError("Deployment failed; previous release restored when present.") from error
    print("Deployed healthy TapMesh release; previous container/image retained for rollback.")


def wait_container_healthy(identity: str, timeout: float = HEALTH_TIMEOUT) -> None:
    deadline = time.monotonic() + timeout
    while True:
        container = verify_owned(identity)
        state = container.get("State", {})
        if container.get("Name") != "/" + CONTAINER:
            raise RuntimeError("New container name changed during health verification.")
        health = state.get("Health", {}).get("Status")
        if state.get("Running") and health == "healthy":
            return
        if not state.get("Running") or health == "unhealthy":
            raise RuntimeError("New container exited or failed health verification.")
        if time.monotonic() >= deadline:
            raise RuntimeError("New container health deadline exceeded.")
        time.sleep(2)


def agent_env() -> dict[str, str]:
    c = config()
    env = {key: value for key, value in os.environ.items()
           if key in {"HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL", "DEVELOPER_DIR"}}
    env["PATH"] = str(VENV / "bin") + ":/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    env.update({
        "TAPMESH_AGENT_TOKEN_FILE": c["agent_token_file"],
        "TAPMESH_APPROVAL_TOKEN_FILE": c["approval_token_file"],
        "TAPMESH_STATE_DIR": str(STATE / "agent-state"),
        "TAPMESH_EXPORT_ROOTS": os.pathsep.join(c.get("export_roots", [str(STATE / "agent-state/artifacts")])),
        "TAPMESH_BUILD_ROOTS": os.pathsep.join(c.get("build_roots", [str(Path(c["project_root"]) / "Fixtures")])),
        "TAPMESH_IDB_COMPANION": c.get("idb_companion", "/opt/homebrew/bin/idb_companion"),
        "TAPMESH_FFMPEG": "/opt/homebrew/bin/ffmpeg",
        "TAPMESH_OCR_URL": "http://127.0.0.1:48793",
    })
    return env


class BoundedLogSink:
    """Continuously rotate one private file plus one backup, without a memory queue."""

    def __init__(self, path: Path, max_bytes: int = LOG_MAX_BYTES):
        if max_bytes < 64:
            raise ValueError("Log bound must permit one metadata record.")
        self.path, self.max_bytes = path, max_bytes
        self.lock = threading.Lock()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        require_private(path.parent, directory=True)
        for existing in (path, path.with_name(path.name + ".1")):
            if existing.exists() or existing.is_symlink():
                require_private(existing)
                if existing.stat().st_size > max_bytes:
                    # Bound pre-existing logs too; retain only their final bounded bytes.
                    with existing.open("rb") as old:
                        old.seek(-max_bytes, os.SEEK_END)
                        tail = old.read(max_bytes)
                    fd = os.open(existing, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
                    with os.fdopen(fd, "wb") as old:
                        old.write(tail)
        self.file = self._open()
        self.file.seek(0, os.SEEK_END)

    def _open(self):
        if self.path.exists() or self.path.is_symlink():
            require_private(self.path)
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        return os.fdopen(fd, "ab", buffering=0)

    def write(self, value: bytes) -> None:
        with self.lock:
            for offset in range(0, len(value), self.max_bytes):
                chunk = value[offset:offset + self.max_bytes]
                if self.file.tell() + len(chunk) > self.max_bytes:
                    self.file.close()
                    backup = self.path.with_name(self.path.name + ".1")
                    if backup.exists() or backup.is_symlink():
                        require_private(backup)
                    os.replace(self.path, backup)
                    self.file = self._open()
                self.file.write(chunk)

    def close(self) -> None:
        with self.lock:
            self.file.close()


def diagnostic_metadata(line: bytes) -> bytes | None:
    """Preserve only the agent's closed, content-free diagnostic event."""
    try:
        # Python's default warning handler can prefix the JSON with its logger
        # name. Only the closed JSON payload is ever re-emitted.
        marker = line.find(b'{"event":')
        if marker < 0:
            return None
        line = line[marker:]
        value = json.loads(line)
        if not isinstance(value, dict) or set(value) != {"event", "diagnostic_id", "exception_type", "stack"}:
            return None
        if value["event"] != "unexpected_backend_exception" or not re.fullmatch(r"[0-9a-f]{32}", value["diagnostic_id"]):
            return None
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", value["exception_type"]):
            return None
        if not isinstance(value["stack"], list) or len(value["stack"]) > 8:
            return None
        for frame in value["stack"]:
            if (not isinstance(frame, dict) or set(frame) != {"file", "line", "function"}
                    or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", frame["file"])
                    or not re.fullmatch(r"[A-Za-z0-9_<>]{1,80}", frame["function"])
                    or type(frame["line"]) is not int or not 0 <= frame["line"] <= 100000):
                return None
        return json.dumps(value, separators=(",", ":")).encode() + b"\n"
    except (ValueError, TypeError, KeyError):
        return None


def drain_child_output(stream, sink: BoundedLogSink, name: str) -> threading.Thread:
    def drain():
        try:
            while chunk := stream.readline(8193):
                # Arbitrary subprocess output can contain request/device/error bodies.
                # Preserve bounded activity metadata only; health/status provide diagnosis.
                diagnostic = diagnostic_metadata(chunk) if name == "agent" and len(chunk) <= 8192 else None
                sink.write(diagnostic or f"{name} output: {len(chunk)} bytes withheld\n".encode())
        finally:
            stream.close()

    reader = threading.Thread(target=drain, name=f"tapmesh-log-{name}", daemon=True)
    reader.start()
    return reader


def supervisor() -> None:
    c = config()
    children: dict[str, tuple[subprocess.Popen, BoundedLogSink, threading.Thread]] = {}
    supervisor_log = BoundedLogSink(STATE / "logs/supervisor.log")
    stopping = False

    def stop(_signal: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    commands = {
        "agent": [c["python"], "-m", "agent.app"],
        "tunnel": ssh_args() + ["-N", "-o", "ExitOnForwardFailure=yes", "-L",
                                "127.0.0.1:48793:127.0.0.1:48793", "-R",
                                "127.0.0.1:48792:127.0.0.1:48792", c["ssh_alias"]],
    }
    retry_after = {name: 0.0 for name in commands}
    failures = {name: 0 for name in commands}
    started_at: dict[str, float] = {}
    try:
        while not stopping:
            now = time.monotonic()
            for name, argv in commands.items():
                child, log, reader = children.get(name, (None, None, None))
                if child is not None and child.poll() is None:
                    if now - started_at[name] > 30:
                        failures[name] = 0
                    continue
                if child is not None:
                    reader.join(timeout=3)
                    log.close()
                    children.pop(name)
                    failures[name] += 1
                    retry_after[name] = now + min(30, 2 ** min(failures[name], 5))
                    supervisor_log.write(f"Exited {name}; code {child.returncode}; retry in {retry_after[name] - now:.0f}s\n".encode())
                if now < retry_after[name]:
                    continue
                path = STATE / "logs" / f"{name}.log"
                log = BoundedLogSink(path)
                child = subprocess.Popen(argv, cwd=STATE, env=agent_env(), stdin=subprocess.DEVNULL,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         start_new_session=True)
                reader = drain_child_output(child.stdout, log, name)
                children[name] = child, log, reader
                started_at[name] = now
                supervisor_log.write(f"Started {name}; pid {child.pid}\n".encode())
            time.sleep(1)
    finally:
        for child, log, reader in children.values():
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
        for child, log, reader in children.values():
            try:
                child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)
            reader.join(timeout=3)
            log.close()
        supervisor_log.close()


def bootstrap_service(domain: str, *, timeout: float = 20) -> None:
    # bootout can acknowledge removal before launchd finishes releasing the
    # old registration. Retrying this owned registration never replays input.
    deadline = time.monotonic() + timeout
    argv = ["launchctl", "bootstrap", domain, str(PLIST)]
    while True:
        result = subprocess.run(argv, capture_output=True)
        if result.returncode == 0:
            return
        if result.returncode != 5 or time.monotonic() >= deadline:
            raise subprocess.CalledProcessError(result.returncode, argv)
        time.sleep(.5)


def start() -> None:
    c = config()
    if PLIST.exists() or PLIST.is_symlink():
        verify_launchagent()
    # launchd owns process lifetime and restart; keep the controller off SMB.
    destination = STATE / "manage.py"
    shutil.copy2(Path(__file__), destination)
    destination.chmod(0o600)
    private_json(STATE / "connection.json", c)
    plist = {
        "Label": LABEL, "ProgramArguments": [c["python"], str(destination), "supervise"],
        "WorkingDirectory": str(STATE), "RunAtLoad": True, "KeepAlive": True,
        "ThrottleInterval": 15, "ProcessType": "Background",
        # The supervisor writes its controlled lifecycle events to its rotating sink.
        "StandardOutPath": "/dev/null", "StandardErrorPath": "/dev/null",
        "TapMeshOwnerID": owner_id(),
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
    }
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    PLIST.write_bytes(plistlib.dumps(plist))
    PLIST.chmod(0o600)
    service = f"gui/{os.getuid()}/{LABEL}"
    subprocess.run(["launchctl", "bootout", service], capture_output=True)
    bootstrap_service(f"gui/{os.getuid()}")
    wait_local_ready(c)
    print("Started project-owned agent/tunnel supervisor; agent and server health verified.")


def wait_local_ready(c: dict, timeout: float = 40) -> None:
    deadline = time.monotonic() + timeout
    missing = []
    while True:
        missing = []
        for label, base in (("agent", c["agent_url"]), ("server", c["api_url"])):
            try:
                with urllib.request.urlopen(base + "/health", timeout=2) as response:
                    data = json.loads(response.read(4096))
                    if response.status != 200 or not isinstance(data, dict) or data.get("status") != "ok":
                        missing.append(label)
            except (OSError, ValueError):
                missing.append(label)
        if not missing:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("Supervisor started but degraded: health unavailable for " + ", ".join(missing) + ". Use status/doctor for diagnosis.")
        time.sleep(1)


def stop() -> None:
    verify_launchagent()
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], check=True)
    print("Stopped TapMesh agent and tunnel; NAS service retained.")


def status() -> None:
    c = config()
    healthy = True
    for label, url in (("agent", c["agent_url"]), ("server", c["api_url"])):
        try:
            with urllib.request.urlopen(url + "/health", timeout=5) as response:
                print(label, response.status, response.read(4096).decode())
        except Exception as error:
            print(label, "unavailable:", error)
            healthy = False
    subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not healthy:
        raise SystemExit(1)


def snapshot_tar(root, selections, destination, max_bytes=100_000_000, max_files=5000):
    """Bounded read-only snapshot; the same standard-library code runs on the NAS."""
    import os
    from pathlib import Path
    import sqlite3
    import stat
    import tarfile
    import tempfile
    import time

    root, destination = Path(root), Path(destination)
    for parent in (root, *root.parents):
        if parent.is_symlink():
            raise RuntimeError("Backup root may not follow symlinks")
    info = root.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError("Backup root must be owner-only")
    total = count = 0
    deadline = time.monotonic() + 60
    with tempfile.TemporaryDirectory(prefix="tapmesh-snapshot-") as scratch:
        scratch = Path(scratch)
        with tarfile.open(destination, "w", format=tarfile.PAX_FORMAT) as archive:
            for selection in selections:
                relative = Path(selection)
                if relative.is_absolute() or ".." in relative.parts:
                    raise RuntimeError("Unsafe backup selection")
                first = root / relative
                if not first.exists() and not first.is_symlink():
                    continue
                pending = [first]
                while pending:
                    path = pending.pop()
                    details = path.lstat()
                    if details.st_uid != os.getuid() or stat.S_ISLNK(details.st_mode):
                        raise RuntimeError("Backup refuses symlinks or foreign-owned files")
                    if stat.S_ISDIR(details.st_mode):
                        with os.scandir(path) as entries:
                            for entry in entries:
                                count += 1
                                if count > max_files:
                                    raise RuntimeError("Backup file count limit exceeded")
                                pending.append(Path(entry.path))
                        continue
                    if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
                        raise RuntimeError("Backup refuses non-regular or hard-linked files")
                    if path.name.endswith(("-wal", "-shm", "-journal")):
                        database = Path(str(path).rsplit("-", 1)[0])
                        if database.is_file() and database.suffix in {".db", ".sqlite", ".sqlite3"}:
                            continue
                    if details.st_size > max_bytes - total or time.monotonic() > deadline:
                        raise RuntimeError("Backup size/time limit exceeded")
                    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                    with os.fdopen(fd, "rb") as source:
                        opened = os.fstat(source.fileno())
                        if (opened.st_dev, opened.st_ino) != (details.st_dev, details.st_ino):
                            raise RuntimeError("Backup input changed identity")
                        input_path = path
                        if source.read(16) == b"SQLite format 3\x00":
                            snapshot = scratch / "database.sqlite3"
                            snapshot.unlink(missing_ok=True)
                            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2) as database:
                                page_size = database.execute("PRAGMA page_size").fetchone()[0]
                                estimate = database.execute("PRAGMA page_count").fetchone()[0] * page_size
                                if estimate > max_bytes - total:
                                    raise RuntimeError("SQLite backup exceeds size limit")
                                def progress(status, remaining, pages):
                                    if time.monotonic() > deadline or pages * page_size > max_bytes - total:
                                        raise RuntimeError("SQLite backup time/size limit exceeded")
                                with sqlite3.connect(snapshot) as target:
                                    database.backup(target, pages=64, progress=progress, sleep=0.01)
                            after = path.lstat()
                            if stat.S_ISLNK(after.st_mode) or (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino):
                                raise RuntimeError("SQLite backup input changed identity")
                            input_path = snapshot
                        size = input_path.stat().st_size
                        if size > max_bytes - total:
                            raise RuntimeError("Backup size limit exceeded")
                        item = tarfile.TarInfo(path.relative_to(root).as_posix())
                        item.size, item.mode = size, 0o600
                        if input_path == path:
                            source.seek(0)
                            archive.addfile(item, source)
                        else:
                            with input_path.open("rb") as snapshot_file:
                                archive.addfile(item, snapshot_file)
                        total += size
            if destination.stat().st_size > max_bytes + max_files * 2048:
                raise RuntimeError("Backup archive limit exceeded")
    os.chmod(destination, 0o600)


def backup(*, include_credentials: bool = False, local_only: bool = False) -> Path:
    require_private(STATE, directory=True)
    owner_id()
    # No arbitrary export path: backups always remain in private local app storage.
    destination = STATE / "backups"
    destination.mkdir(mode=0o700, exist_ok=True)
    require_private(destination, directory=True)
    folder = Path(tempfile.mkdtemp(prefix=time.strftime("%Y%m%d-%H%M%S-"), dir=destination))
    try:
        selections = ["connection.json", "ownership.json", "agent-state"]
        if include_credentials:
            selections += ["server.token", "agent.token", "approvals.token"]
        snapshot_tar(STATE, selections, folder / "mac-state.tar")
        if not local_only:
            container = current_container()
            if container is None:
                raise RuntimeError("NAS backup requires a verified owned container; use --local-only for offline Mac backup.")
            # Transmit only a bounded, read-only snapshot, never remote secret files.
            script = (inspect.getsource(snapshot_tar) +
                      "\nimport os,pathlib,sys,tempfile\n" +
                      "with tempfile.TemporaryDirectory(prefix='tapmesh-backup-') as work:\n" +
                      " p=pathlib.Path(work)/'state.tar'\n" +
                      " snapshot_tar('/Volume1/.tapmesh',['server-state'],p)\n" +
                      " with p.open('rb') as source:\n" +
                      "  while chunk:=source.read(65536):sys.stdout.buffer.write(chunk)\n")
            result = remote_command(["python3", "-c", script], capture=True)
            if len(result.stdout) > 111_000_000:
                raise RuntimeError("Remote backup exceeded archive limit")
            total = count = 0
            try:
                with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as contents:
                    for item in contents:
                        relative = Path(item.name)
                        total += item.size
                        count += 1
                        if (not item.isfile() or relative.is_absolute() or ".." in relative.parts or
                                not relative.parts or relative.parts[0] != "server-state" or
                                total > 100_000_000 or count > 5000):
                            raise RuntimeError("Unsafe remote state snapshot")
            except tarfile.TarError:
                raise RuntimeError("Invalid remote state snapshot archive") from None
            archive = folder / "nas-state.tar"
            fd = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as output:
                output.write(result.stdout)
        private_json(folder / "backup.json", {"owner_id": owner_id(),
                     "credentials_included": include_credentials, "nas_included": not local_only})
    except BaseException:
        shutil.rmtree(folder)
        raise
    print(f"Private backup created: {folder}; credentials {'included' if include_credentials else 'excluded'}.")
    return folder


def verify_launchagent() -> dict:
    require_private(PLIST)
    value = plistlib.loads(PLIST.read_bytes())
    c = config()
    if (value.get("Label") != LABEL or value.get("WorkingDirectory") != str(STATE) or
            value.get("ProgramArguments") != [c["python"], str(STATE / "manage.py"), "supervise"] or
            value.get("TapMeshOwnerID") not in (None, owner_id())):
        raise RuntimeError("LaunchAgent ownership/configuration mismatch; refusing operation.")
    # Older project plists predate owner IDs. Exact owner-private executable state,
    # argv and label are required for that narrowly scoped local migration.
    require_private(STATE, directory=True)
    return value


def record_installed_app(path: Path) -> None:
    path = Path(path)
    expected = Path.home() / "Applications/TapMesh.app"
    if path != expected:
        raise RuntimeError("Only the current user's TapMesh app may be recorded.")
    reject_symlinks(path)
    executable = path / "Contents/MacOS/TapMesh"
    for component in (path, path / "Contents", path / "Contents/MacOS", executable, path / "Contents/Info.plist"):
        if component.is_symlink() or component.stat().st_uid != os.getuid():
            raise RuntimeError("Installed app contains foreign/symlinked ownership metadata.")
    info = plistlib.loads((path / "Contents/Info.plist").read_bytes())
    if info.get("CFBundleIdentifier") != "io.tapmesh.mac":
        raise RuntimeError("Installed app bundle identity mismatch.")
    manifest = ownership_manifest()
    with executable.open("rb") as binary:
        digest = hashlib.file_digest(binary, "sha256").hexdigest()
    manifest["app"] = {"path": str(path), "executable_sha256": digest}
    private_json(STATE / "ownership.json", manifest)


def verified_installed_app() -> Path | None:
    path = Path.home() / "Applications/TapMesh.app"
    if not path.exists() and not path.is_symlink():
        return None
    reject_symlinks(path)
    receipt = ownership_manifest().get("app", {})
    if receipt.get("path") != str(path):
        raise RuntimeError("Installed app has no private ownership receipt; refusing removal.")
    executable = path / "Contents/MacOS/TapMesh"
    for current, directories, files in os.walk(path, followlinks=False):
        for entry in [Path(current), *(Path(current) / name for name in directories + files)]:
            if entry.is_symlink() or entry.lstat().st_uid != os.getuid():
                raise RuntimeError("Installed app contains a symlink or foreign owner; refusing removal.")
    info = plistlib.loads((path / "Contents/Info.plist").read_bytes())
    with executable.open("rb") as binary:
        digest = hashlib.file_digest(binary, "sha256").hexdigest()
    if info.get("CFBundleIdentifier") != "io.tapmesh.mac" or digest != receipt.get("executable_sha256"):
        raise RuntimeError("Installed app differs from ownership receipt; refusing removal.")
    return path


def uninstall(*, local_only: bool = False) -> None:
    owner_id()
    app = verified_installed_app()
    has_plist = PLIST.exists() or PLIST.is_symlink()
    if has_plist:
        verify_launchagent()
    containers = []
    if not local_only:
        current = current_container()  # A foreign canonical name blocks all changes.
        if current:
            containers.append(current["Id"])
        result = remote_command(["docker", "ps", "-a", "--no-trunc", "--filter",
                                 "label=" + PROJECT_LABEL + "=tapmesh", "--filter",
                                 "label=" + OWNER_LABEL + "=" + owner_id(), "--format", "{{.ID}}"], capture=True)
        for identity in result.stdout.decode().split():
            container = verify_owned(identity)
            if container.get("Name") != "/" + CONTAINER and not container.get("Name", "").startswith("/" + CONTAINER + "-previous-"):
                raise RuntimeError("Unexpected owned container name; refusing uninstall.")
            if identity not in containers:
                containers.append(identity)
        for identity in ownership_manifest()["containers"]:
            if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{64}", identity):
                raise RuntimeError("Invalid private container ownership record.")
            if identity in containers:
                continue
            try:
                container = verify_owned(identity)
            except subprocess.CalledProcessError as error:
                if b"No such container" in (error.stderr or b""):
                    continue
                raise
            if container.get("Name") != "/" + CONTAINER and not container.get("Name", "").startswith("/" + CONTAINER + "-previous-"):
                raise RuntimeError("Adopted container name changed; refusing uninstall.")
            containers.append(identity)
    # Every target was verified before the first destructive action.
    if has_plist:
        result = subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
        if result.returncode not in (0, 3, 113):
            raise RuntimeError("LaunchAgent bootout failed; private data and application retained.")
        verify_launchagent()
        PLIST.unlink()
    for identity in containers:
        owned_action("stop", identity, "--time", "20")
        owned_action("rm", identity)
    if app:
        verified_installed_app()
        shutil.rmtree(app)
    print("Uninstalled verified TapMesh services/application; private data, credentials, backups and images retained.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    setup = sub.add_parser("configure")
    setup.add_argument("--ssh-alias", help="Private deployment SSH alias; preserves the existing profile when omitted")
    setup.add_argument("--nas-hostname", help="Private deployment hostname; preserves the existing profile when omitted")
    setup.add_argument("--host-key-alias", help="Explicit known-host identity; preserves the existing profile when omitted")
    setup.add_argument("--remote-project-root", help="Private deployment directory; preserves the existing profile when omitted")
    adopt = sub.add_parser("adopt-container", help="Explicitly trust one verified unlabeled initial container")
    adopt.add_argument("--container-id", required=True)
    backup_parser = sub.add_parser("backup", help="Snapshot bounded state into owner-only local app storage")
    backup_parser.add_argument("--include-credentials", action="store_true")
    backup_parser.add_argument("--local-only", action="store_true")
    uninstall_parser = sub.add_parser("uninstall", help="Remove only verified owned services/app; retain data")
    uninstall_parser.add_argument("--local-only", action="store_true")
    for name in ("install-agent", "deploy", "start", "stop", "status", "supervise", "stop-nas"):
        sub.add_parser(name)
    args = parser.parse_args()
    commands = {"configure": lambda: configure(args), "install-agent": install_agent,
                "deploy": deploy, "start": start, "stop": stop, "status": status,
                "supervise": supervisor, "stop-nas": stop_nas,
                "adopt-container": lambda: adopt_container(args.container_id),
                "backup": lambda: backup(include_credentials=args.include_credentials, local_only=args.local_only),
                "uninstall": lambda: uninstall(local_only=args.local_only)}
    try:
        if args.command in {"status", "supervise"}:
            commands[args.command]()
        else:
            with operation_lock():
                commands[args.command]()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, RuntimeError, ValueError) as error:
        # Never include arbitrary subprocess stdout/stderr or payloads in CLI errors.
        detail = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        raise SystemExit(f"Operation failed: {detail}") from error


if __name__ == "__main__":
    main()
