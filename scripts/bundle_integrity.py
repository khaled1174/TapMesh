"""Compare signed application bundle contents without following symlinks."""
import hashlib
import os
from pathlib import Path
import stat


def file_digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def bundle_inventory(bundle: Path) -> dict:
    entries = {}
    for path in (bundle, *bundle.rglob('*')):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            value = os.readlink(path)
        elif stat.S_ISREG(mode):
            value = file_digest(path)
        elif stat.S_ISDIR(mode):
            value = None
        else:
            raise ValueError('Unexpected entry type in the application bundle.')
        entries[path.relative_to(bundle)] = (mode, value)
    return entries


def require_same_bundle(actual: Path, expected: Path):
    if not actual.is_dir() or actual.is_symlink() or not expected.is_dir() or expected.is_symlink():
        raise ValueError('Both application bundles must be directories.')
    if bundle_inventory(actual) != bundle_inventory(expected):
        raise ValueError('Application bundle contents differ from the expected app.')
