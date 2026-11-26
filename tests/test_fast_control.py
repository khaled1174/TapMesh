"""Fast control: effective policy C runs at once; S, D, H and U keep approvals."""
import asyncio
import socket

import httpx
import pytest
from starlette.testclient import TestClient

from agent.core import Router
from agent.safety import Approvals, BridgeError
from test_router import DeviceFixture


TOKEN = 'a' * 64
LOCAL = 'b' * 64


class SimulatorFixture(DeviceFixture):
    async def invoke(self, tool, device, args, context):
        result = await super().invoke(tool, device, args, context)
        if tool == 'start_simulator': return {**result, 'device_id': 'sim:fixture'}
        return result


def make_router(tmp_path, fast):
    backend = SimulatorFixture()
    return Router(backend, state_dir=tmp_path, fast_control=fast), backend


def tap_args(router, session='agent'):
    observation = router.observations.issue(session, 'sim:fixture', 1, {'width': 100, 'height': 200}, None, 'capture')
    return {'device_id': 'sim:fixture', 'target': {'x': 10, 'y': 10, 'coordinate_space': 'device_points', 'observation_id': observation['observation_id']}}


@pytest.mark.parametrize('tool,arguments', [
    ('type_text', {'device_id': 'sim:fixture', 'text': 'hello'}),
    ('press_button', {'device_id': 'sim:fixture', 'button': 'home'}),
    ('open_url', {'device_id': 'sim:fixture', 'url': 'https://example.com'}),
    ('launch_app', {'device_id': 'sim:fixture', 'bundle_id': 'io.tapmesh.fixture'}),
    ('batch_commands', {'device_id': 'sim:fixture', 'commands': [
        {'tool': 'open_url', 'arguments': {'url': 'https://example.com'}},
        {'tool': 'press_button', 'arguments': {'button': 'home'}}]}),
])
async def test_control_runs_at_once_without_approval_record(tmp_path, tool, arguments):
    router, backend = make_router(tmp_path, True)
    try:
        result = await router.call('agent', tool, arguments)
        assert result['status'] == 'success', result
        assert backend.calls
        assert router.approvals.records == {} and router.approvals.pending() == []
    finally: await router.close()


async def test_tap_runs_at_once_in_fast_mode(tmp_path):
    router, backend = make_router(tmp_path, True)
    try:
        assert (await router.call('agent', 'tap', tap_args(router)))['status'] == 'success'
        assert [call[0] for call in backend.calls] == ['tap']
        assert router.approvals.records == {}
    finally: await router.close()


async def test_start_simulator_follows_the_same_rule(tmp_path):
    router, backend = make_router(tmp_path, True)
    try:
        result = await router.call('agent', 'start_simulator', {'simulator_id': 'fixture'})
        assert result['status'] == 'success', result
        assert router.approvals.records == {}
    finally: await router.close()
    router, backend = make_router(tmp_path / 'off', False)
    try:
        result = await router.call('agent', 'start_simulator', {'simulator_id': 'fixture'})
        assert result['error']['code'] == 'approval_required'
        assert backend.calls == []
    finally: await router.close()


async def test_control_waits_for_approval_when_fast_mode_is_off(tmp_path):
    router, backend = make_router(tmp_path, False)
    try:
        args = tap_args(router)
        pending = await router.call('agent', 'tap', args)
        assert pending['error']['code'] == 'approval_required'
        assert pending['error']['side_effect_state'] == 'not_sent'
        assert pending['error']['details']['policy'] == 'C'
        assert backend.calls == []
        router.approvals.decide(pending['error']['details']['approval_id'], 'allow')
        assert (await router.call('agent', 'tap', args))['status'] == 'success'
    finally: await router.close()


