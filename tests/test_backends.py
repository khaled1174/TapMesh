import pytest


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
