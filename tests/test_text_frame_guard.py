"""Exercise the physical dispatch boundary with real images and observation refs.

Only the external OCR and Apple device services are controlled doubles. Router,
snapshot binding, image comparison and the decision to transmit remain real.
"""
import copy
import asyncio
import hashlib
import io
import time

from PIL import Image, ImageDraw, ImageFont
import pytest

from agent.backends import MacBackend
from agent.core import Router
from agent.native_physical import classify_sensitivity
from Contracts.catalog import CATALOG
from test_router import allow


LOGICAL = {'width': 440, 'height': 956}
DEVICE = 'ios:text-fixture'


def frame(*, caret=False, signal=False, clock='6:25', target='Enter name',
          shift=0, context='TapMesh test device', mark=None, mark_color='white'):
    im = Image.new('RGB', (440, 956), '#101010')
    draw = ImageDraw.Draw(im)
    font = ImageFont.load_default(size=20)
    rows = []
    for label, x, y in [(clock, 50, 20), (context, 30, 80),
                         (target, 30 + shift, 150), ('Entered:', 30, 210),
                         ('Existing value', 30, 270)]:
        box = draw.textbbox((x, y), label, font=font)
        draw.text((x, y), label, fill='white', font=font)
        rows.append({'label': label, 'value': None, 'role': 'text', 'source': 'ocr',
                     'confidence': .99, 'bounds': dict(zip(('x', 'y', 'width', 'height'),
                        (box[0], box[1], box[2]-box[0], box[3]-box[1])))})
    box = rows[2]['bounds']
    if caret:
        x = box['x'] + box['width'] + 3
        draw.rectangle((x, box['y']-3, x+2, box['y']+box['height']+3), fill='#0a84ff')
    if signal:
        draw.rectangle((330, 26, 333, 39), fill='white')
    if mark:
        draw.rectangle(mark, fill=mark_color)
    out = io.BytesIO()
    im.save(out, format='PNG')
    return out.getvalue(), rows


class AppleSurface:
    def __init__(self, backend):
        self.backend = backend
        self.current = frame()
        self.recognitions = {}
        self.remember(self.current)
        self.transmissions = []
        self.after_media = None
        self.labels = ['TapMesh test device', 'Enter name']
        self.device = {'device_id': DEVICE, 'native_id': 'fixture', 'backend': 'physical',
                       'ready': True, 'connection_epoch': 1,
                       'capabilities': {t['capability']: {'status': 'supported'} for t in CATALOG.values()}}
        backend.native_serials[DEVICE] = 'bound'
        backend._dev = backend._dev_serialized = self.dev
        backend.geometry = self.geometry
        backend._raw_screenshot = self.screenshot
        backend.discover = self.discover
        backend.native_physical.sensitive_state = self.sensitivity
        backend.native_physical.input = self.transmit

    def remember(self, value, *, truncated=False):
        self.recognitions[hashlib.sha256(value[0]).hexdigest()] = {
            'elements': copy.deepcopy(value[1]), 'source': 'ocr',
            'logical_size': dict(LOGICAL), 'backend_truncated': truncated}

    def show(self, **kwargs):
        self.current = frame(**kwargs)
        self.remember(self.current)

    async def discover(self, include_all=False):
        return [self.device]

    async def dev(self, args, *unused, **kwargs):
        if args == ['device', 'info', 'displays']:
            return {'displays': [{'logicalSize': dict(LOGICAL), 'orientation': 'portrait'}]}
        return {'passcodeRequired': False}

    async def geometry(self, device):
        return {'logical_size': dict(LOGICAL), 'orientation': 'portrait', 'density': 1}

    async def screenshot(self, device, **kwargs):
        return self.current[0]

    async def sensitivity(self, serial, locked=None):
        return classify_sensitivity(locked, self.labels)

    async def transmit(self, tool, serial, geometry, targets, args, *, guard=None,
                       on_send=None, text_context=None):
        if self.after_media:
            self.after_media()
        if guard:
            await guard(None)
        if on_send:
            on_send()
        self.transmissions.append((tool, targets['target'][:2], args['text']))
        return {'delivery': 'sent', 'verified': False}


class OCRService:
    def __init__(self, surface):
        self.surface = surface

    async def available(self):
        return True

    async def close(self):
        pass

    async def inspect(self, backend, device, context):
        shot = await backend.invoke('take_screenshot', device, {'format': 'png'}, context)
        result = copy.deepcopy(self.surface.recognitions[hashlib.sha256(self.surface.current[0]).hexdigest()])
        return {**result, 'orientation': 'portrait', '_frame_pixel_sha256': shot['frame_pixel_sha256'],
                '_captured_monotonic': time.monotonic()}

    async def inspect_frame(self, content, logical):
        assert logical == LOGICAL
        return copy.deepcopy(self.surface.recognitions[hashlib.sha256(content).hexdigest()])


