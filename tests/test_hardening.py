"""Isolated regressions for independently reproduced core boundary failures."""
import asyncio
import hashlib
import json
import os
import signal
import sys
import time

import httpx
import pytest
from starlette.testclient import TestClient

from agent.core import Router
from agent.safety import Approvals, BridgeError
from test_router import DeviceFixture


async def approve(router, session, tool, arguments):
    pending = await router.call(session, tool, arguments)
    router.approvals.decide(pending['error']['details']['approval_id'], 'allow')


class ControlledBackend(DeviceFixture):
    def __init__(self):
        super().__init__()
        self.epoch = 1
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.cleaned = asyncio.Event()
        self.recordings = {'recording': {'session_id': 'owner', 'device_id': 'sim:fixture'}}

    async def discover(self, include_all=False):
        devices = await super().discover(include_all)
        devices[0]['connection_epoch'] = self.epoch
        other = dict(devices[0], device_id='sim:other', native_id='other')
        return devices + [other]


@pytest.fixture
async def hardened(tmp_path):
    backend = ControlledBackend()
    router = Router(backend, state_dir=tmp_path)
    yield router, backend
    backend.release.set()
    await router.close()


async def test_sent_readback_failure_stops_batch_and_propagates(hardened):
    router, backend = hardened
    async def invoke(tool, device, args, context):
        backend.calls.append(tool)
        if tool == 'open_url':
            context['sent'] = True
            raise BridgeError('backend_failure', 'Observation failed after input')
        return {'apps': []}
    backend.invoke = invoke
    args = {'device_id': 'sim:fixture', 'stop_on_error': False, 'commands': [
        {'tool': 'open_url', 'arguments': {'url': 'https://example.com'}},
        {'tool': 'list_apps', 'arguments': {}}]}
    await approve(router, 'owner', 'batch_commands', args)
    result = await router.call('owner', 'batch_commands', args)
    assert result['data']['results'][0]['error']['side_effect_state'] == 'unknown'
    assert backend.calls == ['open_url']


async def test_batch_rechecks_epoch_before_each_dispatch(hardened):
    router, backend = hardened
    async def invoke(tool, device, args, context):
        backend.calls.append(tool)
        if tool == 'list_apps': backend.epoch += 1
        return {'apps': []}
    backend.invoke = invoke
    args = {'device_id': 'sim:fixture', 'stop_on_error': False, 'commands': [
        {'tool': 'list_apps', 'arguments': {}},
        {'tool': 'open_url', 'arguments': {'url': 'https://example.com'}}]}
    await approve(router, 'owner', 'batch_commands', args)
    result = await router.call('owner', 'batch_commands', args)
    assert backend.calls == ['list_apps']
    assert result['data']['results'][1]['error']['code'] == 'connection_changed'
    assert result['data']['results'][1]['error']['side_effect_state'] == 'not_sent'


@pytest.mark.parametrize('source', ['accessibility', 'screenshot', 'ocr'])
async def test_inflight_observation_cannot_publish_at_new_revision(hardened, source):
    router, backend = hardened
    async def capture(tool, device, args, context):
        if tool == 'open_url': return {'delivery': 'confirmed'}
        backend.entered.set()
        await backend.release.wait()
        if tool == 'take_screenshot':
            obs = router.observations.issue('owner', device['device_id'], device['connection_epoch'], {'width': 100, 'height': 200}, (100, 200), 'capture')
            return {**obs, 'image': {'mime_type': 'image/png', 'data': ''}}
        return {'source': 'accessibility', 'elements': [], 'logical_size': {'width': 100, 'height': 200}}
    backend.invoke = capture
    if source == 'ocr':
        class OCR:
            async def available(self): return True
            async def close(self): pass
            async def inspect(self, backend, device, context):
                await capture('list_elements', device, {}, context)
                return {'source': 'ocr', 'elements': [], 'logical_size': {'width': 100, 'height': 200}}
        router.ocr = OCR()
    tool = 'take_screenshot' if source == 'screenshot' else 'list_elements'
    args = {'device_id': 'sim:fixture'}
    if tool == 'list_elements': args['source'] = source
    observation = asyncio.create_task(router.call('owner', tool, args))
    await backend.entered.wait()
    mutation = {'device_id': 'sim:fixture', 'url': 'https://example.com'}
    await approve(router, 'owner', 'open_url', mutation)
    assert (await router.call('owner', 'open_url', mutation))['status'] == 'success'
    backend.release.set()
    result = await observation
    assert result['status'] == 'error'
    assert result['error']['code'] == 'stale_observation'
    assert not router.observations.records


