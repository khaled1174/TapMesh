import base64
import io
import httpx
import pytest
from PIL import Image
from starlette.testclient import TestClient
import server.app as server_app
from agent.nas_ocr import NASOCR
from agent.safety import BridgeError

SERVER = 's' * 43
AGENT = 'a' * 43


def png():
    output = io.BytesIO()
    Image.new('RGB', (100, 200), 'white').save(output, format='PNG')
    return output.getvalue()


@pytest.fixture
def processor(tmp_path, monkeypatch):
    calls = []
    async def recognize(data, *, logical_size):
        calls.append((data, logical_size))
        return {'source': 'ocr', 'logical_size': logical_size, 'image_size': {'width': 100, 'height': 200},
                'elements': [{'label': 'TapMesh', 'role': 'text', 'value': None, 'source': 'ocr',
                              'bounds': {'x': 5, 'y': 10, 'width': 40, 'height': 12}, 'confidence': .9}]}
    async def health():
        return {'status': 'available', 'engine': 'tesseract', 'languages': ['eng', 'ara']}
    monkeypatch.setattr(server_app, 'recognize_ocr', recognize, raising=False)
    monkeypatch.setattr(server_app, 'ocr_health', health, raising=False)
    agent_client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    app = server_app.create_app(state_dir=tmp_path, token=SERVER, agent_client=agent_client, processor_token=AGENT)
    with TestClient(app) as client:
        yield client, calls


def test_only_agent_credential_can_submit_image_processing(processor):
    client, calls = processor
    body = {'image_base64': base64.b64encode(png()).decode(), 'logical_size': {'width': 100, 'height': 200}}
    assert client.post('/internal/ocr', json=body, headers={'Authorization': 'Bearer ' + SERVER}).status_code == 401
    assert not calls
    response = client.post('/internal/ocr', json=body, headers={'Authorization': 'Bearer ' + AGENT})
    assert response.status_code == 200
    assert response.json()['elements'][0]['label'] == 'TapMesh'
    assert calls == [(png(), {'width': 100, 'height': 200})]
    assert client.get('/internal/ocr/health', headers={'Authorization': 'Bearer ' + AGENT}).json()['status'] == 'available'


def test_processor_rejects_unknown_fields_and_invalid_base64(processor):
    client, calls = processor
    header = {'Authorization': 'Bearer ' + AGENT}
    body = {'image_base64': 'invalid!', 'logical_size': {'width': 100, 'height': 200}}
    assert client.post('/internal/ocr', json=body, headers=header).status_code == 400
    body.update(image_base64=base64.b64encode(png()).decode(), executable='/bin/sh')
    assert client.post('/internal/ocr', json=body, headers=header).status_code == 400
    assert not calls


def test_processor_enforces_streamed_body_bound_without_content_length(processor):
    client, calls = processor
    response = client.post('/internal/ocr', content=(b'x' * (1024 * 1024) for _ in range(13)),
                           headers={'Authorization': 'Bearer ' + AGENT})
    assert response.status_code == 413
    assert not calls


@pytest.mark.parametrize('url', ['https://127.0.0.1', 'http://example.com', 'http://localhost@evil.test',
                                'http://127.0.0.1/internal/ocr', 'file:///tmp/ocr'])
def test_ocr_client_cannot_send_screen_to_external_or_caller_chosen_route(url):
    with pytest.raises(RuntimeError):
        NASOCR(url, AGENT)


@pytest.mark.asyncio
async def test_ocr_client_rejects_out_of_bounds_targets_and_never_creates_ax_roles():
    unsafe = False
    async def respond(request):
        if request.url.path.endswith('/health'):
            return httpx.Response(200, json={'status': 'available'})
        assert request.headers['Authorization'] == 'Bearer ' + AGENT
        return httpx.Response(200, json={'source': 'ocr', 'logical_size': {'width': 100, 'height': 200},
            'elements': [{'label': 'TapMesh', 'role': 'AXButton', 'identifier': 'fabricated',
                          'bounds': {'x': 5, 'y': 10, 'width': 200 if unsafe else 40, 'height': 12}, 'confidence': .9}]})
    class Backend:
        async def invoke(self, tool, device, arguments, context):
            assert tool == 'take_screenshot'
            return {'image': {'data': base64.b64encode(png()).decode()},
                    'logical_size': {'width': 100, 'height': 200}, 'orientation': 'portrait'}
    async with httpx.AsyncClient(base_url='http://127.0.0.1', headers={'Authorization': 'Bearer ' + AGENT},
                                 transport=httpx.MockTransport(respond)) as client:
        port = NASOCR('http://127.0.0.1', AGENT, client=client)
        result = await port.inspect(Backend(), {}, {})
        assert result['elements'][0]['role'] == 'text'
        assert 'identifier' not in result['elements'][0] and 'ref' not in result['elements'][0]
        unsafe = True
        with pytest.raises(BridgeError, match='validated'):
            await port.inspect(Backend(), {}, {})


async def test_private_ocr_revalidates_exact_supplied_frame_without_device_recapture():
    raw = png()
    async def respond(request):
        assert request.headers['Authorization'] == 'Bearer ' + AGENT
        if request.url.path.endswith('/health'):
            return httpx.Response(200, json={'status': 'available'})
        import json
        body = json.loads(request.content)
        assert base64.b64decode(body['image_base64']) == raw
        assert body['logical_size'] == {'width': 100, 'height': 200}
        return httpx.Response(200, json={'source': 'ocr', 'logical_size': body['logical_size'],
            'truncated': True, 'elements': [{'label': 'Name', 'role': 'AXTextField',
              'bounds': {'x': 5, 'y': 10, 'width': 40, 'height': 12}, 'confidence': .99}]})
    async with httpx.AsyncClient(base_url='http://127.0.0.1', headers={'Authorization': 'Bearer ' + AGENT},
                                 transport=httpx.MockTransport(respond)) as client:
        port = NASOCR('http://127.0.0.1', AGENT, client=client)
        result = await port.inspect_frame(raw, {'width': 100, 'height': 200})
        assert result['backend_truncated'] is True
        assert result['elements'][0]['role'] == 'text'


async def test_private_frame_ocr_uses_the_observation_resize_and_pixel_bound():
    raw = io.BytesIO()
    Image.new('RGB', (2064, 2752), 'white').save(raw, format='PNG')
    calls = []
    async def respond(request):
        if request.url.path.endswith('/health'):
            return httpx.Response(200, json={'status': 'available'})
        import json
        body = json.loads(request.content)
        image = Image.open(io.BytesIO(base64.b64decode(body['image_base64'])))
        calls.append(image.size)
        assert image.size == (2048, 2731)
        return httpx.Response(200, json={'source': 'ocr', 'logical_size': body['logical_size'], 'elements': []})
    async with httpx.AsyncClient(base_url='http://127.0.0.1', transport=httpx.MockTransport(respond)) as client:
        port = NASOCR('http://127.0.0.1', AGENT, client=client)
        await port.inspect_frame(raw.getvalue(), {'width': 1032, 'height': 1376})
        oversized = io.BytesIO()
        Image.new('RGB', (3000, 3000)).save(oversized, format='PNG')
        with pytest.raises(BridgeError):
            await port.inspect_frame(oversized.getvalue(), {'width': 1500, 'height': 1500})
        assert calls == [(2048, 2731)]
