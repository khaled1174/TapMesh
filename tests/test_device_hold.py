import asyncio
import json
import stat
import time
from types import SimpleNamespace

import httpx
import pytest

from agent.app import create_app
from agent.core import Router
from agent.safety import BridgeError
from test_control_access import BEARER, OWNER, PHYSICAL, PRESS, PhysicalFixture
from test_fast_control import LOCAL, TOKEN


DEVICE = PHYSICAL['device_id']


class HeldFixture(PhysicalFixture):
    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.cleaned = asyncio.Event()

    async def invoke(self, tool, device, args, context):
        if tool == 'press_button' and context['session_id'] == 'slow':
            self.calls.append((tool, args))
            self.entered.set()
            try:
                await self.release.wait()
            finally:
                await asyncio.sleep(0)
                self.cleaned.set()
            return {'delivery': 'confirmed'}
        return await super().invoke(tool, device, args, context)


class LiveView:
    closed = False

    def close(self, reason=None):
        self.closed = True


@pytest.fixture
async def phone(tmp_path):
    backend = HeldFixture()
    router = Router(backend, state_dir=tmp_path, fast_control=True)
    router.access.update(owner_session='app')
    router.access.allow_device(DEVICE)
    try:
        yield router, backend
    finally:
        await router.close()


async def test_hold_cancels_active_and_queued_work_then_blocks_new_approvals(phone):
    router, backend = phone
    async with asyncio.TaskGroup() as tasks:
        active = tasks.create_task(router.call('slow', 'press_button', PRESS))
        await asyncio.wait_for(backend.entered.wait(), 1)
        queued = tasks.create_task(router.call('queued', 'press_button', PRESS))
        async with asyncio.timeout(1):
            while queued not in router.work[DEVICE]:
                await asyncio.sleep(0)
        assert router.activity()['devices'][0]['active']
        assert await router.hold(DEVICE, True) == {'device_id': DEVICE, 'held': True}
        assert backend.cleaned.is_set()
    assert active.result()['error']['code'] == 'cancelled'
    assert active.result()['error']['side_effect_state'] == 'unknown'
    assert queued.result()['error']['code'] == 'cancelled'
    assert queued.result()['error']['side_effect_state'] == 'not_sent'
    router.access.update(agent_devices=[])
    blocked = await router.call('another', 'press_button', PRESS)
    assert blocked['error']['code'] == 'device_held'
    assert blocked['error']['retryable'] and blocked['error']['side_effect_state'] == 'not_sent'
    handoff = await router.call('another', 'request_human_action', {'device_id': DEVICE, 'reason': 'Sign in', 'instructions': 'Sign in'})
    assert handoff['error']['code'] == 'device_held'
    assert len(backend.calls) == 1
    assert router.approvals.pending() == []


async def test_owner_keeps_control_while_held(phone):
    router, backend = phone
    await router.hold(DEVICE, True)
    assert (await router.call('app', 'press_button', PRESS, owner_authorized=True))['status'] == 'success'
    assert (await router.call('app', 'press_button', PRESS))['error']['code'] == 'local_authorization_required'
    assert (await router.call('other-app', 'press_button', PRESS, owner_authorized=True))['error']['code'] == 'device_held'
    assert len(backend.calls) == 1
    assert router.activity() == {'devices': [{'device_id': DEVICE, 'active': False, 'seconds_since_action': None, 'tool': None, 'held': True}]}


async def test_hold_preserves_work_already_running_for_the_owner(phone):
    router, backend = phone
    router.access.update(owner_session='slow')
    async with asyncio.TaskGroup() as tasks:
        owner = tasks.create_task(router.call('slow', 'press_button', PRESS, owner_authorized=True))
        await asyncio.wait_for(backend.entered.wait(), 1)
        await router.hold(DEVICE, True)
        assert not owner.done() and not backend.cleaned.is_set()
        backend.release.set()
    assert owner.result()['status'] == 'success'


