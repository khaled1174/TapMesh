import pytest


@pytest.mark.parametrize(('native_state','cli_reply','expected_error','lock_sources'), [
    (False, {'passcodeRequired': True}, None, ['native']),
    (True, {'passcodeRequired': False}, 'protected_action_requires_human', ['native']),
    (None, {'passcodeRequired': False}, None, ['native', 'cli']),
    (0, {'passcodeRequired': False}, None, ['native', 'cli']),
    (None, {'passcodeRequired': 'false'}, 'prerequisite_required', ['native', 'cli']),
    (None, {'passcodeRequired': 0}, 'prerequisite_required', ['native', 'cli']),
    (None, [], 'prerequisite_required', ['native', 'cli']),
])
async def test_swipe_checks_lock_once_immediately_before_input(tmp_path, native_state, cli_reply, expected_error, lock_sources):
    from agent.backends import MacBackend
    from agent.safety import BridgeError

    backend = MacBackend(tmp_path)
    device = {'device_id': 'ios:fixture', 'native_id': 'fixture', 'backend': 'physical', 'connection_epoch': 1}
    backend.native_serials[device['device_id']] = 'paired-usb'
    calls = []

    async def native_lock_state(serial):
        assert serial == 'paired-usb'
        calls.append('native')
        return native_state

    async def cli(args, confirmed_device, **kwargs):
        assert args == ['device', 'info', 'lockState'] and confirmed_device == device
        calls.append('cli')
        return cli_reply

    async def geometry(confirmed_device):
        calls.append('geometry')
        return {'orientation': 'portrait', 'logical_size': {'width': 100, 'height': 200}}

    async def input(tool, serial, geometry, targets, arguments, *, guard, on_send, still_unlocked):
        assert tool == 'swipe' and serial == 'paired-usb' and guard is None
        calls.append('hid_ready')
        if not await still_unlocked():
            raise BridgeError('protected_action_requires_human', 'Device locked')
        on_send()
        calls.append('first_hid_report')
        return {'delivery': 'sent'}

    backend.native_physical.lock_state = native_lock_state
    backend.native_physical.input = input
    backend.geometry = geometry
    backend._dev = cli
    context = {'sent': True}
    try:
        if expected_error:
            with pytest.raises(BridgeError) as error:
                await backend._native_input('swipe', device, {}, context)
            assert (error.value.code, error.value.side_effect_state) == (expected_error, 'not_sent')
            assert calls == ['geometry', 'hid_ready', *lock_sources]
            assert context['sent'] is False and not backend.lock_states
        else:
            assert await backend._native_input('swipe', device, {}, context) == {'delivery': 'sent'}
            assert calls == ['geometry', 'hid_ready', *lock_sources, 'first_hid_report']
            assert context['sent'] is True
    finally:
        await backend.close()


@pytest.mark.parametrize(('next_epoch','next_serial'), [(2, 'paired-usb'), (1, 'replacement-usb')])
async def test_old_lock_reply_cannot_authorize_or_replace_a_rebound_device_state(tmp_path, next_epoch, next_serial):
    import asyncio
    from agent.backends import MacBackend
    from agent.safety import BridgeError

    backend = MacBackend(tmp_path)
    device = {'device_id': 'ios:fixture', 'native_id': 'fixture', 'backend': 'physical', 'connection_epoch': 1}
    did = device['device_id']
    backend.native_serials[did] = 'paired-usb'
    backend.native_epochs[did] = 1
    started, finish = asyncio.Event(), asyncio.Event()

    async def lock_state(serial):
        started.set()
        await finish.wait()
        return False

    async def unexpected_fallback(*args, **kwargs):
        raise AssertionError('A superseded read must terminate before trying a different transport')

    backend.native_physical.lock_state = lock_state
    backend._dev = unexpected_fallback
    read = asyncio.create_task(backend._read_lock_state(device, 'paired-usb'))
    try:
        await asyncio.wait_for(started.wait(), 1)
        backend.native_epochs[did] = next_epoch
        backend.native_serials[did] = next_serial
        replacement = (next_epoch, 123, next_serial)
        backend.lock_states[did] = replacement
        finish.set()
        with pytest.raises(BridgeError) as error:
            await read
        assert error.value.code == 'connection_changed'
        assert backend.lock_states[did] == replacement
    finally:
        finish.set()
        await asyncio.gather(read, return_exceptions=True)
        await backend.close()