@pytest.mark.parametrize('tool,arguments,policy', [
    ('type_text', {'device_id': 'sim:fixture', 'text': 'secret', 'input_method': 'clipboard'}, 'S'),
    ('open_url', {'device_id': 'sim:fixture', 'url': 'myapp://reset'}, 'S'),
    ('launch_app', {'device_id': 'sim:fixture', 'bundle_id': 'io.tapmesh.fixture', 'arguments': ['-reset']}, 'S'),
    ('set_clipboard', {'device_id': 'sim:fixture', 'text': 'secret'}, 'S'),
    ('uninstall_app', {'device_id': 'sim:fixture', 'bundle_id': 'io.tapmesh.fixture'}, 'D'),
    ('batch_commands', {'device_id': 'sim:fixture', 'commands': [
        {'tool': 'press_button', 'arguments': {'button': 'home'}},
        {'tool': 'open_url', 'arguments': {'url': 'myapp://reset'}}]}, 'S'),
])
async def test_sensitive_and_destructive_actions_still_ask_in_fast_mode(tmp_path, tool, arguments, policy):
    router, backend = make_router(tmp_path, True)
    try:
        result = await router.call('agent', tool, arguments)
        assert result['error']['code'] == 'approval_required', result
        assert result['error']['details']['policy'] == policy
        assert result['error']['side_effect_state'] == 'not_sent'
        assert backend.calls == []
    finally: await router.close()


async def test_human_handoff_still_requires_the_owner_in_fast_mode(tmp_path):
    router, backend = make_router(tmp_path, True)
    try:
        result = await router.call('agent', 'request_human_action', {'device_id': 'sim:fixture', 'reason': 'Sign-in', 'instructions': 'Sign in'})
        assert result['error']['code'] == 'protected_action_requires_human'
        assert result['error']['details']['policy'] == 'U'
    finally: await router.close()


def test_host_execution_policy_is_not_covered_by_fast_mode():
    approvals = Approvals()
    approvals.require('agent', 'sim:fixture', 1, 'tap', {}, policy='C', immediate=True)
    for policy in ('S', 'D', 'H', 'U'):
        with pytest.raises(BridgeError) as asked: approvals.require('agent', 'sim:fixture', 1, 'build_and_run', {'n': policy}, policy=policy, immediate=True)
        assert asked.value.code in {'approval_required', 'protected_action_requires_human'}


def test_turning_fast_mode_on_retires_waiting_control_requests_only(tmp_path):
    from agent.app import create_app
    router, _ = make_router(tmp_path, False)
    approvals = router.approvals
    with pytest.raises(BridgeError): approvals.require('agent', 'sim:fixture', 1, 'tap', {'x': 1}, policy='C')
    with pytest.raises(BridgeError): approvals.require('agent', 'sim:fixture', 1, 'set_clipboard', {'text': 'x'}, policy='S')
    assert {record['policy'] for record in approvals.pending()} == {'C', 'S'}
    with TestClient(create_app(router, token=TOKEN, approval_token=LOCAL)) as client:
        headers = {'Authorization': 'Bearer ' + TOKEN, 'X-DeviceBridge-Local-Authorization': 'Bearer ' + LOCAL}
        assert client.post('/v1/settings', headers=headers, json={'fast_control': True}).json() == {'fast_control': True, 'agent_devices': []}
        assert [record['policy'] for record in approvals.pending()] == ['S']
        approvals.require('agent', 'sim:fixture', 1, 'tap', {'x': 1}, policy='C', consume=True, immediate=router.access.fast_control)
        assert client.post('/v1/settings', headers=headers, json={'fast_control': False}).status_code == 200
        with pytest.raises(BridgeError) as asked: approvals.require('agent', 'sim:fixture', 1, 'tap', {'x': 1}, policy='C', immediate=router.access.fast_control)
        assert asked.value.code == 'approval_required'
        assert client.post('/v1/settings', headers=headers, json={'fast_control': 'yes'}).status_code == 400


def test_service_default_is_fast_unless_started_with_it_off(monkeypatch):
    from agent.app import fast_control_default
    monkeypatch.delenv('TAPMESH_FAST_CONTROL', raising=False)
    assert fast_control_default() is True
    for value in ('0', 'false', 'OFF', 'no'):
        monkeypatch.setenv('TAPMESH_FAST_CONTROL', value)
        assert fast_control_default() is False
    monkeypatch.setenv('TAPMESH_FAST_CONTROL', '1')
    assert fast_control_default() is True


