import json

import httpx
import pytest

from server import stdio
from tapmesh_runtime import Profile, stdio_bridge


@pytest.fixture
def install_transport(monkeypatch):
    client_type = httpx.AsyncClient

    def install(handler):
        transport = httpx.MockTransport(handler)
        monkeypatch.setattr(stdio.httpx, 'AsyncClient', lambda **options: client_type(transport=transport, **options))

    return install


async def test_bridge_reconnects_after_the_service_moves_to_a_new_port(tmp_path, monkeypatch, install_transport):
    profile = Profile(tmp_path / 'Local')
    profile.prepare()
    profile.configure(50000, 50001, 'first-instance')
    monkeypatch.setenv('TAPMESH_CONNECTION_FILE', str(profile.root / 'connection.json'))
    sent = []

    def respond(request):
        if request.url.port == 50001:
            profile.configure(50002, 50003, 'new-instance')
            (profile.root / 'server.token').write_text('a' * 64)
            raise httpx.ConnectError('Previous service stopped', request=request)
        sent.append((request.url.port, request.headers['authorization'], json.loads(request.content)))
        return httpx.Response(200, json={'request_id': 'request', 'status': 'success'})

    install_transport(respond)
    result = await stdio.HTTPBridge(stdio.connect).call('mcp:client', 'tap', {'x': 12, 'y': 15})

    assert result['status'] == 'success'
    assert sent == [(50003, 'Bearer ' + 'a' * 64, {'session_id': 'mcp:client', 'tool': 'tap', 'arguments': {'x': 12, 'y': 15}})]


async def test_bridge_reports_tapmesh_not_running(install_transport):
    attempts = []

    def respond(request):
        attempts.append(request)
        raise httpx.ConnectError('Service stopped', request=request)

    install_transport(respond)
    result = await stdio.HTTPBridge(lambda: ('http://127.0.0.1:50001', 'a' * 64)).call('client', 'list_devices', {})

    assert len(attempts) == 2
    assert result['request_id']
    assert result['status'] == 'error'
    assert result['error'] == {
        'code': 'backend_unavailable',
        'message': "TapMesh isn't running. Open TapMesh and try again.",
        'retryable': True,
        'side_effect_state': 'not_sent',
    }


async def test_bridge_never_retries_after_send(install_transport):
    attempts = []

    def respond(request):
        attempts.append(request)
        raise httpx.ReadError('Connection interrupted after sending', request=request)

    install_transport(respond)
    with pytest.raises(httpx.ReadError):
        await stdio.HTTPBridge(lambda: ('http://127.0.0.1:50001', 'a' * 64)).call('client', 'tap', {})
    assert len(attempts) == 1


async def test_bridge_handles_a_missing_connection_file(tmp_path, monkeypatch):
    monkeypatch.setenv('TAPMESH_CONNECTION_FILE', str(tmp_path / 'connection.json'))
    result = await stdio.HTTPBridge(stdio.connect).call('client', 'list_devices', {})
    assert result['error']['code'] == 'backend_unavailable'


@pytest.mark.parametrize('url', ['http://example.invalid:50001', 'http://127.0.0.1:99999'])
async def test_invalid_profile_url_never_reaches_transport(tmp_path, monkeypatch, install_transport, url):
    profile = Profile(tmp_path / 'Local')
    profile.prepare()
    connection_file = profile.root / 'connection.json'
    connection_file.write_text(json.dumps({'api_url': url}))
    monkeypatch.setenv('TAPMESH_CONNECTION_FILE', str(connection_file))
    install_transport(lambda _: pytest.fail('An invalid endpoint reached the transport'))

    result = await stdio.HTTPBridge(stdio.connect).call('client', 'list_devices', {})
    assert result['error']['code'] == 'backend_unavailable'


def test_legacy_stdio_credentials_remain_supported(tmp_path, monkeypatch):
    monkeypatch.delenv('TAPMESH_CONNECTION_FILE', raising=False)
    token_file = tmp_path / 'legacy.token'
    token_file.write_text('a' * 64)
    token_file.chmod(0o600)
    monkeypatch.setenv('TAPMESH_SERVER_URL', 'http://127.0.0.1:48793')
    monkeypatch.setenv('TAPMESH_SERVER_TOKEN_FILE', str(token_file))

    assert stdio.connect() == ('http://127.0.0.1:48793', 'a' * 64)


def test_launcher_passes_the_profile_connection_file(tmp_path, monkeypatch):
    profile = Profile(tmp_path / 'Local')
    profile.prepare()
    profile.configure(50000, 50001, 'instance')
    monkeypatch.setattr('tapmesh_runtime._ready', lambda configuration: True)
    monkeypatch.setenv('TAPMESH_CONNECTION_FILE', str(tmp_path / 'other-profile.json'))
    discovered = []
    monkeypatch.setattr(stdio, 'main', lambda: discovered.append(stdio.connect()))

    stdio_bridge(profile, tmp_path / 'TapMesh.app')

    assert discovered == [('http://127.0.0.1:50001', (profile.root / 'server.token').read_text().strip())]