@pytest.mark.parametrize('disconnected', [False, True])
async def test_native_swipe_rejects_changes_after_router_validation(tmp_path, disconnected):
    from agent.backends import MacBackend
    from agent.safety import BridgeError

    backend = MacBackend(tmp_path)
    device = {'device_id': 'ios:fixture', 'native_id': 'fixture', 'backend': 'physical', 'connection_epoch': 1}
    backend.native_serials[device['device_id']] = 'paired-usb'
    if disconnected:
        backend.identities[device['device_id']] = ('absent', 2)
    calls = []

    async def geometry(_):
        calls.append('geometry')
        return {'orientation': 'landscapeLeft', 'logical_size': {'width': 200, 'height': 100}}

    async def unexpected(*args, **kwargs):
        raise AssertionError('Rotation must be detected before dispatch')

    backend.geometry = geometry
    backend.native_physical.input = unexpected
    backend.native_physical.lock_state = unexpected
    backend._dev = unexpected
    record = {'public': {'orientation': 'portrait', 'logical_size': {'width': 100, 'height': 200}}}
    context = {'targets': {'start': (10, 20, record), 'end': (80, 20, record)}, 'sent': True}
    try:
        with pytest.raises(BridgeError) as error:
            await backend._native_input('swipe', device, {}, context)
        expected = 'connection_changed' if disconnected else 'stale_observation'
        assert (error.value.code, error.value.side_effect_state) == (expected, 'not_sent')
        assert calls == ([] if disconnected else ['geometry']) and context['sent'] is False
    finally:
        await backend.close()


@pytest.mark.parametrize('display_state', ['aligned', 'rotated', 'invalidated'])
async def test_physical_swipe_reads_geometry_once_at_dispatch(tmp_path, display_state):
    from agent.backends import MacBackend
    from agent.core import Router

    backend = MacBackend(tmp_path / 'backend')
    device = {
        'device_id': 'ios:fixture', 'native_id': 'fixture', 'connection_epoch': 1,
        'ready': True, 'backend': 'physical', 'capabilities': {'input.swipe': {'status': 'supported'}},
    }
    device_id = device['device_id']
    backend.native_serials[device_id] = 'paired-usb'
    router = Router(backend, state_dir=tmp_path / 'router', fast_control=True)
    router.access.allow_device(device_id)
    dimensions = {'width': 100, 'height': 200}
    obs = router.observations.issue('client', device_id, 1, dimensions, None, 'fixture', orientation='portrait')
    calls = []

    async def discover(include_all=False):
        return [device]

    async def geometry(confirmed_device):
        assert confirmed_device['device_id'] == device_id
        calls.append('geometry')
        if display_state == 'rotated':
            return {'logical_size': {'width': 200, 'height': 100}, 'orientation': 'landscapeLeft'}
        if display_state == 'invalidated':
            router.observations.invalidate(device_id)
        return {'logical_size': dimensions, 'orientation': 'portrait'}

    async def lock_state(serial):
        calls.append('lock')
        return False

    async def input(tool, serial, geometry, targets, arguments, *, guard, on_send, still_unlocked):
        assert await still_unlocked()
        calls.append('sent')
        on_send()
        return {'delivery': 'sent'}

    backend._discover = discover
    backend.geometry = geometry
    backend.native_physical.lock_state = lock_state
    backend.native_physical.input = input
    target = {'coordinate_space': 'device_points', 'y': 100, 'observation_id': obs['observation_id']}
    try:
        result = await router.call('client', 'swipe', {
            'device_id': device_id, 'start': {**target, 'x': 75}, 'end': {**target, 'x': 25},
        })
        if display_state == 'aligned':
            assert result['status'] == 'success'
            assert calls == ['geometry', 'lock', 'sent']
        else:
            assert result['error']['code'] == 'stale_observation'
            assert result['error']['side_effect_state'] == 'not_sent'
            assert calls == ['geometry']
    finally:
        await backend.close()


