import importlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

import pytest


@pytest.fixture
def release_tools(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    return importlib.import_module('notarize_app'), importlib.import_module('qualify_native_update')


def write_info(bundle, build, **values):
    contents = bundle / 'Contents'
    contents.mkdir(parents=True, exist_ok=True)
    (contents / 'Info.plist').write_bytes(plistlib.dumps({
        'CFBundleIdentifier': 'io.tapmesh.mac',
        'CFBundleShortVersionString': '0.2.0',
        'CFBundleVersion': str(build),
        'SUPublicEDKey': 'fixture-public-key',
        **values,
    }))


def release_manifest(path, archive, notarized, file_digest):
    metadata = {
        'product': 'TapMesh',
        'version': '0.2.0',
        'build_number': 22,
        'signing': 'developer-id',
        'notarized': notarized,
        'updater_configured': True,
        'source_compliance_verified': True,
        'archive': archive.name,
        'sha256': file_digest(archive),
    }
    path.write_text(json.dumps(metadata))
    return metadata


@pytest.mark.parametrize('mismatch', ['nested_payload','extra_archive_entry'])
def test_notary_preflight_compares_full_app_before_reading_credentials(tmp_path, monkeypatch, release_tools, mismatch):
    notary, _ = release_tools
    app = tmp_path / 'TapMesh.app'
    write_info(app, 22)
    payload = app / 'Contents/Resources/Python/python'
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b'signed nested payload')
    payload.chmod(0o755)
    payload.with_name('python3').symlink_to('python')
    archive = tmp_path / 'TapMesh-release.zip'
    archive.write_bytes(b'frozen ZIP bytes')
    manifest = tmp_path / 'release-manifest.json'
    metadata = release_manifest(manifest, archive, False, notary.file_digest)
    workspace = tmp_path / 'preflight'
    workspace.mkdir()
    verified = []
    changed_archive = False

    def unpack(argv, *, check):
        assert argv[:3] == ['/usr/bin/ditto', '-x', '-k'] and check
        snapshot = Path(argv[-2])
        assert snapshot != archive and snapshot.read_bytes() == archive.read_bytes()
        extracted = Path(argv[-1]) / app.name
        shutil.copytree(app, extracted, symlinks=True)
        if changed_archive:
            if mismatch == 'nested_payload':
                (extracted / payload.relative_to(app)).write_bytes(b'different nested payload')
            else:
                extracted.with_name('unrelated.txt').write_text('extra file')
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(notary.subprocess, 'run', unpack)
    monkeypatch.setattr(notary, 'verify_app', verified.append)
    _, archived_app, result = notary.preflight(app, archive, manifest, workspace)
    assert result == metadata
    assert verified == [archived_app, app]

    changed_archive = True
    verified.clear()
    monkeypatch.setattr(notary, 'read_credentials', lambda _: pytest.fail('credential file was opened'))
    monkeypatch.setattr(sys, 'argv', ['notarize_app.py', '--app', str(app), '--archive', str(archive),
                                     '--manifest', str(manifest), '--credentials-file', 'unused'])
    message = 'different application bundles' if mismatch == 'nested_payload' else 'must contain only'
    with pytest.raises(SystemExit, match=message):
        notary.main()
    assert not verified


def test_bundle_comparison_detects_mode_and_symlink_changes(tmp_path, release_tools):
    notary, _ = release_tools
    expected = tmp_path / 'Expected.app'
    write_info(expected, 22)
    link = 'Contents/InfoLink'
    (expected / link).symlink_to('Info.plist')
    actual = tmp_path / 'Actual.app'
    shutil.copytree(expected, actual, symlinks=True)
    notary.require_same_bundle(actual, expected)
    (actual / link).unlink()
    (actual / link).symlink_to('./Info.plist')
    with pytest.raises(ValueError, match='bundle contents differ'):
        notary.require_same_bundle(actual, expected)
    (actual / link).unlink()
    (actual / link).symlink_to('Info.plist')
    (actual / 'Contents/Info.plist').chmod(0o600)
    (expected / 'Contents/Info.plist').chmod(0o644)
    with pytest.raises(ValueError, match='bundle contents differ'):
        notary.require_same_bundle(actual, expected)


def test_native_update_preflight_binds_exact_target_and_switches_to_valid(tmp_path, release_tools):
    notary, update = release_tools
    app = tmp_path / 'Installed/TapMesh.app'
    write_info(app, 21, TapMeshUpdateStagingMode=True, SUFeedURL='http://127.0.0.1:61000/appcast.xml')
    expected = tmp_path / 'TapMesh.app'
    write_info(expected, 22)
    archive = tmp_path / 'update.zip'
    archive.write_bytes(b'exact notarized ZIP')
    manifest = tmp_path / 'release-manifest.json'
    release_manifest(manifest, archive, True, notary.file_digest)
    disposition = {'installed_build': 21, 'candidate_build': 22, 'port': 61000,
                   'archive_sha256': notary.file_digest(archive)}
    qualification = tmp_path / 'qualification.json'
    qualification.write_text(json.dumps(disposition))
    control = tmp_path / 'control.json'
    control.write_text('{"mode":"tampered"}')

    assert update.ready_fixture(tmp_path, app, manifest, expected) == disposition
    state = update.valid_fixture_state(tmp_path)
    assert state.mode() == 'valid'
    assert os.stat(control).st_mode & 0o777 == 0o600

    archive.write_bytes(b'disposable fixture ZIP')
    disposition['archive_sha256'] = notary.file_digest(archive)
    qualification.write_text(json.dumps(disposition))
    assert update.ready_fixture(tmp_path, app) == disposition
    with pytest.raises(SystemExit, match='qualification inputs'):
        update.ready_fixture(tmp_path, app, manifest, expected)
