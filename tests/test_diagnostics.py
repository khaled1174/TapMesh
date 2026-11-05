import json
import os
from pathlib import Path
import stat

import pytest

from agent.diagnostics import MetadataDiagnostics, DiagnosticsUnavailable


def event(index=0):
    return {'event': 'unexpected_backend_exception', 'diagnostic_id': f'{index:032x}',
            'exception_type': 'RuntimeError', 'stack': [{'file': 'core.py', 'line': 42, 'function': 'call'}]}


def test_only_allowlisted_metadata_is_persisted(tmp_path):
    log = MetadataDiagnostics(tmp_path / 'agent-state')
    payload = event() | {'message': 'PRIVATE EXCEPTION', 'headers': {'Authorization': 'Bearer SECRET'},
                         'arguments': {'text': 'PRIVATE INPUT'}, 'device_id': 'PRIVATE UDID'}
    payload['stack'][0]['locals'] = {'token': 'PRIVATE TOKEN'}
    payload['stack'].append({'file': '/private/project/SECRET.py', 'line': 1, 'function': 'PRIVATE INPUT'})
    assert log.append(payload)
    stored = json.loads((log.directory / 'diagnostics.jsonl').read_text())
    assert stored == event()
    assert 'PRIVATE' not in json.dumps(stored) and 'SECRET' not in json.dumps(stored)


def test_unknown_exception_class_names_do_not_become_free_text(tmp_path):
    log = MetadataDiagnostics(tmp_path / 'state')
    assert log.append(event() | {'exception_type': 'CredentialValueThatLooksLikeAClass'})
    assert json.loads((log.directory / 'diagnostics.jsonl').read_text())['exception_type'] == 'UnexpectedError'


def test_private_capped_log_has_only_two_rotations(tmp_path):
    state = tmp_path / 'state'
    log = MetadataDiagnostics(state, max_bytes=512)
    for index in range(100):
        assert log.append(event(index))
    files = sorted(log.directory.iterdir())
    assert {file.name for file in files} == {'diagnostics.jsonl', 'diagnostics.jsonl.1', 'diagnostics.jsonl.2'}
    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    assert stat.S_IMODE(log.directory.stat().st_mode) == 0o700
    for file in files:
        assert file.stat().st_size <= 512
        assert stat.S_IMODE(file.stat().st_mode) == 0o600
        for line in file.read_text().splitlines():
            assert json.loads(line)['event'] == 'unexpected_backend_exception'


def test_existing_oversized_owned_log_is_bounded_without_reading_it(tmp_path):
    log = MetadataDiagnostics(tmp_path / 'state', max_bytes=512)
    old = log.directory / 'diagnostics.jsonl.2'
    old.write_bytes(b'PRIVATE OLD DATA' * 1000)
    old.chmod(0o600)
    assert log.append(event())
    assert old.stat().st_size == 0
    assert all(file.stat().st_size <= 512 for file in log.directory.iterdir())


def test_bundle_resources_are_never_created_or_modified(tmp_path):
    directory = tmp_path / 'TapMesh.app/Contents/Resources/state'
    with pytest.raises(DiagnosticsUnavailable):
        MetadataDiagnostics(directory)
    assert not (tmp_path / 'TapMesh.app').exists()


def test_symlink_directory_or_output_is_not_followed(tmp_path):
    target = tmp_path / 'elsewhere'; target.mkdir()
    state = tmp_path / 'state'; state.mkdir()
    (state / 'logs').symlink_to(target, target_is_directory=True)
    with pytest.raises(DiagnosticsUnavailable):
        MetadataDiagnostics(state)
    assert list(target.iterdir()) == []
    (state / 'logs').unlink()
    log = MetadataDiagnostics(state)
    secret = target / 'private.txt'; secret.write_text('PRIVATE DATA')
    (log.directory / 'diagnostics.jsonl').symlink_to(secret)
    assert not log.append(event())
    assert secret.read_text() == 'PRIVATE DATA'


def test_hardlinked_output_is_not_modified(tmp_path):
    log = MetadataDiagnostics(tmp_path / 'state')
    secret = tmp_path / 'secret.txt'; secret.write_text('PRIVATE DATA')
    os.link(secret, log.directory / 'diagnostics.jsonl')
    assert not log.append(event())
    assert secret.read_text() == 'PRIVATE DATA'


def test_logging_failure_does_not_replace_original_result(tmp_path, monkeypatch):
    log = MetadataDiagnostics(tmp_path / 'state')
    def full(*args):
        raise OSError('PRIVATE disk failure')
    monkeypatch.setattr(os, 'write', full)
    assert log.append(event()) is False


async def test_router_diagnostics_stay_in_their_own_profile(tmp_path, caplog):
    from agent.core import Router
    class BrokenBackend:
        async def discover(self, include_all=False):
            raise RuntimeError('PRIVATE payload and credential')
        async def close(self):
            pass
    routers = [Router(BrokenBackend(), state_dir=tmp_path / name) for name in ['first', 'second']]
    try:
        results = [await router.call('local-test', 'list_devices', {}) for router in routers]
        for router, result in zip(routers, results):
            log = router.diagnostics.directory / 'diagnostics.jsonl'
            rows = [json.loads(line) for line in log.read_text().splitlines()]
            assert len(rows) == 1
            assert rows[0]['diagnostic_id'] == result['error']['details']['diagnostic_id']
            assert 'PRIVATE' not in log.read_text()
        assert 'PRIVATE' not in caplog.text
    finally:
        for router in routers:
            await router.close()