@pytest.mark.parametrize('runtime,expected', [
    ('com.apple.CoreSimulator.SimRuntime.iOS-18-2', '18.2'),
    ('com.apple.CoreSimulator.SimRuntime.iOS-26-0', '26.0'),
    ('com.apple.CoreSimulator.SimRuntime.iOS-18-2-1', '18.2.1'),
    ('com.apple.CoreSimulator.SimRuntime.iOS-unavailable', None),
    ('com.apple.CoreSimulator.SimRuntime.tvOS-18-2', None),
    ('weird', None),
])
def test_simulator_os_version(runtime, expected):
    from agent.backends import simulator_os_version
    assert simulator_os_version(runtime) == expected


async def test_discovery_lists_only_ios_simulators(tmp_path):
    import json
    from agent.backends import MacBackend
    from agent.process import Output

    class DeviceRunner:
        async def run(self, argv, **kwargs):
            if argv[:4] == ['xcrun', 'simctl', 'list', 'devices']:
                runtimes = {
                    'iOS-18-2': [{'udid': 'phone', 'name': 'iPhone', 'state': 'Booted'}],
                    'iOS-17-5': [{'udid': 'tablet', 'name': 'iPad', 'state': 'Shutdown'}],
                    'iOS-16-0': [{'udid': 'missing', 'name': 'iPhone', 'state': 'Shutdown', 'isAvailable': False}],
                    'watchOS-11-1': [{'udid': 'watch', 'name': 'Watch', 'state': 'Booted'}],
                    'tvOS-18-2': [{'udid': 'tv', 'name': 'TV', 'state': 'Booted'}],
                    'xrOS-2-0': [{'udid': 'vision', 'name': 'Vision Pro', 'state': 'Shutdown'}],
                }
                devices = {'com.apple.CoreSimulator.SimRuntime.' + family: entries for family, entries in runtimes.items()}
                return Output(json.dumps({'devices': devices}).encode(), b'', 0)
            if argv[:4] == ['xcrun', 'devicectl', 'list', 'devices']:
                return Output(b'{"result":{"devices":[]}}', b'', 0)
            raise AssertionError(f'Unexpected CLI operation: {argv[:5]}')

        async def close(self):
            pass

    backend = MacBackend(tmp_path, runner=DeviceRunner())
    try:
        for include_all, expected in (
            (False, {'sim:phone': ('18.2', None)}),
            (True, {'sim:phone': ('18.2', None), 'sim:tablet': ('17.5', 'simulator_shutdown'), 'sim:missing': ('16.0', 'runtime_missing')}),
        ):
            devices = await backend.discover(include_all)
            assert {device['device_id']: (device['os_version'], device['readiness_code']) for device in devices} == expected
    finally:
        await backend.close()


class PhysicalCapabilityRunner:
    """Public CLI fixture: list metadata is deliberately less complete than details."""
    def __init__(self, capabilities):
        self.details_calls = 0
        self.capabilities = capabilities

    async def run(self, argv, **kwargs):
        import json
        from agent.process import Output
        if argv[:4] == ['xcrun', 'simctl', 'list', 'devices']:
            return Output(b'{"devices":{}}', b'', 0)
        if argv[:4] == ['xcrun', 'devicectl', 'list', 'devices']:
            device = {'identifier': 'owned', 'capabilities': [{'featureIdentifier': 'com.apple.coredevice.feature.getdeviceinfo'}],
                      'properties': {'hardware': {'reality': 'physical', 'platform': 'iOS', 'udid': 'owned-udid'},
                                     'connection': {'pairingState': 'paired', 'state': 'connected'},
                                     'state': {'name': 'Fixture', 'bootState': 'booted'}}}
            return Output(json.dumps({'result': {'devices': [device]}}).encode(), b'', 0)
        if argv[:5] == ['xcrun', 'devicectl', 'device', 'info', 'details']:
            self.details_calls += 1
            return Output(json.dumps({'result': {'capabilities': self.capabilities}}).encode(), b'', 0)
        raise AssertionError('Unexpected CLI operation: ' + str(argv[:5]))

    async def close(self):
        pass


