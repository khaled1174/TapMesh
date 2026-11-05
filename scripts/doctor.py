#!/usr/bin/env python3
"""Read-only TapMesh prerequisite checks; never display credentials."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys


def private_file(path: Path) -> bool:
    return (not path.is_symlink() and path.is_file() and
            path.stat().st_uid == os.getuid() and path.stat().st_mode & 0o077 == 0)


def main() -> None:
    passed = True
    for command in ("xcrun", "xcodebuild", "ssh", "uv", "ffmpeg"):
        path = shutil.which(command)
        print(f"{'OK' if path else 'MISSING'} {command}: {path or 'install prerequisite'}")
        passed &= bool(path)
    state = Path.home() / "Library/Application Support/TapMesh"
    for name in ("agent.token", "server.token", "approvals.token"):
        path = state / name
        ok = private_file(path)
        print(f"{'OK' if ok else 'MISSING/UNSAFE'} private {name}")
        passed &= ok
    config = state / "connection.json"
    if private_file(config):
        try:
            values = json.loads(config.read_text())
            companion = Path(values.get("idb_companion", ""))
            ok = companion.is_file() and os.access(companion, os.X_OK)
            print(f"{'OK' if ok else 'MISSING/NOT EXECUTABLE'} qualified companion")
            passed &= ok
        except (ValueError, TypeError, OSError):
            print("FAILED connection.json validation")
            passed = False
    else:
        print("MISSING/UNSAFE connection.json; run manage.py configure")
        passed = False
    for label, argv in (("working Xcode", ["xcodebuild", "-version"]),
                        ("simctl runtimes", ["xcrun", "simctl", "list", "runtimes"])):
        try:
            result = subprocess.run(argv, check=False, capture_output=True, timeout=30)
            ok = result.returncode == 0
            # Tool output is read-only prerequisite information, never configuration or tokens.
            detail = (result.stdout if ok else result.stderr) or b""
            if isinstance(detail, bytes):
                detail = detail.decode(errors="replace")
            print(f"{'OK' if ok else 'FAILED'} {label}: exit {result.returncode}")
            print(detail[:4096].strip())
            passed &= ok
        except (OSError, subprocess.TimeoutExpired) as error:
            print(f"FAILED {label}: {type(error).__name__}")
            passed = False
    print("Health: scripts/manage.py status. Hardware tests require a booted project-owned simulator.")
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