async def test_reads_and_live_view_continue_while_held(phone):
    router, backend = phone
    async def live(device):
        return LiveView()
    backend.live = live
    first = await router.live('agent', DEVICE)
    await router.hold(DEVICE, True)
    assert not first.closed
    assert (await router.call('agent', 'take_screenshot', {'device_id': DEVICE}))['status'] == 'success'
    assert not (await router.live('another', DEVICE)).closed


async def test_resume_lets_the_agent_continue(phone):
    router, _ = phone
    await router.hold(DEVICE, True)
    assert await router.hold(DEVICE, False) == {'device_id': DEVICE, 'held': False}
    assert (await router.call('agent', 'press_button', PRESS))['status'] == 'success'


@pytest.mark.parametrize('instance,owner,held_after_registration', [
    ('owned-runtime', 'owning-workspace', True),
    ('owned-runtime', 'new-workspace', False),
    ('relaunched-runtime', 'owning-workspace', False),
])
async def test_supervised_hold_is_restored_only_in_its_runtime_and_owner(tmp_path, monkeypatch, instance, owner,
                                                                  held_after_registration):
    monkeypatch.setenv('TAPMESH_BUNDLED_RUNTIME', '1')
    monkeypatch.setenv('TAPMESH_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('TAPMESH_INSTANCE_ID', 'owned-runtime')
    monkeypatch.setattr('agent.backends.MacBackend', lambda state: PhysicalFixture())

    def client_for_agent():
        app = create_app(token=TOKEN, approval_token=LOCAL)
        return app.state.router, httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://localhost', headers=OWNER)

    first, client = client_for_agent()
    async with client:
        assert (await client.post('/v1/settings', json={'owner_session': 'owning-workspace', 'agent_devices': [DEVICE]})).status_code == 200
        assert (await client.post('/v1/hold', json={'device_id': DEVICE, 'held': True})).json()['held']
    await first.close()
    receipt = tmp_path / 'device-holds.json'
    raw = receipt.read_text()
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o600
    assert 'owning-workspace' not in raw and LOCAL not in raw
    assert json.loads(raw)['device_ids'] == [DEVICE]

    monkeypatch.setenv('TAPMESH_INSTANCE_ID', instance)
    restarted, client = client_for_agent()
    try:
        assert restarted.access.owner_session is None
        before = await restarted.call('agent', 'press_button', PRESS)
        if instance == 'owned-runtime':
            assert before['error']['code'] == 'device_held'
            assert before['error']['side_effect_state'] == 'not_sent'
            assert restarted.backend.calls == []
        else:
            assert before['status'] == 'success'
        async with client:
            assert (await client.post('/v1/settings', json={'owner_session': owner})).status_code == 200
            after = await restarted.call('agent', 'press_button', PRESS)
            if held_after_registration:
                assert after['error']['code'] == 'device_held'
                assert (await restarted.call(owner, 'press_button', PRESS, owner_authorized=True))['status'] == 'success'
                assert (await client.post('/v1/hold', json={'device_id': DEVICE, 'held': False})).json()['held'] is False
                assert (await restarted.call('agent', 'press_button', PRESS))['status'] == 'success'
            else:
                assert after['status'] == 'success'
            assert not receipt.exists()
    finally:
        await restarted.close()


async def test_hold_validating_during_owner_change_does_not_install_an_old_hold(phone):
    router, backend = phone
    discover = backend.discover
    async def delayed_discovery(include_all=False):
        backend.entered.set()
        await backend.release.wait()
        return await discover(include_all)
    backend.discover = delayed_discovery
    hold = asyncio.create_task(router.hold(DEVICE, True))
    try:
        await asyncio.wait_for(backend.entered.wait(), 1)
        router.access.update(owner_session='app2')
        router.release_holds()
        backend.release.set()
        with pytest.raises(BridgeError) as changed:
            await hold
        assert changed.value.code == 'ownership_changed'
    finally:
        if not hold.done():
            hold.cancel()
            await asyncio.gather(hold, return_exceptions=True)
    assert router.activity() == {'devices': []}


async def test_hold_during_preflight_blocks_approval_creation(phone):
    router, backend = phone
    router.access.update(agent_devices=[])
    preflight = router._preflight
    async def delayed_preflight(tool, args, device):
        backend.entered.set()
        await backend.release.wait()
        return await preflight(tool, args, device)
    router._preflight = delayed_preflight
    async with asyncio.TaskGroup() as tasks:
        call = tasks.create_task(router.call('agent', 'press_button', PRESS))
        await asyncio.wait_for(backend.entered.wait(), 1)
        await router.hold(DEVICE, True)
        backend.release.set()
    assert call.result()['error']['code'] == 'device_held'
    assert router.approvals.pending() == []
    assert backend.calls == []


async def test_activity_reports_agent_work_only_and_expires(phone, monkeypatch):
    from agent import core

    router, _ = phone
    await router.call('app', 'press_button', PRESS, owner_authorized=True)
    assert router.activity() == {'devices': []}
    await router.call('agent', 'press_button', PRESS)
    assert router.activity() == {'devices': [{'device_id': DEVICE, 'active': False, 'seconds_since_action': pytest.approx(0, abs=1), 'tool': 'press_button', 'held': False}]}
    monkeypatch.setattr(core, 'time', SimpleNamespace(monotonic=lambda: time.monotonic() + 61))
    assert router.activity() == {'devices': []}


async def test_hold_batch_stops_after_cancelled_step_even_when_allowed_to_continue(phone):
    router, backend = phone
    arguments = {'device_id': DEVICE, 'stop_on_error': False, 'commands': [
        {'tool': 'press_button', 'arguments': {'button': 'home'}},
        {'tool': 'press_button', 'arguments': {'button': 'home'}},
    ]}
    async with asyncio.TaskGroup() as tasks:
        batch = tasks.create_task(router.call('slow', 'batch_commands', arguments))
        await asyncio.wait_for(backend.entered.wait(), 1)
        await router.hold(DEVICE, True)
    assert batch.result()['status'] == 'partial'
    assert batch.result()['data']['results'][0]['error']['code'] == 'cancelled'
    assert backend.cleaned.is_set()
    assert len(backend.calls) == 1


async def test_new_owner_session_clears_hold_and_routes_enforce_owner_credential(phone, tmp_path):
    from server.app import create_app as create_server

    router, _ = phone
    agent = create_app(router, token=TOKEN, approval_token=LOCAL)
    payload = {'device_id': DEVICE, 'held': True}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=agent), base_url='http://localhost', headers=BEARER) as upstream:
        server = create_server(state_dir=tmp_path / 'server', token=TOKEN, agent_client=upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url='http://localhost') as client:
            assert (await client.get('/api/activity')).status_code == 401
            assert (await client.post('/api/hold', json=payload)).status_code == 401
            for api, prefix in ((upstream, '/v1'), (client, '/api')):
                assert (await api.post(prefix + '/hold', headers=BEARER, json=payload)).status_code == 403
                wrong = {**BEARER, 'X-DeviceBridge-Local-Authorization': 'Bearer ' + 'c' * 64}
                assert (await api.post(prefix + '/hold', headers=wrong, json=payload)).status_code == 403
                assert (await api.post(prefix + '/hold', headers=OWNER, json={**payload, 'held': 1})).status_code == 400
                assert (await api.post(prefix + '/hold', headers=OWNER, json={**payload, 'extra': True})).status_code == 400
                missing = await api.post(prefix + '/hold', headers=OWNER, json={**payload, 'device_id': 'ios:missing'})
                assert missing.json()['error']['code'] == 'device_not_found'
            assert (await client.post('/api/hold', headers=OWNER, json=payload)).json() == payload
            activity = await client.get('/api/activity', headers=BEARER)
            assert activity.status_code == 200
            assert activity.json()['devices'][0]['held']
            assert (await client.post('/api/settings', headers=OWNER, json={'owner_session': 'app'})).status_code == 200
            assert (await client.get('/api/activity', headers=BEARER)).json()['devices'][0]['held']
            assert (await client.post('/api/settings', headers=OWNER, json={'owner_session': 'app2'})).status_code == 200
            assert (await client.get('/api/activity', headers=BEARER)).json() == {'devices': []}
        assert (await router.call('agent', 'press_button', PRESS))['status'] == 'success'