@pytest.mark.parametrize('features,expected', [
    ([{'featureIdentifier': 'com.apple.coredevice.feature.capturescreenshot'}], 'unsupported'),
    ([], 'unsupported'),
    (None, 'degraded'),
    ([{'featureIdentifier': 'com.apple.coredevice.feature.screenrecording'}], 'supported'),
])
async def test_physical_recording_support_comes_from_complete_device_capabilities(tmp_path, features, expected):
    from agent.backends import MacBackend
    backend = MacBackend(tmp_path, runner=PhysicalCapabilityRunner(features))
    try:
        devices = await backend.discover()
        assert devices[0]['capabilities']['screen.record']['status'] == expected
        assert devices[0]['capabilities']['screen.capture']['status'] == 'supported'
    finally:
        await backend.close()


async def test_physical_recording_capability_cache_refreshes_without_querying_every_call(tmp_path, monkeypatch):
    from agent.backends import MacBackend
    now = [100.0]
    monkeypatch.setattr('agent.backends.time.monotonic', lambda: now[0])
    runner = PhysicalCapabilityRunner([])
    backend = MacBackend(tmp_path, runner=runner)
    try:
        assert (await backend.discover())[0]['capabilities']['screen.record']['status'] == 'unsupported'
        runner.capabilities = [{'featureIdentifier': 'com.apple.coredevice.feature.screenrecording'}]
        now[0] += 1
        assert (await backend.discover())[0]['capabilities']['screen.record']['status'] == 'unsupported'
        assert runner.details_calls == 1
        now[0] += 30
        assert (await backend.discover())[0]['capabilities']['screen.record']['status'] == 'supported'
        assert runner.details_calls == 2
    finally:
        await backend.close()


async def test_unsupported_physical_recording_fails_before_storage_or_writer_start(tmp_path):
    from types import SimpleNamespace
    from agent.backends import MacBackend
    from agent.safety import BridgeError
    class NoStorage:
        def check_storage(self, size):
            raise AssertionError('Unsupported recording must not reserve storage or start a writer')
    backend = MacBackend(tmp_path, runner=PhysicalCapabilityRunner([]))
    device = {'backend': 'physical', 'device_id': 'ios:owned', 'native_id': 'owned', 'ready': True, 'connection_epoch': 1}
    try:
        with pytest.raises(BridgeError) as failure:
            await backend.invoke('start_recording', device, {}, {'router': SimpleNamespace(artifacts=NoStorage()), 'session_id': 'qualification'})
        assert failure.value.code == 'unsupported_capability'
        assert failure.value.side_effect_state == 'not_sent'
    finally:
        await backend.close()


async def test_recording_feature_loss_during_approved_request_is_not_sent(tmp_path):
    from agent.backends import MacBackend
    from agent.core import Router
    runner=PhysicalCapabilityRunner([{'featureIdentifier':'com.apple.coredevice.feature.screenrecording'}])
    backend=MacBackend(tmp_path/'backend',runner=runner)
    router=Router(backend,state_dir=tmp_path/'router')
    args={'device_id':'ios:owned'}
    try:
        pending=await router.call('owner','start_recording',args)
        assert pending['error']['code']=='approval_required'
        router.approvals.decide(pending['error']['details']['approval_id'],'allow')
        runner.capabilities=[]
        result=await router.call('owner','start_recording',args)
        assert result['error']['code']=='unsupported_capability'
        assert result['error']['side_effect_state']=='not_sent'
        assert backend.recordings=={}
        assert list(router.artifacts.root.iterdir())==[]
    finally:
        await router.close()


async def test_physical_launch_keeps_backend_options_before_app_argument_separator(tmp_path):
    from agent.backends import MacBackend
    from agent.process import Output
    class CaptureRunner:
        async def run(self, argv, **kwargs):
            delimiter = argv.index('--')
            assert argv[delimiter + 1:] == ['--device', 'literal-app-value']
            backend_options = argv[:delimiter]
            assert backend_options[backend_options.index('--device') + 1] == 'owned-coredevice'
            assert backend_options.index('--device') < backend_options.index('io.tapmesh.fixture')
            assert backend_options.index('--json-output') < backend_options.index('io.tapmesh.fixture')
            assert '--json-output' in backend_options
            assert '--timeout' in backend_options
            return Output(b'{"result":{"process":{"processIdentifier":123}}}', b'', 0)
        async def close(self): pass
    backend = MacBackend(tmp_path, runner=CaptureRunner())
    try:
        result = await backend.invoke('launch_app', {'backend': 'physical', 'native_id': 'owned-coredevice', 'device_id': 'ios:owned-coredevice', 'ready': True},
                                      {'bundle_id': 'io.tapmesh.fixture', 'arguments': ['--device', 'literal-app-value']}, {'router': object()})
        assert result['process_id'] == 123 and result['verified'] is True
    finally:
        await backend.close()


