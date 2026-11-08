"""Physical text preflight must not repeatedly scroll an unchanged view."""
import io
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from PIL import Image

from agent.backends import MacBackend
from agent.native_physical import NativePhysical, classify_sensitivity, frame_hash
from agent.safety import BridgeError


class Surface:
    def __init__(self, backend):
        self.backend = backend
        self.color = 'red'
        self.locked = False
        self.audits = 0
        self.sent = 0
        self.fail_audit = False
        self.after_setup = None
        self.device = {'device_id': 'ios:fixture', 'native_id': 'fixture',
                       'backend': 'physical', 'connection_epoch': 1, 'ready': True}
        backend.native_serials['ios:fixture'] = 'bound'
        backend._dev = self.dev
        backend._dev_serialized = self.dev
        backend.geometry = self.geometry
        backend._raw_screenshot = self.screenshot
        backend.native_physical.sensitive_state = self.audit
        backend.native_physical.input = self.input

    async def dev(self, *args, **kwargs):
        if args[0] == ['device', 'info', 'displays']:
            return {'displays': [{'logicalSize': {'width': 440, 'height': 956}, 'orientation': 'portrait'}]}
        return {'passcodeRequired': self.locked}

    async def geometry(self, device):
        return {'logical_size': {'width': 440, 'height': 956}, 'orientation': 'portrait'}

    async def screenshot(self, device, **kwargs):
        out = io.BytesIO()
        Image.new('RGB', (4, 4), self.color).save(out, format='PNG')
        return out.getvalue()

    async def audit(self, serial, locked=None):
        self.audits += 1
        if self.fail_audit:
            raise BridgeError('prerequisite_required', 'Audit unavailable')
        # Public AX focus traversal can change the viewport before returning.
        self.color = 'blue'
        return classify_sensitivity(locked, ['TapMesh test device', 'Enter name'])

    async def input(self, *args, on_send=None, **kwargs):
        if self.after_setup:
            self.after_setup()
        if kwargs.get('guard'):
            await kwargs['guard'](None)
        self.sent += 1
        if on_send:
            on_send()
        return {'delivery': 'sent', 'verified': False}

    async def context(self, advisory):
        return {'sensitive_state': advisory, 'targets': {
            'target': (20, 30, {'public': {
                'frame_pixel_sha256': frame_hash(await self.screenshot(self.device))}})}}


async def test_rejected_old_view_then_fresh_fill_reuses_exact_prepared_frame(tmp_path):
    backend = MacBackend(tmp_path)
    surface = Surface(backend)
    try:
        old = await surface.context(None)
        old['sensitive_state'] = await backend.sensitive_state(surface.device)
        with pytest.raises(BridgeError) as error:
            await backend._native_input('fill', surface.device, {'text': 'first'}, old)
        assert error.value.code == 'stale_observation'
        assert error.value.side_effect_state == 'not_sent'
        assert surface.sent == 0

        fresh = await surface.context(None)
        fresh['sensitive_state'] = await backend.sensitive_state(surface.device)
        assert surface.audits == 1  # Repeating AX would disturb the inspected view.
        result = await backend._native_input('fill', surface.device, {'text': 'second'}, fresh)
        assert result['delivery'] == 'sent'
        assert surface.sent == 1
    finally:
        await backend.close()


@pytest.mark.parametrize('change', ['pixels', 'epoch', 'serial', 'expiry'])
async def test_prepared_advisory_is_discarded_when_binding_changes(tmp_path, monkeypatch, change):
    backend = MacBackend(tmp_path)
    surface = Surface(backend)
    clock = [100.0]
    monkeypatch.setattr('agent.backends.time', SimpleNamespace(monotonic=lambda: clock[0]))
    try:
        await backend.sensitive_state(surface.device)
        await backend.sensitive_state(surface.device)
        assert surface.audits == 1
        if change == 'pixels':
            surface.color = 'green'
        elif change == 'epoch':
            surface.device['connection_epoch'] += 1
        elif change == 'serial':
            backend.native_serials['ios:fixture'] = 'new-binding'
        else:
            clock[0] += 11
        await backend.sensitive_state(surface.device)
        assert surface.audits == 2
    finally:
        await backend.close()


@pytest.mark.parametrize('locked,expected', [(True, 'protected'), (None, 'unknown')])
async def test_prepared_clear_view_never_overrides_fresh_lock_state(tmp_path, locked, expected):
    backend = MacBackend(tmp_path)
    surface = Surface(backend)
    try:
        assert (await backend.sensitive_state(surface.device))['state'] == 'clear'
        surface.locked = locked
        assert (await backend.sensitive_state(surface.device))['state'] == expected
        assert surface.sent == 0
    finally:
        await backend.close()