@pytest.fixture
async def physical(tmp_path):
    backend = MacBackend(tmp_path/'backend')
    surface = AppleSurface(backend)
    router = Router(backend, state_dir=tmp_path/'router')
    router.ocr = OCRService(surface)
    try:
        yield router, surface
    finally:
        await router.close()


async def observed_target(router, surface, kind='ref'):
    tool = 'take_screenshot' if kind == 'screenshot' else 'list_elements'
    args = {'device_id': DEVICE, **({'source': 'ocr'} if tool == 'list_elements' else {'format': 'png'})}
    value = await router.call('field-test', tool, args)
    assert value['status'] == 'success'
    data = value['data']
    box = surface.current[1][2]['bounds']
    point = (box['x'] + box['width']/2, box['y'] + box['height']/2)
    if kind == 'ref':
        selected = next(e for e in data['elements'] if e['label'] == 'Enter name')
        target = {'ref': selected['ref'], 'snapshot_id': data['snapshot_id']}
    else:
        target = {'x': point[0], 'y': point[1], 'coordinate_space': 'device_points',
                  'observation_id': data['observation_id']}
    return target, point


async def fill(router, target, **extra):
    return await allow(router, 'field-test', 'fill', {'device_id': DEVICE, 'target': target,
        'text': 'MeshGood', 'input_method': 'keyboard', 'keyboard_layout': 'us',
        'field_context': 'ordinary_app', 'frame_validation': 'text_target', **extra})


@pytest.mark.parametrize('kind', ['ref', 'point', 'screenshot'])
async def test_stable_field_accepts_standard_caret_and_status_changes(physical, kind):
    router, surface = physical
    target, point = await observed_target(router, surface, kind)
    surface.show(caret=True, signal=True, clock='6:26')
    result = await fill(router, target)
    assert result['status'] == 'success', result.get('error')
    assert surface.transmissions == [('fill', point, 'MeshGood')]
    assert result['data']['verified'] is False


async def test_target_ocr_box_jitter_requires_unchanged_glyph_pixels(physical):
    router, surface = physical
    target, point = await observed_target(router, surface)
    surface.show(signal=True)
    box = surface.current[1][2]['bounds']
    box['y'] -= 4.4
    box['height'] += 4.4
    surface.remember(surface.current)
    result = await fill(router, target)
    assert result['status'] == 'success', result.get('error')
    assert surface.transmissions == [('fill', point, 'MeshGood')]


async def test_status_indicator_redraw_has_a_bounded_independent_pixel_budget(physical):
    router, surface = physical
    target, point = await observed_target(router, surface)
    surface.show(clock='6:26', mark=(300, 25, 339, 40))
    result = await fill(router, target)
    assert result['status'] == 'success', result.get('error')
    assert surface.transmissions == [('fill', point, 'MeshGood')]


async def test_ocr_box_may_include_the_standard_blue_caret_at_its_edge(physical):
    router, surface = physical
    surface.current[1][2]['bounds']['width'] += 6
    surface.remember(surface.current)
    target, point = await observed_target(router, surface)
    surface.show(caret=True, signal=True)
    surface.current[1][2]['bounds']['width'] += 6
    surface.remember(surface.current)
    result = await fill(router, target)
    assert result['status'] == 'success', result.get('error')
    assert surface.transmissions == [('fill', point, 'MeshGood')]


@pytest.mark.parametrize('change', ['target_text', 'target_position', 'context_text',
                                   'unexplained_pixel', 'target_glyph', 'large_change',
                                   'low_confidence', 'protected', 'missing_AX', 'truncated', 'middle_status', 'blue_inside_target'])
async def test_changed_or_unprovable_field_never_transmits(physical, change):
    router, surface = physical
    target, _ = await observed_target(router, surface)
    surface.show(caret=True, signal=True)
    if change == 'target_text':
        surface.show(target='Enter code', signal=True)
    elif change == 'target_position':
        surface.show(shift=6, signal=True)
    elif change == 'context_text':
        surface.show(context='Sign in to continue', signal=True)
    elif change == 'unexplained_pixel':
        surface.show(mark=(400, 500, 400, 500), signal=True)
    elif change == 'target_glyph':
        b = surface.current[1][2]['bounds']
        surface.show(mark=(b['x']+2, b['y']+2, b['x']+2, b['y']+4), mark_color='red', signal=True)
    elif change == 'blue_inside_target':
        b = surface.current[1][2]['bounds']
        x = b['x'] + b['width']/2
        surface.show(mark=(x, b['y']-2, x+2, b['y']+b['height']+2), mark_color='#0a84ff', signal=True)
    elif change == 'large_change':
        surface.show(mark=(10, 20, 430, 70), signal=True)
    elif change == 'low_confidence':
        surface.current[1][2]['confidence'] = .4
        surface.remember(surface.current)
    elif change == 'protected':
        surface.labels = ['Password, secure text field']
    elif change == 'missing_AX':
        surface.labels = []
    elif change == 'middle_status':
        surface.show(mark=(220, 30, 220, 32), signal=True)
    else:
        surface.remember(surface.current, truncated=True)
    result = await fill(router, target)
    assert result['status'] == 'error'
    assert result['error']['side_effect_state'] == 'not_sent'
    assert surface.transmissions == []