async def test_physical_read_only_inspection_never_walks_accessibility_focus(tmp_path):
    from agent.backends import MacBackend
    from agent.safety import BridgeError
    backend = MacBackend(tmp_path)
    calls = []
    async def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError('Read-only inspection must not move device focus')
    backend.native_physical.audit = forbidden
    backend.geometry = forbidden
    try:
        with pytest.raises(BridgeError) as failed:
            await backend.invoke('list_elements', {'backend': 'physical', 'native_id': 'owned', 'ready': True}, {'source': 'accessibility'}, {'router': object()})
        assert failed.value.code == 'unsupported_capability'
        assert failed.value.side_effect_state == 'not_sent'
        assert calls == []
    finally:
        await backend.close()


async def test_physical_epoch_ignores_lazy_transport_dates_but_tracks_availability(tmp_path):
    from agent.backends import MacBackend
    from agent.process import Output
    backend = MacBackend(tmp_path)
    samples = iter([
        ('disconnected', 1, 'booted'), ('connected', 2, 'booted'),
        ('unavailable', 2, 'booted'), ('disconnected', 3, 'booted'),
        ('disconnected', 3, 'shutdown'),
    ])
    async def sim(*args, **kwargs): return Output(b'{"devices":{}}', b'', 0)
    async def dev(*args, **kwargs):
        if args[0]==['device','info','details']:
            return {'capabilities': []}
        state, date, boot = next(samples)
        return {'devices': [{'identifier':'owned', 'properties': {
            'hardware': {'reality':'physical', 'platform':'iOS', 'udid':'bound'},
            'connection': {'pairingState':'paired', 'state':state, 'lastConnectionDate':date},
            'state': {'name':'Fixture', 'bootState':boot},
        }}]}
    backend._sim = sim
    backend._dev = dev
    backend.discovery_ttl = 0
    try:
        epochs = [(await backend.discover(True))[0]['connection_epoch'] for _ in range(5)]
        assert epochs == [1, 1, 2, 3, 4]
    finally:
        await backend.close()


def test_device_normalization_does_not_leak_pairing_identifiers(tmp_path):
    from agent.backends import normalize_physical
    device=normalize_physical({'identifier':'UUID','properties':{'hardware':{'reality':'physical','platform':'iOS','productType':'iPhone19,7','serialNumber':'SECRET','udid':'SECRET-UDID'},'connection':{'pairingState':'paired','state':'disconnected','lastConnectionDate':1},'state':{'name':'Fixture','bootState':'booted'}}},1)
    assert device['device_id']=='ios:UUID'
    assert 'SECRET' not in str(device)
    assert device['backend']=='physical'


@pytest.mark.parametrize('pairing,developer_mode,boot,connection,code,reason', [
    ('unpaired', 'disabled', 'booting', 'unavailable', 'unpaired', "This device isn't paired with this Mac."),
    ('paired', 'disabled', 'booting', 'unavailable', 'developer_mode_off', 'Developer Mode is off on this device.'),
    ('paired', 'enabled', 'booting', 'unavailable', 'not_booted', "This device hasn't finished starting."),
    ('paired', 'enabled', 'booted', 'unavailable', 'unavailable', "This device isn't reachable right now."),
    ('paired', 'enabled', 'booted', 'disconnected', None, None),
])
def test_physical_device_readiness_follows_first_applicable_issue(pairing, developer_mode, boot, connection, code, reason):
    from agent.backends import normalize_physical

    result = normalize_physical({
        'identifier': 'fixture',
        'deviceProperties': {'developerModeStatus': developer_mode, 'bootState': boot},
        'connectionProperties': {'pairingState': pairing, 'tunnelState': connection},
    }, 1)

    assert result['ready'] is (code is None)
    assert result['readiness_code'] == code
    assert result['readiness_reasons'] == ([reason] if reason else [])


