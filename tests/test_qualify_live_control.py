import asyncio
import base64
import io
import json
import struct

import httpx
from PIL import Image
import pytest

from scripts import qualify_live_control as trial


DEVICE = 'ios:private-device'


def avcc(*units):
    return b''.join(struct.pack('!I', len(unit)) + unit for unit in units)


def packet(kind, payload):
    return trial.LIVE_HEADER.pack(kind, len(payload)) + payload


def frame(shade, *, key=False):
    header = trial.LIVE_FRAME_HEADER.pack(int(key), 0)
    nal = bytes([(19 if key else 1) << 1, shade])
    return packet(trial.LIVE_FRAME, header + avcc(nal))


def still_capture(orientation, width, height):
    buffer = io.BytesIO()
    Image.new('RGB', (width, height), 'white').save(buffer, format='PNG')
    return {
        'image': {'data': base64.b64encode(buffer.getvalue()).decode()},
        'observation_id': 'private-observation',
        'device_id': DEVICE,
        'orientation': orientation,
        'logical_size': {'width': width, 'height': height},
        'image_size': {'width': width, 'height': height},
    }


class FakeVideo(httpx.AsyncByteStream):
    def __init__(self):
        self.frames = asyncio.Queue()
        self.closed = False

    async def __aiter__(self):
        metadata = json.dumps({'version': 1, 'codec': 'hevc', 'coded_width': 32, 'coded_height': 64}).encode()
        start = packet(trial.LIVE_FORMAT, metadata)
        start += packet(trial.LIVE_PARAMS, avcc(*(bytes([kind << 1, 0]) for kind in (32, 33, 34))))
        start += frame(0, key=True)
        yield start[:3]
        yield start[3:]
        while True:
            yield await self.frames.get()

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize('refuse', [False, True])
async def test_swipe_trial_uses_only_live_changes_for_five_warm_measurements(monkeypatch, refuse):
    monkeypatch.setattr(trial, 'SWIPE_STILL_SECONDS', 0)
    stream = FakeVideo()
    tools, swipes, decoders = [], [], []
    decoded = asyncio.Event()

    class Decoder:
        def __init__(self, *params):
            self.outputs = []
            self.released = 0
            self.closed = False
            decoders.append(self)

        def feed(self, units):
            self.outputs.append((0, units[-1][1]))

        def drain(self):
            outputs, self.outputs = self.outputs, []
            return outputs

        def image(self, shade):
            if swipes:
                decoded.set()
            return Image.new('L', (32, 64), shade)

        def release(self, handle):
            self.released += 1

        def close(self):
            self.closed = True

    async def handle(request):
        if request.method == 'GET':
            return httpx.Response(200, headers={'Content-Type': 'application/vnd.tapmesh.live'}, stream=stream)
        arguments = json.loads(request.content)
        tools.append(arguments['tool'])
        if arguments['tool'] == 'take_screenshot':
            return httpx.Response(200, json={'status': 'success', 'data': still_capture('portrait', 32, 64)})
        assert arguments['tool'] == 'swipe'
        swipes.append(arguments['arguments'])
        if refuse:
            return httpx.Response(200, json={
                'status': 'error',
                'error': {'code': 'approval_required', 'side_effect_state': 'not_sent', 'message': DEVICE},
            })
        decoded.clear()
        await stream.frames.put(frame(255 if len(swipes) % 2 else 0))
        await asyncio.wait_for(decoded.wait(), 1)
        return httpx.Response(200, json={'status': 'success'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url='http://test') as client:
        result = await trial.swipe_latency(client, DEVICE, decoder_factory=Decoder)
    assert stream.closed and len(decoders) == 1 and decoders[0].closed
    encoded = json.dumps(result)
    assert DEVICE not in encoded and 'private-observation' not in encoded
    if refuse:
        assert result['samples'] == 0
        assert tools == ['take_screenshot', 'swipe']
        assert result['attempts'][0]['error_code'] == 'approval_required'
        assert result['attempts'][0]['side_effect_state'] == 'not_sent'
        assert 'changed_live_frame_ms' not in result['attempts'][0]
    else:
        assert result['samples'] == 5
        assert tools == ['take_screenshot', 'swipe'] * 5
        assert decoders[0].released == 6
        assert all(row['changed_live_frame_ms'] <= row['request_ms'] for row in result['attempts'])
        assert [swipe['start']['x'] > swipe['end']['x'] for swipe in swipes] == [True, False, True, False, True]
        assert all(swipe['start']['observation_id'] == 'private-observation' for swipe in swipes)


async def test_landscape_evidence_contains_only_geometry_and_never_sends_tap(monkeypatch):
    answers = iter(('', '', 'fail', 'stale_observation'))
    monkeypatch.setattr('builtins.input', lambda _: next(answers))
    tools = []

    async def handle(request):
        arguments = json.loads(request.content)
        tools.append(arguments['tool'])
        if arguments['tool'] == 'take_screenshot':
            return httpx.Response(200, json={'status': 'success', 'data': still_capture('landscapeLeft', 64, 32)})
        assert arguments['tool'] == 'get_device_info'
        return httpx.Response(200, json={'status': 'success', 'data': {
            'device': DEVICE, 'screen': {'orientation': 'landscape_left', 'logical_size': {'width': 64, 'height': 32}},
        }})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url='http://test') as client:
        result = await trial.landscape_trial(client, DEVICE)
    assert tools == ['take_screenshot', 'get_device_info']
    assert result['status'] == 'failed' and result['tap_error_code'] == 'stale_observation'
    assert result['read_only_evidence_before_manual_tap']['matches_current_display'] is True
    assert DEVICE not in json.dumps(result) and 'private-observation' not in json.dumps(result)