async def test_takeover_cancels_active_and_waiting_without_blocking_other_device(hardened):
    router, backend = hardened
    async def invoke(tool, device, args, context):
        backend.calls.append((tool, device['device_id']))
        if device['device_id'] == 'sim:fixture':
            backend.entered.set()
            try: await backend.release.wait()
            finally:
                await asyncio.sleep(0)
                backend.cleaned.set()
        return {'delivery': 'confirmed'}
    backend.invoke = invoke
    args = {'device_id': 'sim:fixture', 'url': 'https://example.com'}
    other = dict(args, device_id='sim:other')
    for session, action in [('active', args), ('queued', args), ('other', other)]:
        await approve(router, session, 'open_url', action)
    active = asyncio.create_task(router.call('active', 'open_url', args))
    await backend.entered.wait()
    queued = asyncio.create_task(router.call('queued', 'open_url', args))
    await asyncio.sleep(.01)
    assert (await router.call('other', 'open_url', other))['status'] == 'success'
    result = await router.takeover('owner', 'sim:fixture')
    assert result['data']['cancelled_requests'] == 2
    assert result['data']['ownership_generation'] >= 1
    assert backend.cleaned.is_set()
    assert (await active)['error']['side_effect_state'] == 'unknown'
    assert (await queued)['error']['side_effect_state'] == 'not_sent'
    assert backend.calls.count(('open_url', 'sim:fixture')) == 1


async def test_stop_recording_does_not_wait_for_device_work_and_serializes_itself(hardened):
    router, backend = hardened
    concurrent = 0
    maximum = 0
    async def invoke(tool, device, args, context):
        nonlocal concurrent, maximum
        assert tool == 'stop_recording'
        concurrent += 1; maximum = max(maximum, concurrent)
        await asyncio.sleep(.01)
        concurrent -= 1
        return {'recording_id': 'recording', 'state': 'complete'}
    backend.invoke = invoke
    lock = router.locks.setdefault('sim:fixture', asyncio.Lock())
    await lock.acquire()
    try:
        tasks = [asyncio.create_task(router.call('owner', 'stop_recording', {'device_id': 'sim:fixture', 'recording_id': 'recording', 'timeout_ms': 100})) for _ in range(2)]
        results = await asyncio.wait_for(asyncio.gather(*tasks), .5)
        assert all(result['status'] == 'success' for result in results)
        assert maximum == 1
    finally: lock.release()


def test_expired_and_denied_approval_reissue_never_reuses_authorization():
    approvals = Approvals(ttl=60)
    action = ('owner', 'sim:fixture', 1, 'open_url', {'url': 'https://example.com'})
    with pytest.raises(BridgeError) as pending: approvals.require(*action, policy='C')
    old = pending.value.details['approval_id']
    approvals.records[old]['_expires'] = time.monotonic() - 1
    with pytest.raises(BridgeError) as reissued: approvals.require(*action, policy='C')
    assert reissued.value.code == 'approval_required'
    new = reissued.value.details['approval_id']
    assert new != old
    with pytest.raises(BridgeError): approvals.decide(old, 'allow')
    approvals.decide(new, 'deny')
    with pytest.raises(BridgeError) as denied: approvals.require(*action, policy='C')
    assert denied.value.code == 'approval_denied'
    with pytest.raises(BridgeError) as again: approvals.require(*action, policy='C')
    assert again.value.code == 'approval_required'
    assert again.value.details['approval_id'] != new