async def test_first_actual_input_invalidates_prepared_advisory(tmp_path):
    backend = MacBackend(tmp_path)
    surface = Surface(backend)
    try:
        advisory = await backend.sensitive_state(surface.device)
        await backend.sensitive_state(surface.device)
        assert surface.audits == 1
        await backend._native_input('type_text', surface.device, {'text': 'test'},
                                    await surface.context(advisory))
        assert surface.sent == 1
        await backend.sensitive_state(surface.device)
        assert surface.audits == 2
    finally:
        await backend.close()


async def test_app_lifecycle_invalidates_prepared_advisory(tmp_path):
    backend = MacBackend(tmp_path)
    surface = Surface(backend)
    try:
        await backend.sensitive_state(surface.device)
        await backend.sensitive_state(surface.device)
        assert surface.audits == 1
        await backend.invoke('launch_app', surface.device,
                             {'bundle_id': 'io.tapmesh.fixture'}, {'router': object()})
        await backend.sensitive_state(surface.device)
        assert surface.audits == 2
    finally:
        await backend.close()


async def test_failed_reinspection_cannot_restore_previous_clear_advisory(tmp_path):
    backend = MacBackend(tmp_path)
    surface = Surface(backend)
    try:
        await backend.sensitive_state(surface.device)
        surface.color = 'green'
        surface.fail_audit = True
        with pytest.raises(BridgeError):
            await backend.sensitive_state(surface.device)
        surface.color = 'blue'
        surface.fail_audit = False
        await backend.sensitive_state(surface.device)
        assert surface.audits == 3
    finally:
        await backend.close()


async def test_callers_cannot_mutate_prepared_advisory(tmp_path):
    backend = MacBackend(tmp_path)
    surface = Surface(backend)
    try:
        first = await backend.sensitive_state(surface.device)
        first['state'] = 'protected'
        assert (await backend.sensitive_state(surface.device))['state'] == 'clear'
        assert surface.audits == 1
    finally:
        await backend.close()


async def test_explicit_focused_keyboard_input_does_not_require_a_coordinate_anchor(tmp_path):
    backend = MacBackend(tmp_path)
    surface = Surface(backend)
    try:
        advisory = await backend.sensitive_state(surface.device)
        surface.color = 'green'
        result = await backend._native_input('type_text', surface.device, {'text': 'test'},
                                             {'sensitive_state': advisory, 'targets': {}})
        assert result['delivery'] == 'sent' and surface.sent == 1
    finally:
        await backend.close()


@pytest.mark.parametrize('change,code', [('pixels', 'stale_observation'),
                                       ('locked', 'protected_action_requires_human'),
                                       ('unknown_lock', 'prerequisite_required'),
                                       ('expired', 'prerequisite_required'),
                                       ('old_target', 'stale_observation')])
async def test_backend_rechecks_after_setup_and_never_sends_changed_context(tmp_path, monkeypatch, change, code):
    backend = MacBackend(tmp_path)
    surface = Surface(backend)
    clock = [100.0]
    monkeypatch.setattr('agent.backends.time', SimpleNamespace(monotonic=lambda: clock[0]))
    try:
        advisory = await backend.sensitive_state(surface.device)
        context = await surface.context(advisory)
        if change == 'pixels':
            surface.after_setup = lambda: setattr(surface, 'color', 'green')
        elif change in {'locked', 'unknown_lock'}:
            surface.after_setup = lambda: setattr(surface, 'locked', True if change == 'locked' else None)
        elif change == 'expired':
            surface.after_setup = lambda: clock.__setitem__(0, 111.0)
        else:
            context['targets']['target'][2]['at'] = 69.0
        with pytest.raises(BridgeError) as error:
            await backend._native_input('fill', surface.device, {'text': 'test'}, context)
        assert error.value.code == code
        assert error.value.side_effect_state == 'not_sent'
        assert surface.sent == 0 and context['sent'] is False
        assert not backend.physical_text_preflights
    finally:
        await backend.close()


async def test_native_guard_runs_after_media_setup_before_any_contact():
    media_started = []
    contacts = []

    @asynccontextmanager
    async def tunnel(serial):
        yield SimpleNamespace(udid=serial)

    class HID:
        async def send_multitouch(self, values):
            contacts.extend(values)

    @asynccontextmanager
    async def media(rsd):
        media_started.append(True)
        yield HID()

    async def guard(rsd):
        if media_started:
            raise BridgeError('stale_observation', 'View changed during media authentication')

    adapter = NativePhysical(tunnel_factory=tunnel, media_factory=media)
    with pytest.raises(BridgeError) as error:
        await adapter.input('tap', 'bound',
                            {'logical_size': {'width': 440, 'height': 956}, 'orientation': 'portrait'},
                            {'target': (20, 30, None)}, {}, guard=guard)
    assert error.value.code == 'stale_observation'
    assert error.value.side_effect_state == 'not_sent'
    assert contacts == []