def test_agent_setting_requires_the_approvals_credential(tmp_path):
    from agent.app import create_app
    router, backend = make_router(tmp_path, True)
    app = create_app(router, token=TOKEN, approval_token=LOCAL)
    with TestClient(app) as client:
        bearer = {'Authorization': 'Bearer ' + TOKEN}
        owner = {**bearer, 'X-DeviceBridge-Local-Authorization': 'Bearer ' + LOCAL}
        assert client.get('/v1/settings').status_code == 401
        assert client.post('/v1/settings', json={'fast_control': False}).status_code == 401
        assert client.get('/v1/settings', headers=bearer).status_code == 403
        assert client.post('/v1/settings', headers=bearer, json={'fast_control': False}).status_code == 403
        wrong = {**bearer, 'X-DeviceBridge-Local-Authorization': 'Bearer ' + 'c' * 64}
        assert client.post('/v1/settings', headers=wrong, json={'fast_control': False}).status_code == 403
        assert router.access.fast_control is True
        assert client.get('/v1/settings', headers=owner).json() == {'fast_control': True, 'agent_devices': []}
        assert client.post('/v1/settings', headers=owner, json={'fast_control': 'no'}).status_code == 400
        assert client.post('/v1/settings', headers=owner, json={'fast_control': False, 'extra': 1}).status_code == 400
        assert client.post('/v1/settings', headers=owner, json={}).status_code == 400
        assert client.post('/v1/settings', headers=owner, json={'fast_control': False}).json() == {'fast_control': False, 'agent_devices': []}
        assert router.access.fast_control is False


def test_server_setting_requires_owner_header_and_relays_it(tmp_path):
    from server.app import create_app
    seen = []
    async def agent(request):
        seen.append((request.method, request.url.path, request.headers.get('x-devicebridge-local-authorization'), request.content))
        if request.headers.get('x-devicebridge-local-authorization') != 'Bearer ' + LOCAL:
            return httpx.Response(403, json={'error': 'local_authorization_required'})
        return httpx.Response(200, json={'fast_control': False})
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(agent), base_url='http://127.0.0.1:48792')
    app = create_app(state_dir=tmp_path, token=TOKEN, agent_client=upstream)
    with TestClient(app) as client:
        bearer = {'Authorization': 'Bearer ' + TOKEN}
        assert client.post('/api/settings', json={'fast_control': False}).status_code == 401
        assert client.post('/api/settings', headers=bearer, json={'fast_control': False}).status_code == 403
        assert seen == []
        wrong = {**bearer, 'X-DeviceBridge-Local-Authorization': 'Bearer ' + 'c' * 64}
        assert client.post('/api/settings', headers=wrong, json={'fast_control': False}).status_code == 403
        owner = {**bearer, 'X-DeviceBridge-Local-Authorization': 'Bearer ' + LOCAL}
        assert client.post('/api/settings', headers=owner, json={'fast_control': 1}).status_code == 400
        response = client.post('/api/settings', headers=owner, json={'fast_control': False})
        assert response.status_code == 200 and response.json() == {'fast_control': False}
        assert client.get('/api/settings', headers=owner).status_code == 200
        assert [entry[:3] for entry in seen] == [('POST', '/v1/settings', 'Bearer ' + 'c' * 64), ('POST', '/v1/settings', 'Bearer ' + LOCAL), ('GET', '/v1/settings', 'Bearer ' + LOCAL)]