def test_approval_admission_and_terminal_retention_are_bounded():
    approvals = Approvals(ttl=60, maximum=2, terminal_ttl=0)
    for n in range(2):
        with pytest.raises(BridgeError) as pending: approvals.require('owner', 'sim:fixture', 1, 'open_url', {'url': f'https://example.com/{n}'}, policy='C')
    with pytest.raises(BridgeError) as full: approvals.require('owner', 'sim:fixture', 1, 'open_url', {'url': 'https://example.com/full'}, policy='C')
    assert full.value.code == 'overloaded'
    aid = next(iter(approvals.records))
    approvals.decide(aid, 'deny')
    approvals.pending()
    assert len(approvals.records) == len(approvals.actions) == 1


async def test_session_expiry_and_capacity(hardened):
    router, _ = hardened
    router.max_sessions = 2
    router.session_ttl = .01
    assert (await router.call('one', 'select_device', {'device_id': 'sim:fixture'}))['status'] == 'success'
    assert (await router.call('two', 'list_devices', {}))['status'] == 'success'
    assert (await router.call('three', 'list_devices', {}))['error']['code'] == 'overloaded'
    await asyncio.sleep(.02)
    assert (await router.call('three', 'list_devices', {}))['status'] == 'success'
    assert (await router.call('one', 'get_device_info', {}))['error']['code'] == 'device_not_selected'
    assert 'one' not in router.sessions


async def test_device_queue_is_bounded_and_overload_never_sends(hardened):
    router, backend = hardened
    router.max_device_requests = 1
    async def invoke(tool, device, args, context):
        backend.entered.set(); await backend.release.wait()
        return {'delivery': 'confirmed'}
    backend.invoke = invoke
    args = {'device_id': 'sim:fixture', 'url': 'https://example.com'}
    await approve(router, 'one', 'open_url', args)
    await approve(router, 'two', 'open_url', args)
    active = asyncio.create_task(router.call('one', 'open_url', args))
    await backend.entered.wait()
    try:
        result = await asyncio.wait_for(router.call('two', 'open_url', args), .1)
        assert result['error']['code'] == 'overloaded'
        assert result['error']['side_effect_state'] == 'not_sent'
    finally:
        backend.release.set(); await active


async def test_runner_cleans_exited_leader_descendant_and_pipe_tasks(tmp_path):
    from agent.process import Runner
    runner = Runner()
    pidfile = tmp_path / 'child.pid'
    program = "import subprocess,sys; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); open(sys.argv[1],'w').write(str(p.pid))"
    child = None
    try:
        with pytest.raises(BridgeError) as failed:
            await runner.run([sys.executable, '-c', program, str(pidfile)], timeout=.2)
        assert failed.value.code == 'deadline_exceeded'
        child = int(pidfile.read_text())
        async with asyncio.timeout(2):
            while True:
                try:
                    os.kill(child, 0)
                    # Linux orphan zombies are already terminated; macOS normally reaps immediately.
                    stat = __import__('pathlib').Path(f'/proc/{child}/stat')
                    if stat.exists() and stat.read_text().split()[2] == 'Z': break
                except ProcessLookupError: break
                await asyncio.sleep(.01)
        assert not runner.processes
    finally:
        if child:
            try: os.kill(child, signal.SIGKILL)
            except ProcessLookupError: pass
        await runner.close()


async def test_start_existing_simulator_shares_device_ownership(hardened):
    router, backend = hardened
    async def invoke(tool, device, args, context):
        backend.calls.append(tool)
        return {'device_id': 'sim:fixture', 'ready': True, 'created': False}
    backend.invoke = invoke
    args = {'simulator_id': 'sim:fixture', 'timeout_ms': 30}
    await approve(router, 'owner', 'start_simulator', args)
    lock = router.locks.setdefault('sim:fixture', asyncio.Lock())
    await lock.acquire()
    try:
        result = await router.call('owner', 'start_simulator', args)
        assert result['error']['code'] == 'deadline_exceeded'
        assert backend.calls == []
    finally: lock.release()


