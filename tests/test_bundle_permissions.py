import importlib.util
from pathlib import Path
import stat

import pytest


def normalizer():
    spec=importlib.util.spec_from_file_location('bundle_runtime_permissions',Path(__file__).resolve().parents[1]/'scripts/bundle_runtime.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.normalize_bundle_permissions


def test_shared_checkout_modes_cannot_make_installed_code_group_writable(tmp_path):
    bundle=tmp_path/'TapMesh.app';code=bundle/'Contents/Resources/agent'
    code.mkdir(parents=True)
    module=code/'core.py';module.write_text('owned code');module.chmod(0o770);code.chmod(0o770)
    executable=bundle/'Contents/TapMesh';executable.write_bytes(b'executable');executable.chmod(0o777)
    alias=bundle/'Contents/current';alias.symlink_to('TapMesh')
    normalizer()(bundle)
    assert not any(stat.S_IMODE(path.stat().st_mode)&0o022 for path in [bundle,*bundle.rglob('*')] if not path.is_symlink())
    assert module.read_text()=='owned code' and stat.S_IMODE(module.stat().st_mode)==0o755
    assert stat.S_IMODE(code.stat().st_mode)==0o755
    assert stat.S_IMODE(executable.stat().st_mode)==0o755
    assert alias.is_symlink() and alias.resolve()==executable


def test_public_bundle_remains_readable_if_installed_by_a_different_owner(tmp_path):
    bundle=tmp_path/'TapMesh.app';bundle.mkdir(mode=0o700)
    data=bundle/'Info.plist';data.write_text('public metadata');data.chmod(0o600)
    executable=bundle/'TapMesh';executable.write_bytes(b'executable');executable.chmod(0o700)
    normalizer()(bundle)
    assert stat.S_IMODE(bundle.stat().st_mode)==0o755
    assert stat.S_IMODE(data.stat().st_mode)==0o644
    assert stat.S_IMODE(executable.stat().st_mode)==0o755


def test_external_link_is_rejected_before_any_permission_mutation(tmp_path):
    bundle=tmp_path/'TapMesh.app';bundle.mkdir()
    outside=tmp_path/'outside';outside.write_text('unchanged');outside.chmod(0o666)
    code=bundle/'core.py';code.write_text('unchanged');code.chmod(0o770)
    (bundle/'external').symlink_to(outside)
    with pytest.raises(SystemExit,match='external symlink'):normalizer()(bundle)
    assert stat.S_IMODE(outside.stat().st_mode)==0o666
    assert stat.S_IMODE(code.stat().st_mode)==0o770