async def test_new_modal_after_media_setup_is_rejected_before_text(physical):
    router, surface = physical
    target, _ = await observed_target(router, surface)
    surface.show(caret=True, signal=True)
    surface.after_media = lambda: surface.show(context='Confirmation required', signal=True)
    result = await fill(router, target)
    assert result['error']['code'] == 'stale_observation'
    assert result['error']['side_effect_state'] == 'not_sent'
    assert surface.transmissions == []


async def test_submit_keeps_strict_frame_requirement(physical):
    router, surface = physical
    target, _ = await observed_target(router, surface)
    surface.show(caret=True, signal=True)
    result = await fill(router, target, submit=True, frame_validation='exact')
    assert result['error']['code'] == 'stale_observation'
    assert surface.transmissions == []


async def test_clock_merged_with_mute_symbol_keeps_safe_status_band(physical):
    router, surface = physical
    surface.show(clock='6:25\\')
    target, point = await observed_target(router, surface)
    surface.show(clock='6:26\\', caret=True, signal=True)
    result = await fill(router, target)
    assert result['status'] == 'success', result.get('error')
    assert surface.transmissions == [('fill', point, 'MeshGood')]


async def test_ocr_variation_cannot_reject_identical_non_target_pixels(physical):
    router, surface = physical
    target, point = await observed_target(router, surface)
    surface.show(caret=True, signal=True)
    surface.current[1][3]['label'] = 'Entered'
    surface.remember(surface.current)
    result = await fill(router, target)
    assert result['status'] == 'success', result.get('error')
    assert surface.transmissions == [('fill', point, 'MeshGood')]


async def test_status_icons_misread_as_text_do_not_change_field_identity(physical):
    router, surface = physical
    target, point = await observed_target(router, surface)
    surface.show(caret=True, signal=True)
    surface.current[1].append({'label': '\u30df\u30c4\u30fc', 'role': 'text', 'source': 'ocr',
        'confidence': 1, 'bounds': {'x': 320, 'y': 23, 'width': 35, 'height': 20}})
    surface.remember(surface.current)
    result = await fill(router, target)
    assert result['status'] == 'success', result.get('error')
    assert surface.transmissions == [('fill', point, 'MeshGood')]


async def test_default_fill_keeps_exact_frame_contract(physical):
    router, surface = physical
    target, _ = await observed_target(router, surface)
    surface.show(caret=True, signal=True)
    result = await allow(router, 'field-test', 'fill', {'device_id': DEVICE, 'target': target,
        'text': 'MeshGood', 'keyboard_layout': 'us', 'field_context': 'ordinary_app'})
    assert result['status'] == 'error'
    assert result['error']['code'] == 'stale_observation'
    assert surface.transmissions == []


def test_anchor_budget_evicts_old_images_and_scopes_access():
    from agent.text_frame_guard import FrameAnchors
    now = [0.0]
    anchors = FrameAnchors(max_bytes=8, max_entries=2, max_frame=8, clock=lambda: now[0])
    anchors.put('session:a', 'phone', 1, 'one', b'1234')
    anchors.put('session:a', 'phone', 1, 'two', b'5678')
    assert anchors.get('session:b', 'phone', 1, 'one') is None
    assert anchors.get('session:a', 'other', 1, 'one') is None
    assert anchors.get('session:a', 'phone', 2, 'one') is None
    anchors.put('session:a', 'phone', 1, 'three', b'abcd')
    assert anchors.get('session:a', 'phone', 1, 'one') is None
    assert anchors.get('session:a', 'phone', 1, 'two') == b'5678'
    assert anchors.get('session:a', 'phone', 1, 'three') == b'abcd'
    now[0] = 31
    assert anchors.get('session:a', 'phone', 1, 'three') is None


def test_oversized_frame_cannot_evict_valid_evidence():
    from agent.text_frame_guard import FrameAnchors
    anchors = FrameAnchors(max_bytes=8, max_frame=4)
    anchors.put('session:a', 'phone', 1, 'one', b'1234')
    anchors.put('session:a', 'phone', 1, 'huge', b'123456789')
    assert anchors.get('session:a', 'phone', 1, 'one') == b'1234'
    assert anchors.get('session:a', 'phone', 1, 'huge') is None
    anchors.clear_device('phone')
    assert anchors.get('session:a', 'phone', 1, 'one') is None


async def test_expired_images_leave_memory_without_another_request():
    from agent.text_frame_guard import FrameAnchors
    anchors = FrameAnchors(max_age=.01)
    try:
        anchors.put('session:a', 'phone', 1, 'one', b'1234')
        await asyncio.sleep(.03)
        assert anchors.size == 0
    finally:
        anchors.clear()