@pytest.mark.parametrize('transfer_ok', [True, False])
async def test_failure_detail_artifacts_transfer_preserves_execution_error(tmp_path, transfer_ok):
    from server.coordinator import Coordinator
    content = b'failed build diagnostic log'
    artifact = {'artifact_id': 'a' * 32, 'sha256': hashlib.sha256(content).hexdigest(), 'size_bytes': len(content), 'kind': 'build_log', 'state': 'complete', 'local_path': '/private/build.log'}
    error = {'code': 'build_failed', 'message': 'Build failed', 'side_effect_state': 'not_sent', 'retryable': False, 'details': {'stages': [{'artifact': artifact}]}}
    async def route(request):
        if request.url.path == '/v1/call': return httpx.Response(200, json={'request_id': 'failed', 'status': 'error', 'error': error})
        return httpx.Response(200, content=content) if transfer_ok else httpx.Response(503)
    async with httpx.AsyncClient(transport=httpx.MockTransport(route), base_url='http://127.0.0.1') as client:
        service = Coordinator(tmp_path, client)
        try:
            result = await service.call('owner', 'build_and_run', {'project_path': '/fixture', 'scheme': 'Fixture'})
            transferred = result['error']['details']['stages'][0]['artifact']
            assert transferred['storage'] == ('server' if transfer_ok else 'transfer_failed')
            assert result['status'] == 'error'
            assert result['error']['code'] == 'build_failed'
            assert transferred['local_path'] != '/private/build.log' if transfer_ok else 'local_path' not in transferred
        finally: await service.close()


def test_takeover_routes_require_owner_and_relay_exact_closed_payload(tmp_path):
    from agent.app import create_app as agent_app
    from server.app import create_app as server_app
    router = Router(ControlledBackend(), state_dir=tmp_path / 'agent')
    headers = {'Authorization': 'Bearer ' + 'a' * 64}
    owner = {**headers, 'X-DeviceBridge-Local-Authorization': 'Bearer ' + 'b' * 64}
    payload = {'session_id': 'owner', 'device_id': 'sim:fixture'}
    with TestClient(agent_app(router, token='a' * 64, approval_token='b' * 64)) as client:
        assert client.post('/v1/takeover', headers=headers, json=payload).status_code == 403
        result = client.post('/v1/takeover', headers=owner, json=payload)
        assert result.status_code == 200
        assert result.json()['data']['ownership_generation'] == 1
        assert client.post('/v1/takeover', headers=owner, json={**payload, 'extra': True}).status_code == 400
    calls = []
    async def route(request):
        calls.append((request.url.path, json.loads(request.content), request.headers.get('x-devicebridge-local-authorization')))
        return httpx.Response(200, json={'status': 'success', 'data': {'device_id': 'sim:fixture', 'connection_epoch': 1, 'cancelled_requests': 0, 'ownership_generation': 2}})
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(route), base_url='http://127.0.0.1')
    with TestClient(server_app(state_dir=tmp_path / 'server', token='a' * 64, agent_client=upstream)) as client:
        assert client.post('/api/takeover', headers=headers, json=payload).status_code == 403
        assert not calls
        assert client.post('/api/takeover', headers=owner, json=payload).json()['status'] == 'success'
        assert calls == [('/v1/takeover', payload, owner['X-DeviceBridge-Local-Authorization'])]


async def test_quiet_takeover_preserves_owner_targets_only(hardened):
    router, backend = hardened
    owner = router.observations.issue('owner', 'sim:fixture', 1, {'width': 100, 'height': 200}, None, 'capture')
    agent = router.observations.issue('agent', 'sim:fixture', 1, {'width': 100, 'height': 200}, None, 'capture')
    first = await router.takeover('owner', 'sim:fixture')
    assert first['data']['cancelled_requests'] == 0
    assert router.observations.record('owner', 'sim:fixture', 1, owner['observation_id'])
    with pytest.raises(BridgeError): router.observations.record('agent', 'sim:fixture', 1, agent['observation_id'])
    args = {'device_id': 'sim:fixture', 'target': {'x': 10, 'y': 10, 'coordinate_space': 'device_points', 'observation_id': owner['observation_id']}}
    await approve(router, 'owner', 'tap', args)
    assert (await router.call('owner', 'tap', args))['status'] == 'success'


async def test_capture_epoch_change_is_not_published(hardened):
    router, backend = hardened
    async def invoke(tool, device, args, context):
        backend.epoch = 2
        return {'source': 'accessibility', 'elements': [], 'logical_size': {'width': 100, 'height': 200}}
    backend.invoke = invoke
    result = await router.call('owner', 'list_elements', {'device_id': 'sim:fixture'})
    assert result['error']['code'] == 'stale_observation'
    assert not router.observations.records