async def test_http_and_mcp_clients_follow_the_owner_setting_end_to_end(tmp_path):
    """The app's setting reaches the agent through the server; MCP uses the same path."""
    import uvicorn
    from agent.app import create_app as agent_app
    from server.app import create_app as server_app
    router, backend = make_router(tmp_path / 'agent', True)
    sockets = []; servers = []; tasks = []
    def listen():
        sock = socket.socket(); sock.bind(('127.0.0.1', 0)); sock.listen(); sockets.append(sock)
        return 'http://127.0.0.1:' + str(sock.getsockname()[1])
    agent_url = listen(); nas_url = listen()
    upstream = httpx.AsyncClient(base_url=agent_url, headers={'Authorization': 'Bearer ' + TOKEN})
    apps = [agent_app(router, token=TOKEN, approval_token=LOCAL), server_app(state_dir=tmp_path / 'server', token='d' * 64, agent_client=upstream)]
    try:
        for app, sock in zip(apps, sockets):
            server = uvicorn.Server(uvicorn.Config(app, log_level='critical', access_log=False)); servers.append(server)
            tasks.append(asyncio.create_task(server.serve(sockets=[sock])))
        async with asyncio.timeout(5):
            while not all(s.started for s in servers): await asyncio.sleep(.01)
        async with httpx.AsyncClient(base_url=nas_url, headers={'Authorization': 'Bearer ' + 'd' * 64}, timeout=30) as client:
            owner = {'X-DeviceBridge-Local-Authorization': 'Bearer ' + LOCAL}
            payload = {'session_id': 'native', 'tool': 'press_button', 'arguments': {'device_id': 'sim:fixture', 'button': 'home'}}
            assert (await client.post('/api/call', json=payload)).json()['status'] == 'success'
            mcp = {'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': '2025-11-25'}
            initialized = await client.post('/mcp', headers=mcp, json={'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-11-25', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}}})
            mcp['Mcp-Session-Id'] = initialized.headers['mcp-session-id']
            await client.post('/mcp', headers=mcp, json={'jsonrpc': '2.0', 'method': 'notifications/initialized'})
            async def mcp_press(request_id):
                response = await client.post('/mcp', headers=mcp, json={'jsonrpc': '2.0', 'id': request_id, 'method': 'tools/call', 'params': {'name': 'press_button', 'arguments': {'device_id': 'sim:fixture', 'button': 'home'}}})
                return response.json()['result']['structuredContent']
            assert (await mcp_press(2))['status'] == 'success'
            assert router.approvals.records == {}
            assert (await client.post('/api/settings', headers=owner, json={'fast_control': False})).json() == {'fast_control': False, 'agent_devices': []}
            assert (await mcp_press(3))['error']['code'] == 'approval_required'
            assert (await client.post('/api/call', json=payload)).json()['error']['code'] == 'approval_required'
            assert len(router.approvals.pending()) == 2
            assert (await client.post('/api/settings', headers=owner, json={'fast_control': True})).json() == {'fast_control': True, 'agent_devices': []}
            assert router.approvals.pending() == []
            assert (await mcp_press(4))['status'] == 'success'
            assert [call[0] for call in backend.calls] == ['press_button'] * 3
    finally:
        for server in servers: server.should_exit = True
        await asyncio.gather(*tasks, return_exceptions=True)
        await upstream.aclose()
        for sock in sockets: sock.close()


async def test_same_picture_serves_several_inputs_in_a_row(tmp_path):
    # A person or agent can tap twice on one frame without waiting for a new screenshot.
    router, backend = make_router(tmp_path, True)
    try:
        args = tap_args(router)
        assert (await router.call('agent', 'tap', args))['status'] == 'success'
        assert (await router.call('agent', 'tap', args))['status'] == 'success'
        assert [call[0] for call in backend.calls] == ['tap', 'tap']
    finally: await router.close()


async def test_non_input_change_still_needs_a_fresh_picture(tmp_path):
    router, backend = make_router(tmp_path, True)
    try:
        args = tap_args(router)
        assert (await router.call('agent', 'press_button', {'device_id': 'sim:fixture', 'button': 'home'}))['status'] == 'success'
        result = await router.call('agent', 'tap', args)
        assert result['status'] == 'error' and result['error']['code'] == 'stale_observation'
    finally: await router.close()
