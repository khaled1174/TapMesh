"""Deterministic review regressions; all devices and transports are synthetic."""
import asyncio

import pytest

from agent.backends import MacBackend
from agent.core import Router
from agent.safety import BridgeError
from test_router import DeviceFixture


@pytest.mark.parametrize('after', ['missing', 'not_ready', 'new_epoch'])
async def test_cached_discovery_cannot_hide_a_post_input_connection_change(tmp_path, after):
    backend = MacBackend(tmp_path / 'backend')
    fixture = DeviceFixture()
    sent = False
    discoveries = 0

    async def discover(include_all=False):
        nonlocal discoveries
        discoveries += 1
        devices = await fixture.discover(include_all)
        if sent:
            if after == 'missing': return []
            if after == 'not_ready': devices[0]['ready'] = False
            if after == 'new_epoch': devices[0]['connection_epoch'] = 2
        return devices

    async def invoke(tool, device, args, context):
        nonlocal sent
        fixture.calls.append((tool, args))
        sent = True
        return {'delivery': 'sent'}

    backend._discover = discover
    backend.invoke = invoke
    router = Router(backend, state_dir=tmp_path / 'router', fast_control=True)
    try:
        # The real MacBackend cache is warm for the entire synthetic input.
        await backend.discover(True)
        result = await router.call('agent', 'press_button', {'device_id': 'sim:fixture', 'button': 'home'})
        assert result['status'] == 'error', result
        assert result['error']['code'] == 'delivery_outcome_unknown'
        assert result['error']['side_effect_state'] == 'unknown'
        assert len(fixture.calls) == 1
        assert discoveries >= 2
    finally:
        await router.close()


@pytest.mark.parametrize('stage', ['discovery', 'subscription'])
async def test_update_preparation_cannot_admit_a_pending_live_view(tmp_path, stage):
    entered, release = asyncio.Event(), asyncio.Event()

    class View:
        closed = False
        def close(self, reason): self.closed = True

    class Backend(DeviceFixture):
        views = []
        async def discover(self, include_all=False):
            if stage == 'discovery':
                entered.set()
                await release.wait()
            return await super().discover(include_all)
        async def live(self, device):
            if stage == 'subscription':
                entered.set()
                await release.wait()
            view = View()
            self.views.append(view)
            return view

    backend = Backend()
    router = Router(backend, state_dir=tmp_path)
    task = asyncio.create_task(router.live('viewer', 'sim:fixture'))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert router.prepare_update('lease')['status'] == 'prepared'
        # Even resume must not admit an old, interrupted handshake.
        router.resume_after_update('lease')
        release.set()
        with pytest.raises(BridgeError) as error:
            await task
        assert error.value.code == 'overloaded'
        assert all(view.closed for view in backend.views)
        assert not router.live_views
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await router.close()


async def test_stop_preserves_an_already_running_recording_finalization(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()

    class Backend(DeviceFixture):
        def __init__(self):
            super().__init__()
            self.recordings = {'recording': {'session_id': 'agent', 'device_id': 'sim:fixture'}}
        async def invoke(self, tool, device, args, context):
            assert tool == 'stop_recording'
            entered.set()
            await release.wait()
            self.recordings['recording']['final'] = True
            return {'recording_id': 'recording', 'completed': True}

    backend = Backend()
    router = Router(backend, state_dir=tmp_path, fast_control=True)
    router.access.update(owner_session='owner')
    task = asyncio.create_task(router.call('agent', 'stop_recording', {'device_id': 'sim:fixture', 'recording_id': 'recording'}))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await router.hold('sim:fixture', True)
        assert not task.done()
        release.set()
        assert (await task)['status'] == 'success'
        assert backend.recordings['recording']['final']
        assert not router.recording_locks
    finally:
        release.set()
        await task
        await router.close()
