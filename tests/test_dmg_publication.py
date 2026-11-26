"""Fault-injected DMG publication: no Apple tools, devices or network volumes."""
import importlib.util
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

import pytest


@pytest.mark.parametrize('failure', ['copy', 'signature', 'image'])
def test_failed_dmg_publication_leaves_no_final_path_and_can_retry(tmp_path, monkeypatch, failure):
    scripts = Path(__file__).resolve().parents[1] / 'scripts'
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location('tested_dmg_packager', scripts / 'package_dmg.py')
    packager = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(packager)
    app = tmp_path / 'TapMesh.app'
    (app / 'Contents').mkdir(parents=True)
    (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
        'CFBundleIdentifier': 'io.tapmesh.mac', 'CFBundleShortVersionString': '0.2.0'}))
    (app / 'Contents/payload').write_bytes(b'owned-fixture')
    output = tmp_path / 'output/candidate.dmg'
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.setattr(sys, 'argv', ['package_dmg.py', '--app', str(app), '--output', str(output), '--identity', 'fixture'])
    state = {'failure': failure, 'image_checks': 0, 'signature_checks': 0}
    original_copy = shutil.copyfileobj
    signed_payloads = []

    def copy(source, destination, *args, **kwargs):
        if state['failure'] == 'copy':
            destination.write(b'partial')
            raise OSError('synthetic disk failure')
        return original_copy(source, destination, *args, **kwargs)

    def run(argv, **kwargs):
        program = Path(argv[0]).name
        path = Path(argv[-1])
        if program == 'ditto':
            shutil.copytree(argv[1], argv[2], symlinks=True)
        elif program == 'hdiutil' and argv[1] == 'create':
            path.write_bytes(b'complete-signed-image')
        elif program == 'codesign' and '--verify' in argv:
            if path.suffix == '.app': signed_payloads.append(path)
            if path.suffix == '.dmg':
                state['signature_checks'] += 1
                if state['failure'] == 'signature' and state['signature_checks'] == 2:
                    raise subprocess.CalledProcessError(1, argv)
        elif program == 'hdiutil' and argv[1] == 'verify':
            state['image_checks'] += 1
            if state['failure'] == 'image' and state['image_checks'] == 2:
                raise subprocess.CalledProcessError(1, argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(packager.subprocess, 'run', run)
    monkeypatch.setattr(packager.shutil, 'copyfileobj', copy)
    with pytest.raises((OSError, subprocess.CalledProcessError)):
        packager.main()
    assert not output.exists()
    assert list(output.parent.iterdir()) == []
    state.update(failure=None, image_checks=0, signature_checks=0)
    packager.main()
    assert output.read_bytes() == b'complete-signed-image'
    assert any(path != app for path in signed_payloads), 'Copied app seal must be verified'
    assert list(output.parent.iterdir()) == [output]