async def test_simulator_creation_binds_lease_before_boot_and_releases_creation_gate(hardened):
    router, backend = hardened
    bound = asyncio.Event()
    async def invoke(tool, device, args, context):
        backend.calls.append((tool, args))
        if tool == 'start_simulator':
            found = (await backend.discover())[0]
            await context['bind_simulator'](found)
            bound.set()
            await backend.release.wait()
            return {'device_id': 'sim:fixture', 'created': True, 'ready': True}
        return {'delivery': 'confirmed'}
    backend.invoke = invoke
    create = {'device_type_id': 'type.fixture', 'runtime_id': 'runtime.fixture'}
    await approve(router, 'owner', 'start_simulator', create)
    creation = asyncio.create_task(router.call('owner', 'start_simulator', create))
    await bound.wait()
    assert not router.creation_lock.locked()
    blocked = {'device_id': 'sim:fixture', 'url': 'https://example.com', 'timeout_ms': 20}
    other = dict(blocked, device_id='sim:other')
    await approve(router, 'blocked', 'open_url', blocked)
    await approve(router, 'other', 'open_url', other)
    assert (await router.call('blocked', 'open_url', blocked))['error']['code'] == 'deadline_exceeded'
    assert (await router.call('other', 'open_url', other))['status'] == 'success'
    backend.release.set()
    assert (await creation)['status'] == 'success'


async def test_takeover_cancels_simulator_creation_after_actual_device_binding(hardened):
    router, backend = hardened
    async def invoke(tool, device, args, context):
        await context['bind_simulator']((await backend.discover())[0])
        backend.entered.set()
        try: await backend.release.wait()
        finally: backend.cleaned.set()
    backend.invoke = invoke
    args = {'device_type_id': 'type.fixture', 'runtime_id': 'runtime.fixture'}
    await approve(router, 'agent', 'start_simulator', args)
    creation = asyncio.create_task(router.call('agent', 'start_simulator', args))
    await backend.entered.wait()
    result = await router.takeover('owner', 'sim:fixture')
    assert result['data']['cancelled_requests'] == 1
    assert backend.cleaned.is_set()
    assert (await creation)['error']['side_effect_state'] == 'unknown'
    assert not router.creation_lock.locked()


