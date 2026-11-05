#!/usr/bin/env python3
"""Install the checksum-pinned public idb companion in TapMesh's own cache."""
from pathlib import Path
import hashlib
import os
import subprocess
import tarfile

VERSION = "1.7.8"
SHA256 = "44369a2df0b62e3a1764de61bd09280999c7e9b27ea8f20bff1ea3a9db0302a2"
URL = f"https://github.com/meta/idb/releases/download/v{VERSION}/idb-companion.macos-arm64.tar.gz"
CACHE = Path.home() / "Library/Caches/TapMesh"


def main() -> None:
    if os.uname().machine != "arm64":
        raise SystemExit("This qualified companion archive supports Apple Silicon only.")
    CACHE.mkdir(parents=True, exist_ok=True)
    archive = CACHE / f"idb-companion-{VERSION}.tar.gz"
    if not archive.exists() or hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        temp = archive.with_suffix(".download")
        subprocess.run(["/usr/bin/curl", "--fail", "--location", "--retry", "2",
                        "--max-time", "180", "--output", str(temp), URL], check=True)
        if hashlib.sha256(temp.read_bytes()).hexdigest() != SHA256:
            temp.unlink()
            raise SystemExit("Dependency checksum mismatch; archive rejected.")
        os.replace(temp, archive)
    destination = CACHE / f"idb-companion-{VERSION}"
    destination.mkdir(exist_ok=True)
    with tarfile.open(archive) as source:
        source.extractall(destination, filter="data")
    binary = destination / "idb_companion"
    if not binary.is_file() or not (destination / "Resources").is_dir():
        raise SystemExit("Incomplete dependency layout; companion resources are required.")
    print(binary)


if __name__ == "__main__":
    main()