def test_ax_elements_preserve_source_roles_and_no_ocr_fabrication():
    from agent.backends import normalize_ax
    raw=[{'AXLabel':'Save','type':'Button','role':'AXButton','frame':{'x':1,'y':2,'width':3,'height':4},'pid':8,'AXUniqueId':'save'}]
    result=normalize_ax(raw,False)
    assert result[0]['source']=='accessibility'
    assert result[0]['role']=='AXButton'
    assert result[0]['bounds']=={'x':1,'y':2,'width':3,'height':4}


async def test_simulator_create_requires_explicit_creation_fields(tmp_path):
    from agent.backends import MacBackend
    from agent.safety import BridgeError
    backend=MacBackend(tmp_path)
    with pytest.raises(BridgeError):
        await backend.invoke('start_simulator',None,{'name':'not-enough'}, {})
    await backend.close()


def test_physical_explicit_point_scale_derives_qualified_geometry():
    from agent.backends import display_geometry
    assert display_geometry({'displays':[{'primary':True,'bounds':[[0,0],[1320,2868]],'pointScale':3}],'orientation':{'currentDeviceOrientation':'portrait'}})=={'logical_size':{'width':440,'height':956},'orientation':'portrait','density':3}


def test_ax_invisible_filter_removes_elements_outside_display():
    from agent.backends import normalize_ax
    raw=[{'AXLabel':'Offscreen','role':'AXButton','frame':{'x':10,'y':300,'width':30,'height':30}}]
    assert normalize_ax(raw,False,logical_size={'width':100,'height':200})==[]
    assert len(normalize_ax(raw,True,logical_size={'width':100,'height':200}))==1


@pytest.mark.parametrize('state,bounds,expected', [
    ({'currentDeviceOrientation':'faceUp','currentDeviceNonFlatOrientation':'portrait'}, [2752,2064], {'width':1032,'height':1376}),
    ({'currentDeviceOrientation':'landscapeLeft'}, [2064,2752], {'width':1376,'height':1032}),
])
def test_physical_geometry_aligns_framebuffer_axes_with_explicit_orientation(state,bounds,expected):
    from agent.backends import display_geometry
    result=display_geometry({'displays':[{'primary':True,'bounds':[[0,0],bounds],'pointScale':2}],'orientation':state})
    assert result['logical_size']==expected
    assert result['orientation']==state.get('currentDeviceNonFlatOrientation',state['currentDeviceOrientation'])


def test_flat_device_without_nonflat_metadata_never_invents_input_orientation():
    from agent.backends import display_geometry
    result=display_geometry({'displays':[{'bounds':[[0,0],[2752,2064]],'pointScale':2}],'orientation':{'currentDeviceOrientation':'faceUp'}})
    assert result['orientation']=='faceUp'


async def test_unicode_hid_rejection_precedes_focus_or_any_backend_call(tmp_path):
    from agent.backends import MacBackend
    from agent.safety import BridgeError
    backend=MacBackend(tmp_path)
    try:
        with pytest.raises(BridgeError) as failure:
            await backend._input('type_text',{'backend':'simulator','native_id':'fixture'},{'text':'تجربة'},{'targets':{'target':(10,20,None)}})
        assert failure.value.code=='unsupported_capability'
        assert not backend.runner.processes and not backend.companions
    finally:await backend.close()


def test_complete_idb_schema_preserves_canonical_element_identity_and_value():
    from agent.backends import normalize_ax
    result=normalize_ax({'elements':[{'label':'Name','type':'TextField','identifier':'name-field','value':'Hello','frame':{'x':1,'y':2,'width':30,'height':20}}]})
    assert result[0]['label']=='Name'
    assert result[0]['identifier']=='name-field'
    assert result[0]['value']=='Hello'


async def test_discovery_is_shared_briefly_and_refreshed_after_lifecycle_mutations(tmp_path):
    from agent.backends import MacBackend
    from agent.process import Output
    backend = MacBackend(tmp_path)
    calls = []
    async def sim(*args, **kwargs):
        calls.append(args[:2])
        return Output(b'{"devices":{}}', b'', 0)
    async def dev(*args, **kwargs):
        calls.append(tuple(args[0][:2]))
        return {'devices': []}
    backend._sim = sim
    backend._dev = dev
    try:
        for _ in range(3): await backend.discover(True)
        assert len(calls) == 2
        backend.discovery_cache.clear()
        await backend.discover(True)
        assert len(calls) == 4
    finally:
        await backend.close()