async def test_runner_repeated_cancellation_settles_all_owned_read_tasks(tmp_path):
    from agent.process import Runner
    runner = Runner()
    before = set(asyncio.all_tasks())
    ready = tmp_path / 'ready'
    program = "import signal,time,sys; signal.signal(signal.SIGTERM,lambda *_:None); open(sys.argv[1],'w').write('ready'); time.sleep(60)"
    task = asyncio.create_task(runner.run([sys.executable, '-c', program, str(ready)], timeout=90))
    async with asyncio.timeout(2):
        while not ready.exists(): await asyncio.sleep(.01)
    task.cancel()
    await asyncio.sleep(.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    await asyncio.sleep(0)
    assert not runner.processes
    assert not runner._groups
    assert not [owned for owned in asyncio.all_tasks() - before if owned is not asyncio.current_task() and not owned.done()]


@pytest.mark.parametrize('live_members', [False, True])
async def test_darwin_permission_probe_requires_evidence_of_no_live_group_members(monkeypatch, live_members):
    import agent.process as process
    runner = process.Runner()
    monkeypatch.setattr(process.sys, 'platform', 'darwin')
    def denied(group, sig): raise PermissionError('controlled permission probe')
    monkeypatch.setattr(process.os, 'killpg', denied)
    async def inspect(group):
        assert group == 54321
        return live_members
    monkeypatch.setattr(process.Runner, '_darwin_group_has_live_members', staticmethod(inspect))
    if live_members:
        with pytest.raises(PermissionError): await runner._group_exists(54321)
        with pytest.raises(PermissionError): await runner._signal_owned_group(54321, signal.SIGTERM)
    else:
        assert await runner._group_exists(54321) is False
        await runner._signal_owned_group(54321, signal.SIGTERM)


async def test_runner_terminate_does_not_signal_unregistered_group(tmp_path):
    from agent.process import Runner
    proc = await asyncio.create_subprocess_exec(sys.executable, '-c', 'import time; time.sleep(60)', start_new_session=True)
    try:
        await Runner().terminate(proc)
        assert proc.returncode is None
        os.kill(proc.pid, 0)
    finally:
        os.killpg(proc.pid, signal.SIGKILL)
        await proc.wait()


async def test_failed_build_storage_admission_preserves_build_error(tmp_path):
    from server.coordinator import Coordinator
    artifact = {'artifact_id': 'a' * 32, 'size_bytes': 0, 'sha256': hashlib.sha256(b'').hexdigest(), 'state': 'complete', 'local_path': '/private/log.txt'}
    async def route(request):
        return httpx.Response(200, json={'request_id': 'failed-storage', 'status': 'error', 'error': {'code': 'build_failed', 'message': 'Compilation failed', 'side_effect_state': 'not_sent', 'retryable': False, 'details': {'artifact': artifact}}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(route), base_url='http://127.0.0.1') as client:
        service = Coordinator(tmp_path, client)
        def no_storage(*args): raise BridgeError('storage_full', 'No storage available')
        service.artifacts.check_storage = no_storage
        try:
            result = await service.call('owner', 'build_and_run', {'project_path': '/fixture', 'scheme': 'Fixture'})
            assert result['status'] == 'error'
            assert result['error']['code'] == 'build_failed'
            assert result['error']['details']['artifact']['storage'] == 'transfer_failed'
            assert result['artifact_transfer_status'] == 'partial'
        finally: await service.close()


async def test_locally_approved_handoff_preempts_registered_device_work(hardened):
    from agent.app import create_app
    router, backend = hardened
    async def invoke(tool, device, args, context):
        backend.entered.set()
        try: await backend.release.wait()
        finally: backend.cleaned.set()
        return {'delivery': 'confirmed'}
    backend.invoke = invoke
    args = {'device_id': 'sim:fixture', 'url': 'https://example.com'}
    await approve(router, 'agent', 'open_url', args)
    active = asyncio.create_task(router.call('agent', 'open_url', args))
    await backend.entered.wait()
    handoff_args = {'device_id': 'sim:fixture', 'reason': 'Owner handoff', 'instructions': 'Use the phone to complete this action'}
    pending = await router.call('owner', 'request_human_action', handoff_args)
    aid = pending['error']['details']['approval_id']
    app = create_app(router, token='a' * 64, approval_token='b' * 64)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1') as client:
        response = await client.post('/v1/approvals/' + aid, headers={'Authorization': 'Bearer ' + 'a' * 64, 'X-DeviceBridge-Local-Authorization': 'Bearer ' + 'b' * 64}, json={'decision': 'allow'})
    assert response.status_code == 200
    assert backend.cleaned.is_set()
    assert (await active)['error']['side_effect_state'] == 'unknown'
    assert (await router.call('owner', 'request_human_action', handoff_args))['status'] == 'success'


async def test_real_mac_fill_readback_failure_keeps_actual_dispatch_unknown(tmp_path):
    from agent.backends import MacBackend
    from agent.process import Output
    class FillBackend(MacBackend):
        def __init__(self):
            super().__init__(tmp_path / 'backend')
            self.field = 'old'
            self.dispatched = []
        async def discover(self, include_all=False): return await DeviceFixture().discover(include_all)
        async def geometry(self, device): return {'logical_size': {'width': 100, 'height': 200}, 'orientation': 'portrait'}
        async def _idb(self, device, *args, **kwargs):
            if args[:2] == ('ui', 'set-value'):
                self.field = args[-1]
                self.dispatched.append('set-value')
                return Output(b'', b'', 0)
            raise BridgeError('backend_failure', 'AX readback unavailable')
        async def invoke(self, tool, device, args, context):
            if tool == 'list_elements': return {'logical_size': {'width': 100, 'height': 200}, 'elements': [], 'source': 'accessibility'}
            if tool == 'open_url': self.dispatched.append('open_url'); return {'delivery': 'confirmed'}
            return await super().invoke(tool, device, args, context)
    backend = FillBackend()
    router = Router(backend, state_dir=tmp_path / 'router')
    try:
        obs = router.observations.issue('owner', 'sim:fixture', 1, {'width': 100, 'height': 200}, None, 'capture', orientation='portrait')
        args = {'device_id': 'sim:fixture', 'stop_on_error': False, 'commands': [
            {'tool': 'fill', 'arguments': {'target': {'x': 5, 'y': 5, 'coordinate_space': 'device_points', 'observation_id': obs['observation_id']}, 'text': 'new'}},
            {'tool': 'open_url', 'arguments': {'url': 'https://example.com'}}]}
        await approve(router, 'owner', 'batch_commands', args)
        result = await router.call('owner', 'batch_commands', args)
        assert backend.field == 'new'
        assert backend.dispatched == ['set-value']
        assert result['data']['results'][0]['error']['side_effect_state'] == 'unknown'
    finally: await router.close()


async def test_batch_unexpected_exception_keeps_parent_dispatch_state(hardened):
    router, backend = hardened
    async def invoke(tool, device, args, context):
        context['sent'] = True
        backend.calls.append(tool)
        raise RuntimeError('private readback failure')
    backend.invoke = invoke
    args = {'device_id': 'sim:fixture', 'stop_on_error': False, 'commands': [
        {'tool': 'open_url', 'arguments': {'url': 'https://example.com'}},
        {'tool': 'list_apps', 'arguments': {}}]}
    await approve(router, 'owner', 'batch_commands', args)
    result = await router.call('owner', 'batch_commands', args)
    assert result['error']['side_effect_state'] == 'unknown'
    assert backend.calls == ['open_url']


async def test_observation_binds_copied_discovery_epoch(hardened):
    router, backend = hardened
    reused = (await backend.discover())[0]
    async def discover(include_all=False): return [reused]
    async def invoke(tool, device, args, context):
        assert device['connection_epoch'] == 1
        reused['connection_epoch'] = 2
        return {'source': 'accessibility', 'elements': [], 'logical_size': {'width': 100, 'height': 200}}
    backend.discover = discover
    backend.invoke = invoke
    result = await router.call('owner', 'list_elements', {'device_id': 'sim:fixture'})
    assert result['error']['code'] == 'stale_observation'


async def test_takeover_while_release_is_pending_waits_through_repeated_cancellation(hardened):
    router, backend = hardened
    releasing = asyncio.Event()
    released = asyncio.Event()
    async def invoke(tool, device, args, context):
        backend.entered.set()
        await backend.release.wait()
    async def release(device, context):
        releasing.set()
        await released.wait()
        backend.cleaned.set()
    backend.invoke = invoke
    backend.release_input = release
    obs = router.observations.issue('agent', 'sim:fixture', 1, {'width': 100, 'height': 200}, None, 'capture')
    args = {'device_id': 'sim:fixture', 'target': {'x': 10, 'y': 10, 'coordinate_space': 'device_points', 'observation_id': obs['observation_id']}}
    await approve(router, 'agent', 'tap', args)
    active = asyncio.create_task(router.call('agent', 'tap', args))
    await backend.entered.wait()
    takeover = asyncio.create_task(router.takeover('owner', 'sim:fixture'))
    await releasing.wait()
    takeover.cancel(); active.cancel()
    await asyncio.sleep(.01)
    assert not takeover.done()
    assert router.locks['sim:fixture'].locked()
    released.set()
    with pytest.raises(asyncio.CancelledError): await takeover
    assert (await active)['error']['side_effect_state'] == 'unknown'
    assert backend.cleaned.is_set()
    assert not router.locks['sim:fixture'].locked()


async def test_recording_stop_cancellation_drops_idle_lease(hardened):
    router, backend = hardened
    async def invoke(tool, device, args, context):
        backend.entered.set(); await backend.release.wait()
    backend.invoke = invoke
    stop = asyncio.create_task(router.call('owner', 'stop_recording', {'device_id': 'sim:fixture', 'recording_id': 'recording'}))
    await backend.entered.wait()
    stop.cancel()
    assert (await stop)['error']['code'] == 'cancelled'
    assert not router.recording_locks
