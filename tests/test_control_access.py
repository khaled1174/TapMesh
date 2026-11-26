import httpx
import pytest

from agent.app import create_app
from agent.core import Router
from agent.safety import BridgeError, ControlAccess
from Contracts.catalog import CATALOG
from test_fast_control import LOCAL, TOKEN
from test_router import DeviceFixture


PHYSICAL = {'device_id': 'ios:phone', 'backend': 'physical'}
SIMULATOR = {'device_id': 'sim:fixture', 'backend': 'simulator'}
PRESS = {'device_id': PHYSICAL['device_id'], 'button': 'home'}
BEARER = {'Authorization': 'Bearer ' + TOKEN}
OWNER = {**BEARER, 'X-DeviceBridge-Local-Authorization': 'Bearer ' + LOCAL}


class PhysicalFixture(DeviceFixture):
    epoch = 1

    async def discover(self, include_all=False):
        return [{**(await super().discover(include_all))[0], **PHYSICAL, 'connection_epoch': self.epoch}]


def test_owner_and_simulator_run_at_once_but_agent_on_phone_needs_grant():
    access = ControlAccess()
    access.update(owner_session='app')
    assert access.immediate('app', PHYSICAL)
    assert access.immediate('mcp:x', SIMULATOR)
    assert not access.immediate('mcp:x', PHYSICAL)
    assert access.needs_device_grant('mcp:x', PHYSICAL)
    access.allow_device('ios:phone')
    assert access.immediate('mcp:x', PHYSICAL)
    assert not access.needs_device_grant('mcp:x', PHYSICAL)


def test_fast_control_off_uses_exact_approvals():
    access = ControlAccess(fast_control=False)
    access.update(owner_session='app')
    assert not access.immediate('app', PHYSICAL)
    assert not access.immediate('mcp:x', SIMULATOR)
    assert not access.needs_device_grant('mcp:x', PHYSICAL)


def test_settings_persist_and_owner_session_does_not(tmp_path):
    path = tmp_path / 'control-settings.json'
    access = ControlAccess(path)
    assert access.snapshot() == {'fast_control': True, 'agent_devices': []}
    access.update(fast_control=False, owner_session='app')
    access.allow_device('ios:phone')
    assert path.stat().st_mode & 0o777 == 0o600
    assert 'app' not in path.read_text()
    restored = ControlAccess(path)
    assert restored.snapshot() == {'fast_control': False, 'agent_devices': ['ios:phone']}
    assert restored.owner_session is None


@pytest.mark.parametrize('content', ['{', '[]', '{"fast_control":"yes","agent_devices":[]}',
                                     '{"fast_control":true,"agent_devices":["sim:fixture"]}',
                                     '{"fast_control":true}', '{"fast_control":true,"agent_devices":null}'])
def test_corrupt_settings_fail_closed(tmp_path, content):
    path = tmp_path / 'control-settings.json'
    path.write_text(content)
    assert ControlAccess(path).snapshot() == {'fast_control': False, 'agent_devices': []}


def test_unreadable_settings_fail_closed(tmp_path):
    path = tmp_path / 'control-settings.json'
    path.mkdir()
    assert ControlAccess(path).snapshot() == {'fast_control': False, 'agent_devices': []}


@pytest.mark.parametrize('settings', [{'fast_control': 1}, {'fast_control': None}, {'agent_devices': 'ios:phone'},
                                      {'agent_devices': ['sim:fixture']}, {'agent_devices': ['ios:' + 'x' * 253]},
                                      {'agent_devices': ['ios:phone'] * 65}, {'owner_session': ''},
                                      {'owner_session': 'x' * 129}])
def test_update_rejects_invalid_settings_atomically(settings):
    access = ControlAccess()
    with pytest.raises(BridgeError, match='.') as rejected:
        access.update(**settings)
    assert rejected.value.code == 'invalid_arguments'
    assert access.snapshot() == {'fast_control': True, 'agent_devices': []}
    assert access.owner_session is None


def test_forced_off_cannot_be_turned_on():
    access = ControlAccess(forced_off=True)
    assert not access.fast_control
    with pytest.raises(BridgeError, match='Fast control is turned off'):
        access.update(fast_control=True)


def test_persistence_failure_keeps_the_previous_access(tmp_path):
    access = ControlAccess(tmp_path / 'missing' / 'control-settings.json', fast_control=False)
    with pytest.raises(BridgeError) as failed:
        access.update(fast_control=True, agent_devices=['ios:phone'], owner_session='app')
    assert failed.value.code == 'backend_failure'
    assert access.snapshot() == {'fast_control': False, 'agent_devices': []}
    assert access.owner_session is None


async def test_phone_grant_is_deduplicated_and_survives_reconnect_and_restart(tmp_path):
    path = tmp_path / 'control-settings.json'
    backend = PhysicalFixture()
    router = Router(backend, state_dir=tmp_path, access=ControlAccess(path))
    router.access.update(owner_session='app')
    app = create_app(router, token=TOKEN, approval_token=LOCAL)
    try:
        for index in range(100):
            result = await router.call(f'mcp:{index % 2}', 'press_button', PRESS)
            assert result['status'] == 'error'
            assert result['error']['code'] == 'approval_required'
            assert result['error']['side_effect_state'] == 'not_sent'
            aid = result['error']['details']['approval_id']
            assert result['error']['details'] == {'approval_id': aid, 'policy': 'A', 'scope': 'device'}
        assert [record['id'] for record in router.approvals.pending()] == [aid]
        assert backend.calls == []
        backend.epoch = 2
        reconnect = await router.call('mcp:x', 'press_button', PRESS)
        assert reconnect['error']['details']['approval_id'] == aid
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver', headers=OWNER) as client:
            route = f'/v1/approvals/{aid}'
            assert (await client.post(route, headers={'X-DeviceBridge-Local-Authorization': ''}, json={'decision': 'allow'})).status_code == 403
            response = await client.post(route, json={'decision': 'allow'})
            assert response.status_code == 200 and response.json()['approval']['decision'] == 'allow'
            assert router.approvals.pending() == []
            for session in ('mcp:x', 'mcp:y'):
                assert (await router.call(session, 'press_button', PRESS))['status'] == 'success'
            assert ControlAccess(path).immediate('mcp:new', PHYSICAL)
            assert (await client.post('/v1/settings', json={'agent_devices': []})).status_code == 200
            denied_again = await router.call('mcp:x', 'press_button', PRESS)
            assert denied_again['error']['details']['policy'] == 'A'
            assert denied_again['error']['details']['approval_id'] != aid
        assert [tool for tool, _ in backend.calls] == ['press_button', 'press_button']
    finally:
        await router.close()


async def test_real_owner_on_phone_has_immediate_access_without_a_card(tmp_path):
    backend = PhysicalFixture()
    router = Router(backend, state_dir=tmp_path, fast_control=True)
    router.access.update(owner_session='app')
    try:
        result = await router.call('app', 'press_button', PRESS, owner_authorized=True)
        assert result['status'] == 'success'
        assert router.approvals.pending() == []
    finally:
        await router.close()


async def test_denied_device_grant_does_not_prompt_again_on_retry(tmp_path):
    router = Router(PhysicalFixture(), state_dir=tmp_path, fast_control=True)
    try:
        first = await router.call('mcp:x', 'press_button', PRESS)
        aid = first['error']['details']['approval_id']
        router.approvals.decide(aid, 'deny')
        for session in ('mcp:x', 'mcp:y'):
            denied = await router.call(session, 'press_button', PRESS)
            assert denied['error']['code'] == 'approval_denied'
            assert denied['error']['details']['approval_id'] == aid
        assert router.approvals.pending() == []
    finally:
        await router.close()


async def test_http_owner_session_claim_requires_the_private_credential(tmp_path):
    from server.app import create_app as create_server

    backend = PhysicalFixture()
    router = Router(backend, state_dir=tmp_path / 'agent', fast_control=True)
    agent = create_app(router, token=TOKEN, approval_token=LOCAL)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=agent), base_url='http://localhost', headers=BEARER) as upstream:
            server = create_server(state_dir=tmp_path / 'server', token=TOKEN, agent_client=upstream)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url='http://localhost', headers=BEARER) as client:
                settings = {'owner_session': 'app', 'fast_control': True}
                assert (await client.post('/api/settings', headers=OWNER, json=settings)).status_code == 200
                batch = {'device_id': 'ios:phone', 'commands': [{'tool': 'press_button', 'arguments': {'button': 'home'}}]}
                payload = {'session_id': 'app', 'tool': 'batch_commands', 'arguments': batch}
                for api, route in ((client, '/api/call'), (upstream, '/v1/call')):
                    refused = await api.post(route, json=payload)
                    assert refused.json()['error']['code'] == 'local_authorization_required'
                assert backend.calls == []
                assert (await client.post('/api/call', headers=OWNER, json=payload)).json()['status'] == 'success'
        assert [tool for tool, _ in backend.calls] == ['press_button']
    finally:
        await router.close()


async def test_sensitive_actions_still_ask_on_an_allowed_phone(tmp_path):
    backend = PhysicalFixture()
    router = Router(backend, state_dir=tmp_path, fast_control=True)
    router.access.allow_device('ios:phone')
    try:
        result = await router.call('mcp:x', 'save_screenshot', {'device_id': 'ios:phone'})
        assert result['error']['details']['policy'] == 'S'
        assert backend.calls == []
    finally:
        await router.close()


def test_set_location_is_sensitive():
    assert CATALOG['set_location']['policy'] == 'S'
